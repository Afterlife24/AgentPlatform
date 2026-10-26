"""Workflow-run billing hooks.

Dograh does not rate or deduct credits locally. MPS owns credit accounting.
For hosted deployments, Dograh reports completed platform usage to MPS.
When a server-minted MPS correlation id exists, MPS uses model-service usage
as the canonical duration. Otherwise Dograh reports the completed run duration.

For OSS deployments, usage is debited from the prepaid credit wallet in USD
using the following formula:
    cost_usd = (
        (prompt_tokens   * 0.15 / 1_000_000) +
        (completion_tokens * 0.60 / 1_000_000) +
        (embedding_tokens  * 0.02 / 1_000_000)
    ) * 1.2

    Prices (per 1M tokens):
        Input  (prompt)     : $0.15
        Output (completion) : $0.60
        Embedding           : $0.02
    1.2x = 20% platform markup

Token counts are read from workflow_run.usage_info. The cost is written as a
negative ledger entry via apply_ledger_entry, keyed run:{workflow_run_id} for
idempotency against task retries.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.enums import CreditLedgerEntryType, PostHogEvent, WorkflowRunMode
from api.services.managed_model_services import get_mps_correlation_id
from api.services.mps_service_key_client import mps_service_key_client
from api.services.posthog_client import capture_event

# Fallback pricing per 1M tokens (USD), used ONLY when a model has no active
# pricing row in the DB (e.g. pricing not yet synced/approved). Keeping a
# fallback means a run is never billed $0 or left uncharged due to a missing
# rate — it falls back to the legacy flat rate and logs a warning.
_FALLBACK_INPUT_PRICE_PER_1M = Decimal("0.15")
_FALLBACK_OUTPUT_PRICE_PER_1M = Decimal("0.60")
_FALLBACK_EMBED_PRICE_PER_1M = Decimal("0.02")
_PLATFORM_MARKUP = Decimal("1.2")
_MILLION = Decimal("1000000")
_SECONDS_PER_HOUR = Decimal("3600")

# Fallback speech rates when a TTS/STT model has no rate row (spec §8.4). Set
# deliberately a touch high so an unmapped provider never silently bills $0 —
# guess in the direction that doesn't lose money. A warning is logged on use.
_FALLBACK_TTS_PER_1M_CHARS = Decimal("50.00")
_FALLBACK_STT_PER_HOUR = Decimal("0.60")

# Fallback speech-to-speech realtime rates (USD per 1M tokens) when a realtime
# model has no rate row. Audio tokens dominate realtime cost, so the audio
# fallbacks are set well above text so an unmapped model never under-charges.
_FALLBACK_RT_TEXT_INPUT_PER_1M = Decimal("5.00")
_FALLBACK_RT_TEXT_OUTPUT_PER_1M = Decimal("20.00")
_FALLBACK_RT_AUDIO_INPUT_PER_1M = Decimal("40.00")
_FALLBACK_RT_AUDIO_OUTPUT_PER_1M = Decimal("80.00")


def _workflow_run_organization_id(workflow_run) -> int | None:
    workflow = getattr(workflow_run, "workflow", None)
    return getattr(workflow, "organization_id", None)


async def _resolve_stt_model_for_run(workflow_run) -> tuple[str | None, str | None]:
    """Read the (provider, model) the run's STT service was configured with.

    Used by the STT billing fallback (spec §5.3): when no per-model STT metric
    was recorded we still need to know which model ran to pick its rate. Read it
    from the effective config rather than guessing; return (None, None) if it
    can't be determined (the formula then uses the fallback STT rate).
    """
    try:
        from api.services.configuration.ai_model_configuration import (
            get_resolved_ai_model_configuration,
        )

        workflow = getattr(workflow_run, "workflow", None)
        org_id = getattr(workflow, "organization_id", None)
        user_id = getattr(workflow, "user_id", None)
        resolved = await get_resolved_ai_model_configuration(
            user_id=user_id, organization_id=org_id
        )
        stt = getattr(resolved.effective, "stt", None)
        if stt is None:
            return None, None
        provider = getattr(stt, "provider", None)
        provider_val = provider.value if hasattr(provider, "value") else provider
        model = getattr(stt, "model", None)
        return (str(provider_val) if provider_val else None, str(model) if model else None)
    except Exception as e:
        logger.warning("Could not resolve STT model for run {}: {}", getattr(workflow_run, "id", "?"), e)
        return None, None


def _duration_seconds_from_usage_info(workflow_run) -> float | None:
    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    duration = usage_info.get("call_duration_seconds")
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        return None

    return duration_seconds if duration_seconds > 0 else None


# Run modes that carry no audio and therefore never use a transcriber. The STT
# call-duration fallback must not fire for these — a text chat has a duration
# but no speech, so billing it for STT would be wrong.
_TEXT_ONLY_RUN_MODES = {
    WorkflowRunMode.TEXTCHAT.value,
    WorkflowRunMode.CHAT.value,  # historical text mode
}


def _run_uses_audio(workflow_run) -> bool:
    """True if the run is a voice/telephony run (uses a transcriber).

    Defaults to True when the mode is unknown so we err toward billing a real
    call rather than silently under-charging; only the known text-only modes
    are excluded.
    """
    mode = getattr(workflow_run, "mode", None)
    return mode not in _TEXT_ONLY_RUN_MODES


def _is_realtime_llm_entry(entry: dict[str, Any]) -> bool:
    """True if an llm usage entry came from a speech-to-speech realtime model.

    Realtime services (OpenAI Realtime, Gemini Live) populate the per-modality
    token split; ordinary text LLMs leave those None. Any non-None audio/text
    modality field marks the entry as realtime.
    """
    return any(
        entry.get(k) is not None
        for k in (
            "prompt_audio_tokens",
            "completion_audio_tokens",
            "prompt_text_tokens",
            "completion_text_tokens",
        )
    )


def _extract_token_counts(usage_info: dict[str, Any]) -> tuple[int, int, int]:
    """Extract prompt, completion, and embedding token counts from usage_info.

    Returns:
        (prompt_tokens, completion_tokens, embedding_tokens)

    usage_info stores per-model LLM token counts under the "llm" key. Realtime
    (speech-to-speech) entries are EXCLUDED here — they're billed separately by
    _extract_realtime_usage so their tokens aren't double-counted as chat.
    """
    prompt_tokens = 0
    completion_tokens = 0

    llm_entries = usage_info.get("llm") or {}
    for entry in llm_entries.values():
        if not isinstance(entry, dict):
            continue
        if _is_realtime_llm_entry(entry):
            continue  # billed as speech-to-speech, not chat
        prompt_tokens += int(entry.get("prompt_tokens") or 0)
        completion_tokens += int(entry.get("completion_tokens") or 0)

    embedding_tokens = int(usage_info.get("embedding_tokens") or 0)

    return prompt_tokens, completion_tokens, embedding_tokens


def _extract_realtime_usage(usage_info: dict[str, Any]) -> dict[str, Any] | None:
    """Sum speech-to-speech realtime token usage from usage_info["llm"].

    Returns a dict with the four token counts + provider/model, or None when no
    realtime model was used. When a realtime model reports only merged totals
    (no modality split for one side), the missing audio field falls back to the
    merged prompt/completion count so the audio rate still applies — realtime
    cost is audio-dominated, so this errs toward not under-charging.
    """
    llm_entries = usage_info.get("llm") or {}
    text_in = audio_in = text_out = audio_out = 0
    provider: str | None = None
    model: str | None = None
    found = False

    for key, entry in llm_entries.items():
        if not isinstance(entry, dict) or not _is_realtime_llm_entry(entry):
            continue
        found = True
        if provider is None:
            provider, model = _split_usage_key(key)
        prompt_total = int(entry.get("prompt_tokens") or 0)
        completion_total = int(entry.get("completion_tokens") or 0)
        p_text = entry.get("prompt_text_tokens")
        p_audio = entry.get("prompt_audio_tokens")
        c_text = entry.get("completion_text_tokens")
        c_audio = entry.get("completion_audio_tokens")
        # Prefer the modality split; fall back so merged tokens bill at the
        # audio rate (the larger realtime rate) rather than vanishing.
        text_in += int(p_text or 0)
        audio_in += int(p_audio) if p_audio is not None else (
            0 if p_text is not None else prompt_total
        )
        text_out += int(c_text or 0)
        audio_out += int(c_audio) if c_audio is not None else (
            0 if c_text is not None else completion_total
        )

    if not found:
        return None
    return {
        "provider": provider,
        "model": model,
        "text_input_tokens": text_in,
        "audio_input_tokens": audio_in,
        "text_output_tokens": text_out,
        "audio_output_tokens": audio_out,
    }


def _model_from_usage_key(key: str) -> str:
    """Extract the model id from a usage_info key.

    Keys look like "OpenAILLMService#1|||gpt-4.1" (current) or
    "ServiceName#0__model" (legacy). The model is the part after the separator.
    """
    if "|||" in key:
        return key.split("|||")[-1]
    if "__" in key:
        return key.split("__")[-1]
    return key


def _first_model(section: dict[str, Any]) -> str | None:
    """Return the model id of the first entry in an llm/tts/stt usage section."""
    for key in section:
        return _model_from_usage_key(key)
    return None


def _split_usage_key(key: str) -> tuple[str, str]:
    """Turn a usage_info key into (provider_slug, model) — speech-billing spec §7.

    Keys look like "CartesiaTTSService#0|||sonic-3.5". Algorithm: split on
    "|||"; take the left half; strip the "#<digits>" instance suffix; strip a
    trailing TTSService / STTService / TurnsSTTService / FluxSTTService /
    LLMService; strip a leading "Dograh" wrapper prefix; lowercase. Examples:
        "CartesiaTTSService#0|||sonic-3.5"        -> ("cartesia", "sonic-3.5")
        "DeepgramFluxSTTService#0|||flux-general" -> ("deepgram", "flux-general")
        "CartesiaTurnsSTTService#0|||ink-2"       -> ("cartesia", "ink-2")
        "ElevenLabsTTSService#0|||eleven_flash"   -> ("elevenlabs", "eleven_flash")
    """
    left, sep, right = key.partition("|||")
    model = right if sep else ""
    cls = left.split("#", 1)[0]  # drop "#<n>"
    # Strip known service-class suffixes (longest first).
    for suffix in (
        "RealtimeLLMService",  # OpenAI/Grok realtime (speech-to-speech)
        "LiveLLMService",      # Gemini Live (speech-to-speech)
        "TurnsSTTService",
        "FluxSTTService",
        "STTService",
        "TTSService",
        "LLMService",
        "Service",
    ):
        if cls.endswith(suffix):
            cls = cls[: -len(suffix)]
            break
    if cls.startswith("Dograh"):
        cls = cls[len("Dograh") :]
    # A couple of realtime classes carry a trailing modality word after the
    # service suffix is stripped (e.g. "...Realtime"/"...Live"); drop it so the
    # provider slug is clean (OpenAIRealtime -> openai, GeminiLive -> gemini).
    for tail in ("Realtime", "Live"):
        if cls.endswith(tail) and cls != tail:
            cls = cls[: -len(tail)]
            break
    return cls.lower(), model


def _extract_tts_usage(usage_info: dict[str, Any]) -> tuple[int, str | None, str | None]:
    """Sum TTS characters and return (characters, provider, model).

    usage_info["tts"] maps "Service#n|||model" -> character count (int).
    """
    tts = usage_info.get("tts") or {}
    characters = 0
    provider: str | None = None
    model: str | None = None
    for key, value in tts.items():
        try:
            characters += int(value or 0)
        except (TypeError, ValueError):
            continue
        if provider is None:
            provider, model = _split_usage_key(key)
    return characters, provider, model


def _extract_stt_usage(usage_info: dict[str, Any]) -> tuple[Decimal, str | None, str | None]:
    """Sum STT audio seconds and return (seconds, provider, model).

    usage_info["stt"] maps "Service#n|||model" -> seconds (number). Populated by
    the pipeline's STT metrics for voice runs; empty for text chats.
    """
    stt = usage_info.get("stt") or {}
    seconds = Decimal("0")
    provider: str | None = None
    model: str | None = None
    for key, value in stt.items():
        try:
            seconds += Decimal(str(value or 0))
        except (TypeError, ValueError):
            continue
        if provider is None:
            provider, model = _split_usage_key(key)
    return seconds, provider, model


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


async def _calculate_cost_usd(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    embedding_tokens: int,
    tts_characters: int,
    tts_model: str | None,
    tts_provider: str | None,
    stt_seconds: Decimal,
    stt_model: str | None,
    stt_provider: str | None,
    stt_source: str,
    chat_model: str | None,
    embedding_model: str | None,
    realtime: dict | None = None,
) -> tuple[Decimal, int | None, dict]:
    """Compute the USD cost using per-model rates from the pricing DB.

    Formula (speech-billing spec §5.1):
        cost_usd = (
            prompt_tokens     * chat_input_rate   / 1M
          + completion_tokens * chat_output_rate  / 1M
          + embedding_tokens  * embedding_rate    / 1M
          + tts_characters    * tts_rate_per_1m   / 1M      (USD per 1M chars)
          + stt_seconds       * stt_rate_per_hour / 3600    (USD per hour)
        ) * 1.2

    Rates are looked up for the specific model each service used. Chat/embedding
    fall back to the legacy flat rate when unmapped; TTS/STT fall back to a
    deliberately-high default rate (never $0) with a logged warning.

    Returns (cost_usd, chat_pricing_version_id, breakdown). `breakdown` is the
    per-component JSON stored on the ledger row (spec §9.2).
    """
    from api.enums import ModelPricingKind

    raw = Decimal("0")
    chat_version_id: int | None = None
    breakdown: dict = {"markup": str(_PLATFORM_MARKUP)}

    def _sub(amount: Decimal) -> str:
        return str(amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

    # Chat (per-1M-token).
    if prompt_tokens > 0 or completion_tokens > 0:
        input_rate = _FALLBACK_INPUT_PRICE_PER_1M
        output_rate = _FALLBACK_OUTPUT_PRICE_PER_1M
        row = (
            await db_client.get_active_price_by_model(chat_model, kind=ModelPricingKind.CHAT)
            if chat_model
            else None
        )
        if row is not None:
            chat_version_id = row.id
            input_rate = _dec(row.input_per_1m) or _FALLBACK_INPUT_PRICE_PER_1M
            output_rate = _dec(row.output_per_1m) or _FALLBACK_OUTPUT_PRICE_PER_1M
        else:
            logger.warning(
                "No active chat price for model {} — using fallback rate", chat_model
            )
        chat_cost = (
            Decimal(prompt_tokens) * input_rate / _MILLION
            + Decimal(completion_tokens) * output_rate / _MILLION
        )
        raw += chat_cost
        breakdown["llm"] = {
            "model": chat_model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "input_rate_per_1m": str(input_rate),
            "output_rate_per_1m": str(output_rate),
            "cost_usd": _sub(chat_cost),
        }

    # Embedding (per-1M-token).
    if embedding_tokens > 0:
        embed_rate = _FALLBACK_EMBED_PRICE_PER_1M
        row = (
            await db_client.get_active_price_by_model(
                embedding_model, kind=ModelPricingKind.EMBEDDING
            )
            if embedding_model
            else None
        )
        if row is not None:
            embed_rate = _dec(row.embedding_per_1m) or _FALLBACK_EMBED_PRICE_PER_1M
        else:
            logger.warning(
                "No active embedding price for model {} — using fallback rate",
                embedding_model,
            )
        embed_cost = Decimal(embedding_tokens) * embed_rate / _MILLION
        raw += embed_cost
        breakdown["embedding"] = {
            "model": embedding_model,
            "tokens": embedding_tokens,
            "rate_per_1m": str(embed_rate),
            "cost_usd": _sub(embed_cost),
        }

    # TTS (USD per 1,000,000 characters). Fallback rate if unmapped (never $0).
    if tts_characters > 0:
        row = (
            await db_client.get_active_price_by_model(tts_model, kind=ModelPricingKind.TTS)
            if tts_model
            else None
        )
        rate = _dec(row.rate) if row else None
        if rate is None:
            rate = _FALLBACK_TTS_PER_1M_CHARS
            logger.warning(
                "No TTS price for model {} ({}) — using fallback {}/1M chars",
                tts_model, tts_provider, rate,
            )
        tts_cost = Decimal(tts_characters) * rate / _MILLION
        raw += tts_cost
        breakdown["tts"] = {
            "provider": tts_provider,
            "model": tts_model,
            "characters": tts_characters,
            "rate_per_1m": str(rate),
            "cost_usd": _sub(tts_cost),
        }

    # STT (USD per hour of audio). Fallback rate if unmapped (never $0).
    if stt_seconds > 0:
        row = (
            await db_client.get_active_price_by_model(stt_model, kind=ModelPricingKind.STT)
            if stt_model
            else None
        )
        rate = _dec(row.rate) if row else None
        if rate is None:
            rate = _FALLBACK_STT_PER_HOUR
            logger.warning(
                "No STT price for model {} ({}) — using fallback {}/hr",
                stt_model, stt_provider, rate,
            )
        stt_cost = stt_seconds * rate / _SECONDS_PER_HOUR
        raw += stt_cost
        breakdown["stt"] = {
            "provider": stt_provider,
            "model": stt_model,
            "seconds": str(stt_seconds),
            "rate_per_hour": str(rate),
            "source": stt_source,
            "cost_usd": _sub(stt_cost),
        }

    # Speech-to-speech realtime (four per-1M-token rates: text/audio × in/out).
    # Used when the run's LLM was a realtime model (OpenAI Realtime / Gemini
    # Live). Billed instead of the chat term for those tokens, never alongside.
    if realtime is not None:
        rt_model = realtime.get("model")
        rt_provider = realtime.get("provider")
        row = (
            await db_client.get_active_price_by_model(
                rt_model, kind=ModelPricingKind.SPEECH_TO_SPEECH
            )
            if rt_model
            else None
        )
        ti_rate = _dec(getattr(row, "rt_text_input_per_1m", None)) if row else None
        to_rate = _dec(getattr(row, "rt_text_output_per_1m", None)) if row else None
        ai_rate = _dec(getattr(row, "rt_audio_input_per_1m", None)) if row else None
        ao_rate = _dec(getattr(row, "rt_audio_output_per_1m", None)) if row else None
        if row is None or (ti_rate is None and to_rate is None and ai_rate is None and ao_rate is None):
            logger.warning(
                "No speech-to-speech price for model {} ({}) — using fallback rates",
                rt_model, rt_provider,
            )
        ti_rate = ti_rate if ti_rate is not None else _FALLBACK_RT_TEXT_INPUT_PER_1M
        to_rate = to_rate if to_rate is not None else _FALLBACK_RT_TEXT_OUTPUT_PER_1M
        ai_rate = ai_rate if ai_rate is not None else _FALLBACK_RT_AUDIO_INPUT_PER_1M
        ao_rate = ao_rate if ao_rate is not None else _FALLBACK_RT_AUDIO_OUTPUT_PER_1M

        text_in = int(realtime.get("text_input_tokens") or 0)
        audio_in = int(realtime.get("audio_input_tokens") or 0)
        text_out = int(realtime.get("text_output_tokens") or 0)
        audio_out = int(realtime.get("audio_output_tokens") or 0)

        rt_cost = (
            Decimal(text_in) * ti_rate / _MILLION
            + Decimal(text_out) * to_rate / _MILLION
            + Decimal(audio_in) * ai_rate / _MILLION
            + Decimal(audio_out) * ao_rate / _MILLION
        )
        raw += rt_cost
        breakdown["speech_to_speech"] = {
            "provider": rt_provider,
            "model": rt_model,
            "text_input_tokens": text_in,
            "text_output_tokens": text_out,
            "audio_input_tokens": audio_in,
            "audio_output_tokens": audio_out,
            "rt_text_input_per_1m": str(ti_rate),
            "rt_text_output_per_1m": str(to_rate),
            "rt_audio_input_per_1m": str(ai_rate),
            "rt_audio_output_per_1m": str(ao_rate),
            "cost_usd": _sub(rt_cost),
        }

    cost = (raw * _PLATFORM_MARKUP).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    breakdown["total_usd"] = str(cost)
    return cost, chat_version_id, breakdown


async def _report_oss_platform_usage(workflow_run) -> None:
    """Debit the prepaid wallet ledger for a completed run in OSS mode.

    Uses the platform formula (USD):
        cost_usd = (
            (prompt_tokens   * 0.15 / 1M) +
            (completion_tokens * 0.60 / 1M) +
            (embedding_tokens  * 0.02 / 1M)
        ) * 1.2
    """
    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping OSS usage record for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    prompt_tokens, completion_tokens, embedding_tokens = _extract_token_counts(usage_info)
    tts_characters, tts_provider, tts_model = _extract_tts_usage(usage_info)
    stt_seconds, stt_provider, stt_model = _extract_stt_usage(usage_info)
    # Speech-to-speech realtime usage (None for ordinary pipeline runs). Billed
    # separately; its tokens are already excluded from the chat term above.
    realtime = _extract_realtime_usage(usage_info)

    # Chat model id for rate lookup — but not a realtime model (those are billed
    # under speech_to_speech, and _extract_token_counts skipped their tokens, so
    # prompt/completion here are the text-LLM side-channel only).
    chat_model = None
    for key, entry in (usage_info.get("llm") or {}).items():
        if isinstance(entry, dict) and _is_realtime_llm_entry(entry):
            continue
        chat_model = _model_from_usage_key(key)
        break
    embedding_model = "text-embedding-3-small" if embedding_tokens > 0 else None

    # STT fallback (spec §5.3): the STT socket is open for the whole call and
    # vendors bill connection time including silence, so call_duration_seconds
    # is a close, safe stand-in when no per-model STT metric was recorded.
    # Only voice runs use a transcriber — text chat has a call_duration_seconds
    # but no audio and no STT, so it must never be billed for STT.
    # A realtime (speech-to-speech) model does STT internally and is billed via
    # its audio tokens — never also bill the STT duration fallback for it.
    stt_source = "metrics"
    if stt_seconds <= 0 and realtime is None and _run_uses_audio(workflow_run):
        duration = _duration_seconds_from_usage_info(workflow_run)
        if duration and duration > 0:
            stt_seconds = Decimal(str(duration))
            stt_source = "call_duration_fallback"
            # Determine which STT provider/model the run used, from config.
            stt_provider, stt_model = await _resolve_stt_model_for_run(workflow_run)
            logger.info(
                "Run {}: no STT metrics, billing on call_duration_seconds={} (model {})",
                workflow_run.id, stt_seconds, stt_model,
            )

    realtime_tokens = 0
    if realtime is not None:
        realtime_tokens = (
            int(realtime.get("text_input_tokens") or 0)
            + int(realtime.get("audio_input_tokens") or 0)
            + int(realtime.get("text_output_tokens") or 0)
            + int(realtime.get("audio_output_tokens") or 0)
        )

    has_usage = (
        prompt_tokens > 0
        or completion_tokens > 0
        or embedding_tokens > 0
        or tts_characters > 0
        or stt_seconds > 0
        or realtime_tokens > 0
    )
    if not has_usage:
        logger.debug(
            "Skipping OSS usage record for workflow run {}: no billable usage found",
            workflow_run.id,
        )
        return

    cost_usd, chat_version_id, breakdown = await _calculate_cost_usd(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        embedding_tokens=embedding_tokens,
        tts_characters=tts_characters,
        tts_model=tts_model,
        tts_provider=tts_provider,
        stt_seconds=stt_seconds,
        stt_model=stt_model,
        stt_provider=stt_provider,
        stt_source=stt_source,
        chat_model=chat_model,
        embedding_model=embedding_model,
        realtime=realtime,
    )

    # Guard on the FINAL cost, not token counts (spec §9.1): a TTS/STT-only call
    # has no LLM tokens but still costs money. apply_ledger_entry rejects a zero
    # amount, so skip only when the computed cost is truly zero.
    if cost_usd <= 0:
        logger.debug(
            "Skipping OSS usage record for workflow run {}: computed cost is zero",
            workflow_run.id,
        )
        return

    tokens_charged = prompt_tokens + completion_tokens + embedding_tokens

    try:
        # Debit the wallet ledger. amount_usd is NEGATIVE (money leaving the
        # wallet). The idempotency key run:{id} makes this safe against ARQ
        # retries of the completion task — a retry finds the existing entry and
        # does not debit again.
        await db_client.apply_ledger_entry(
            organization_id=organization_id,
            entry_type=CreditLedgerEntryType.DEBIT_CALL,
            amount_usd=-cost_usd,
            idempotency_key=f"run:{workflow_run.id}",
            workflow_run_id=workflow_run.id,
            tokens_charged=tokens_charged,
            usage_breakdown=breakdown,
        )
        logger.info(
            "Recorded OSS usage for workflow run {}: "
            "chat={} prompt={} output={} embed={} tts_chars={} stt_sec={} → ${} USD debited",
            workflow_run.id,
            chat_model,
            prompt_tokens,
            completion_tokens,
            embedding_tokens,
            tts_characters,
            stt_seconds,
            cost_usd,
        )
    except Exception as e:
        logger.error(
            "Failed to record OSS usage for workflow run {}: {}",
            workflow_run.id,
            e,
        )
        return

    # Immutable per-run audit: which models, what usage, which rate version,
    # final charge. Never let an audit failure undo the debit above.
    try:
        await db_client.add_usage_audit(
            workflow_run_id=workflow_run.id,
            computed_charge_usd=cost_usd,
            chat_model=chat_model,
            embedding_model=embedding_model,
            tts_model=tts_model,
            stt_model=stt_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            embedding_tokens=embedding_tokens,
            tts_characters=tts_characters,
            stt_seconds=stt_seconds,
            chat_pricing_version_id=chat_version_id,
        )
    except Exception as e:
        logger.warning(
            "Failed to write usage audit for workflow run {}: {}",
            workflow_run.id,
            e,
        )

    # Fire PostHog event after every completed call billing
    try:
        capture_event(
            distinct_id=str(organization_id),
            event=PostHogEvent.CALL_COMPLETED_BILLING,
            properties={
                "workflow_run_id": workflow_run.id,
                "organization_id": organization_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "embedding_tokens": embedding_tokens,
                "tts_characters": tts_characters,
                "stt_seconds": float(stt_seconds),
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": float(cost_usd),
                "model": chat_model or "unknown",
            },
        )
    except Exception as e:
        logger.warning(
            "Failed to send PostHog billing event for workflow run {}: {}",
            workflow_run.id,
            e,
        )


async def _organization_uses_mps_billing_v2(organization_id: int) -> bool:
    account = await mps_service_key_client.get_billing_account_status(
        organization_id=organization_id
    )
    return bool(account and account.get("billing_mode") == "v2")


def _is_usage_not_ready_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 409:
        return False
    return "usage_not_ready" in (getattr(response, "text", "") or "")


async def report_workflow_run_platform_usage(workflow_run) -> None:
    """Report hosted platform usage for a completed workflow run to MPS.

    In OSS mode, usage is recorded locally instead (see _report_oss_platform_usage).
    """
    if DEPLOYMENT_MODE == "oss":
        await _report_oss_platform_usage(workflow_run)
        return

    if not getattr(workflow_run, "is_completed", False):
        logger.warning(
            "Workflow run is not completed in report_workflow_run_platform_usage"
        )
        return

    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    duration_seconds = (
        None if correlation_id else _duration_seconds_from_usage_info(workflow_run)
    )
    if not correlation_id and duration_seconds is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no billable duration",
            workflow_run.id,
        )
        return

    try:
        if not await _organization_uses_mps_billing_v2(organization_id):
            logger.debug(
                "Not reporting platform usage since org not using mps billing v2"
            )
            return

        result = await mps_service_key_client.report_platform_usage(
            organization_id=organization_id,
            correlation_id=correlation_id,
            duration_seconds=duration_seconds,
            workflow_run_id=workflow_run.id,
            metadata={
                "source": "workflow_run_completion",
                "workflow_id": getattr(workflow_run, "workflow_id", None),
                "duration_source": (
                    "mps_correlation" if correlation_id else "dograh_usage_info"
                ),
            },
        )
        logger.info(
            "Reported platform usage for workflow run {} to MPS: {}",
            workflow_run.id,
            result,
        )
    except Exception as e:
        if _is_usage_not_ready_error(e):
            # A run can start and receive an MPS correlation id, then fail or end
            # before billable STT usage is recorded. MPS returns usage_not_ready
            # for that no-platform-fee path, so keep it out of error alerts.
            logger.warning(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )
        else:
            logger.error(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )


async def report_completed_workflow_run_platform_usage(workflow_run_id: int) -> None:
    """Load a completed workflow run and report platform usage to MPS."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Skipping platform usage report: workflow run {} not found",
            workflow_run_id,
        )
        return

    await report_workflow_run_platform_usage(workflow_run)
