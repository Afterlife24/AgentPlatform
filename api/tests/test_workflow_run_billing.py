from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.enums import CreditLedgerEntryType
from api.services import workflow_run_billing as workflow_run_billing_mod
from api.services.workflow_run_billing import (
    _calculate_cost_usd,
    _is_usage_not_ready_error,
    _report_oss_platform_usage,
    _split_usage_key,
    report_completed_workflow_run_platform_usage,
    report_workflow_run_platform_usage,
)


def _make_workflow_run():
    return SimpleNamespace(
        id=123,
        workflow_id=456,
        is_completed=True,
        initial_context={"mps_correlation_id": "mps-corr-123"},
        usage_info={"call_duration_seconds": 87},
        workflow=SimpleNamespace(
            organization_id=42,
            user=SimpleNamespace(selected_organization_id=42),
        ),
    )


def test_is_usage_not_ready_error_detects_mps_409():
    exc = Exception("Failed to report platform usage")
    exc.response = SimpleNamespace(
        status_code=409,
        text='{"detail":"usage_not_ready"}',
    )

    assert _is_usage_not_ready_error(exc) is True


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_hosted_completion(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id="mps-corr-123",
        duration_seconds=None,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "mps_correlation",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id=None,
        duration_seconds=87.0,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "dograh_usage_info",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_non_v2_account(monkeypatch):
    workflow_run = _make_workflow_run()
    get_status = AsyncMock(return_value={"billing_mode": "v1"})
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    get_status.assert_awaited_once_with(organization_id=42)
    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_missing_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    workflow_run.usage_info = {}
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    get_status.assert_not_awaited()
    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_oss(monkeypatch):
    workflow_run = _make_workflow_run()
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_incomplete(monkeypatch):
    workflow_run = _make_workflow_run()
    workflow_run.is_completed = False
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_completed_workflow_run_platform_usage_loads_run(monkeypatch):
    workflow_run = _make_workflow_run()
    get_run = AsyncMock(return_value=workflow_run)
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        get_run,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_completed_workflow_run_platform_usage(workflow_run.id)

    get_run.assert_awaited_once_with(workflow_run.id)
    report_usage.assert_awaited_once()


# ───────────────────────── Phase 4: USD cost formula ─────────────────────────


async def _calc_fallback(prompt, completion, embedding, monkeypatch):
    """Call _calculate_cost_usd with NO DB rows present (→ fallback flat rates).

    Stage D made the calc async and DB-driven; with no active price row it falls
    back to the legacy flat 0.15/0.60/0.02 rates, which reproduce the original
    reference rows. We stub the DB lookup to return None so no DB is needed.
    """
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=None),
    )
    cost, _version_id, _breakdown = await _calculate_cost_usd(
        prompt_tokens=prompt,
        completion_tokens=completion,
        embedding_tokens=embedding,
        tts_characters=0,
        tts_model=None,
        tts_provider=None,
        stt_seconds=Decimal("0"),
        stt_model=None,
        stt_provider=None,
        stt_source="metrics",
        chat_model="gpt-4.1" if (prompt or completion) else None,
        embedding_model="text-embedding-3-small" if embedding else None,
    )
    return cost


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,completion,embedding,expected",
    [
        # The five reference rows from design.md §6 — must match exactly
        # using the fallback flat rates when no DB pricing row exists.
        (1_000_000, 0, 0, Decimal("0.180000")),
        (0, 1_000_000, 0, Decimal("0.720000")),
        (0, 0, 1_000_000, Decimal("0.024000")),
        (100_779, 1_472, 54, Decimal("0.019201")),
        (0, 0, 0, Decimal("0.000000")),
    ],
)
async def test_calculate_cost_usd_reference_rows(
    prompt, completion, embedding, expected, monkeypatch
):
    """The USD cost formula must reproduce the five reference rows exactly."""
    assert await _calc_fallback(prompt, completion, embedding, monkeypatch) == expected


@pytest.mark.asyncio
async def test_calculate_cost_usd_returns_decimal(monkeypatch):
    """Cost must be a Decimal (never float) for exact ledger storage."""
    result = await _calc_fallback(1_000_000, 0, 0, monkeypatch)
    assert isinstance(result, Decimal)


@pytest.mark.asyncio
async def test_calculate_cost_usd_applies_markup(monkeypatch):
    """The 1.2x platform markup must be applied.

    Guards against anyone silently deleting the markup. Raw input cost for
    1M prompt tokens is $0.15; with the 1.2x markup it is $0.18. If the markup
    were dropped the result would be 0.150000, so this assertion fails loudly.
    """
    with_markup = await _calc_fallback(1_000_000, 0, 0, monkeypatch)
    assert with_markup == Decimal("0.180000")
    # Explicitly: result == raw * 1.2
    raw = Decimal("0.15")
    assert with_markup == (raw * Decimal("1.2")).quantize(Decimal("0.000001"))


@pytest.mark.asyncio
async def test_calculate_cost_usd_uses_db_rate(monkeypatch):
    """When a model has an active DB price, that rate is used (not the fallback)."""
    db_row = SimpleNamespace(
        id=7, input_per_1m=Decimal("2.0"), output_per_1m=Decimal("8.0"),
        embedding_per_1m=None, rate=None,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=db_row),
    )
    cost, version_id, _breakdown = await _calculate_cost_usd(
        prompt_tokens=1_000_000, completion_tokens=0, embedding_tokens=0,
        tts_characters=0, tts_model=None, tts_provider=None,
        stt_seconds=Decimal("0"), stt_model=None, stt_provider=None,
        stt_source="metrics",
        chat_model="gpt-4.1", embedding_model=None,
    )
    # 1M prompt * $2.0/1M * 1.2 markup = $2.40 (not the $0.18 fallback).
    assert cost == Decimal("2.400000")
    assert version_id == 7


# ───────────────── Speech-billing spec: TTS/STT units + breakdown ─────────────


@pytest.mark.asyncio
async def test_calculate_cost_usd_tts_per_1m_chars(monkeypatch):
    """TTS bills USD per 1,000,000 characters (spec §5.1).

    50,000 chars @ $50/1M = $2.50 raw * 1.2 markup = $3.00.
    """
    tts_row = SimpleNamespace(id=11, rate=Decimal("50.00"))
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=tts_row),
    )
    cost, _version_id, breakdown = await _calculate_cost_usd(
        prompt_tokens=0, completion_tokens=0, embedding_tokens=0,
        tts_characters=50_000, tts_model="sonic-3", tts_provider="cartesia",
        stt_seconds=Decimal("0"), stt_model=None, stt_provider=None,
        stt_source="metrics", chat_model=None, embedding_model=None,
    )
    assert cost == Decimal("3.000000")
    assert breakdown["tts"]["characters"] == 50_000
    assert breakdown["tts"]["rate_per_1m"] == "50.00"
    assert breakdown["tts"]["provider"] == "cartesia"
    assert breakdown["total_usd"] == "3.000000"


@pytest.mark.asyncio
async def test_calculate_cost_usd_stt_per_hour(monkeypatch):
    """STT bills USD per hour of audio (spec §5.1).

    1800 seconds (0.5 hr) @ $0.54/hr = $0.27 raw * 1.2 markup = $0.324.
    """
    stt_row = SimpleNamespace(id=12, rate=Decimal("0.54"))
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=stt_row),
    )
    cost, _version_id, breakdown = await _calculate_cost_usd(
        prompt_tokens=0, completion_tokens=0, embedding_tokens=0,
        tts_characters=0, tts_model=None, tts_provider=None,
        stt_seconds=Decimal("1800"), stt_model="ink-2", stt_provider="cartesia",
        stt_source="call_duration_fallback", chat_model=None, embedding_model=None,
    )
    assert cost == Decimal("0.324000")
    assert breakdown["stt"]["seconds"] == "1800"
    assert breakdown["stt"]["rate_per_hour"] == "0.54"
    assert breakdown["stt"]["source"] == "call_duration_fallback"


@pytest.mark.asyncio
async def test_calculate_cost_usd_speech_fallback_never_zero(monkeypatch):
    """An unmapped TTS/STT model must bill the high fallback rate, never $0."""
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=None),  # no price row
    )
    cost, _version_id, breakdown = await _calculate_cost_usd(
        prompt_tokens=0, completion_tokens=0, embedding_tokens=0,
        tts_characters=10_000, tts_model="unknown-voice", tts_provider="mystery",
        stt_seconds=Decimal("60"), stt_model="unknown-ears", stt_provider="mystery",
        stt_source="metrics", chat_model=None, embedding_model=None,
    )
    assert cost > Decimal("0")
    # Fallback rates: TTS 50/1M, STT 0.60/hr.
    assert breakdown["tts"]["rate_per_1m"] == "50.00"
    assert breakdown["stt"]["rate_per_hour"] == "0.60"


@pytest.mark.asyncio
async def test_calculate_cost_usd_speech_to_speech_four_rates(monkeypatch):
    """Speech-to-speech bills four per-1M-token rates (text/audio × in/out).

    text_in 1000@5 + text_out 500@20 + audio_in 10000@40 + audio_out 20000@80
    = 0.005 + 0.010 + 0.400 + 1.600 = 2.015 raw * 1.2 = 2.418.
    """
    rt_row = SimpleNamespace(
        id=20,
        rt_text_input_per_1m=Decimal("5"),
        rt_text_output_per_1m=Decimal("20"),
        rt_audio_input_per_1m=Decimal("40"),
        rt_audio_output_per_1m=Decimal("80"),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "get_active_price_by_model",
        AsyncMock(return_value=rt_row),
    )
    cost, _vid, breakdown = await _calculate_cost_usd(
        prompt_tokens=0, completion_tokens=0, embedding_tokens=0,
        tts_characters=0, tts_model=None, tts_provider=None,
        stt_seconds=Decimal("0"), stt_model=None, stt_provider=None,
        stt_source="metrics", chat_model=None, embedding_model=None,
        realtime={
            "provider": "openai", "model": "gpt-realtime",
            "text_input_tokens": 1000, "text_output_tokens": 500,
            "audio_input_tokens": 10000, "audio_output_tokens": 20000,
        },
    )
    assert cost == Decimal("2.418000")
    s2s = breakdown["speech_to_speech"]
    assert s2s["model"] == "gpt-realtime"
    assert s2s["audio_output_tokens"] == 20000
    assert s2s["rt_audio_output_per_1m"] == "80"


@pytest.mark.asyncio
async def test_calculate_cost_usd_speech_to_speech_fallback_never_zero(monkeypatch):
    """An unmapped realtime model bills the high fallback rates, never $0."""
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "get_active_price_by_model",
        AsyncMock(return_value=None),
    )
    cost, _vid, breakdown = await _calculate_cost_usd(
        prompt_tokens=0, completion_tokens=0, embedding_tokens=0,
        tts_characters=0, tts_model=None, tts_provider=None,
        stt_seconds=Decimal("0"), stt_model=None, stt_provider=None,
        stt_source="metrics", chat_model=None, embedding_model=None,
        realtime={
            "provider": "mystery", "model": "unknown-realtime",
            "text_input_tokens": 0, "text_output_tokens": 0,
            "audio_input_tokens": 1000, "audio_output_tokens": 1000,
        },
    )
    assert cost > Decimal("0")
    assert breakdown["speech_to_speech"]["rt_audio_output_per_1m"] == "80.00"


def test_extract_realtime_usage_splits_modalities():
    """_extract_realtime_usage sums the per-modality token split and identifies
    a realtime entry; a plain chat entry yields None."""
    from api.services.workflow_run_billing import _extract_realtime_usage

    usage = {
        "llm": {
            "DograhOpenAIRealtimeLLMService#0|||gpt-realtime": {
                "prompt_tokens": 1100, "completion_tokens": 600,
                "prompt_text_tokens": 100, "prompt_audio_tokens": 1000,
                "completion_text_tokens": 100, "completion_audio_tokens": 500,
            }
        }
    }
    rt = _extract_realtime_usage(usage)
    assert rt is not None
    assert rt["provider"] == "openai"
    assert rt["model"] == "gpt-realtime"
    assert rt["text_input_tokens"] == 100
    assert rt["audio_input_tokens"] == 1000
    assert rt["audio_output_tokens"] == 500

    # Plain chat entry → not realtime.
    assert _extract_realtime_usage(
        {"llm": {"OpenAILLMService#0|||gpt-4.1": {"prompt_tokens": 10, "completion_tokens": 5}}}
    ) is None


@pytest.mark.parametrize(
    "key,expected",
    [
        ("CartesiaTTSService#0|||sonic-3.5", ("cartesia", "sonic-3.5")),
        ("DeepgramFluxSTTService#0|||flux-general", ("deepgram", "flux-general")),
        ("CartesiaTurnsSTTService#0|||ink-2", ("cartesia", "ink-2")),
        ("ElevenLabsTTSService#0|||eleven_flash", ("elevenlabs", "eleven_flash")),
        ("DograhCartesiaTTSService#1|||sonic-3", ("cartesia", "sonic-3")),
        ("OpenAILLMService#0|||gpt-4.1", ("openai", "gpt-4.1")),
    ],
)
def test_split_usage_key(key, expected):
    """usage_info keys normalise to (provider_slug, model) — spec §7."""
    assert _split_usage_key(key) == expected


# ──────────────────── Phase 4: OSS debit on run completion ────────────────────


def _make_oss_run(run_id=777, usage_info=None, mode=None):
    return SimpleNamespace(
        id=run_id,
        workflow_id=456,
        mode=mode,
        is_completed=True,
        initial_context={},
        usage_info=usage_info
        if usage_info is not None
        else {
            "llm": {
                "Svc#0__gemini-3.1": {
                    "prompt_tokens": 100_779,
                    "completion_tokens": 1_472,
                }
            },
            "embedding_tokens": 54,
        },
        workflow=SimpleNamespace(organization_id=42),
    )


@pytest.mark.asyncio
async def test_oss_completion_writes_one_negative_debit(monkeypatch):
    """A completed OSS run debits the ledger once with the correct USD amount."""
    run = _make_oss_run()
    apply = AsyncMock()
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", apply)
    # No DB pricing rows → fallback flat rates (keeps the reference amount).
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "add_usage_audit", AsyncMock()
    )
    # Silence PostHog
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)

    apply.assert_awaited_once()
    kwargs = apply.await_args.kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs["entry_type"] == CreditLedgerEntryType.DEBIT_CALL
    # Negative amount (money leaving the wallet), matching reference row 4
    assert kwargs["amount_usd"] == Decimal("-0.019201")
    assert kwargs["idempotency_key"] == "run:777"
    assert kwargs["workflow_run_id"] == 777
    # Single tokens_charged = prompt + completion + embedding
    assert kwargs["tokens_charged"] == 100_779 + 1_472 + 54
    # Per-component breakdown is stored on the ledger row (spec §9.2).
    breakdown = kwargs["usage_breakdown"]
    assert isinstance(breakdown, dict)
    assert breakdown["total_usd"] == "0.019201"
    assert "llm" in breakdown
    assert breakdown["markup"] == "1.2"


@pytest.mark.asyncio
async def test_oss_completion_zero_tokens_writes_nothing(monkeypatch):
    """A run with no billable tokens must write no ledger entry at all."""
    run = _make_oss_run(usage_info={"llm": {}, "embedding_tokens": 0})
    apply = AsyncMock()
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", apply)

    await _report_oss_platform_usage(run)

    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_chat_does_not_bill_stt_fallback(monkeypatch):
    """A text chat has a call_duration but no transcriber — never bill STT.

    Regression guard: the STT call-duration fallback must skip text-only run
    modes. Only the LLM cost should be charged.
    """
    run = _make_oss_run(
        mode="textchat",
        usage_info={
            "llm": {"OpenAILLMService#0|||gpt-4.1": {
                "prompt_tokens": 1000, "completion_tokens": 100}},
            "stt": {},
            "tts": {},
            "call_duration_seconds": 30,
        },
    )
    captured = {}

    async def fake_apply(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1)

    resolve = AsyncMock(return_value=("cartesia", "ink-whisper"))
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", fake_apply)
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "get_active_price_by_model",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(workflow_run_billing_mod, "_resolve_stt_model_for_run", resolve)
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "add_usage_audit", AsyncMock())
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)

    # STT resolution must not even be attempted for a text chat.
    resolve.assert_not_awaited()
    breakdown = captured["usage_breakdown"]
    assert "stt" not in breakdown
    assert "llm" in breakdown


@pytest.mark.asyncio
async def test_voice_run_bills_stt_fallback_on_duration(monkeypatch):
    """A voice run with no STT metrics bills STT on call_duration (fallback)."""
    run = _make_oss_run(
        mode="smallwebrtc",
        usage_info={
            "llm": {},
            "stt": {},
            "tts": {},
            "call_duration_seconds": 3600,  # 1 hour
        },
    )
    captured = {}

    async def fake_apply(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1)

    # STT rate row (0.18/hr) returned for the resolved model.
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", fake_apply)
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "get_active_price_by_model",
        AsyncMock(return_value=SimpleNamespace(id=5, rate=Decimal("0.18"))),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod, "_resolve_stt_model_for_run",
        AsyncMock(return_value=("cartesia", "ink-whisper")),
    )
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "add_usage_audit", AsyncMock())
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)

    breakdown = captured["usage_breakdown"]
    assert breakdown["stt"]["source"] == "call_duration_fallback"
    assert breakdown["stt"]["model"] == "ink-whisper"
    # 3600s = 1hr @ 0.18/hr = 0.18 raw * 1.2 = 0.216
    assert captured["amount_usd"] == Decimal("-0.216000")


@pytest.mark.asyncio
async def test_oss_completion_idempotent_on_retry(monkeypatch):
    """Re-running completion for the same run must not debit twice.

    The idempotency key run:{id} is what makes this safe. apply_ledger_entry
    itself dedups by key; here we assert the key is stable across retries so
    the DB-level dedup can do its job.
    """
    run = _make_oss_run(run_id=999)
    seen_keys = []

    async def fake_apply(**kwargs):
        seen_keys.append(kwargs["idempotency_key"])
        return SimpleNamespace(id=1)

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "apply_ledger_entry", fake_apply
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "add_usage_audit", AsyncMock()
    )
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)
    await _report_oss_platform_usage(run)

    # Both attempts use the identical idempotency key, so the real
    # apply_ledger_entry would dedup to a single ledger row.
    assert seen_keys == ["run:999", "run:999"]


@pytest.mark.asyncio
async def test_oss_completion_billing_failure_does_not_raise(monkeypatch):
    """A ledger failure must be swallowed (logged, not re-raised) so it never
    aborts artifact upload or integrations."""
    run = _make_oss_run()

    async def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "apply_ledger_entry", boom
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_active_price_by_model",
        AsyncMock(return_value=None),
    )

    # Must not raise
    await _report_oss_platform_usage(run)


# ─────────── Speech-billing spec: DB-backed end-to-end ledger round-trip ───────


@pytest.mark.asyncio
async def test_oss_completion_speech_only_ledger_round_trip(
    db_session, test_organization, monkeypatch
):
    """A TTS+STT-only call (no LLM tokens) debits the wallet and records the
    per-component usage_breakdown with spec units — against the real test DB.

    This exercises the whole path: rate lookup by model id, USD formula with
    per-1M-chars / per-hour units, markup, and usage_breakdown persistence.
    """
    from decimal import Decimal as D

    from api.db.models import ModelPricingModel, WorkflowModel, WorkflowRunModel

    org_id = test_organization.id

    # Seed rates + a minimal workflow/run so the ledger FK is satisfied.
    async with db_session.async_session() as session:
        session.add_all(
            [
                ModelPricingModel(
                    provider="cartesia", model_identifier="sonic-e2e", kind="tts",
                    source="manual", unit="per_1m_char", rate=D("50.00"),
                    status="active", is_enabled=True, effective_to=None,
                ),
                ModelPricingModel(
                    provider="cartesia", model_identifier="ink-e2e", kind="stt",
                    source="manual", unit="per_hour", rate=D("0.54"),
                    status="active", is_enabled=True, effective_to=None,
                ),
            ]
        )
        workflow = WorkflowModel(name="speech-e2e-wf", organization_id=org_id)
        session.add(workflow)
        await session.flush()
        wf_run = WorkflowRunModel(
            name="speech-e2e-run", workflow_id=workflow.id, mode="text_chat",
        )
        session.add(wf_run)
        await session.commit()
        await session.refresh(wf_run)
        run_id = wf_run.id

    # Wallet must exist before a debit.
    await db_session.get_or_create_account(organization_id=org_id)

    run = SimpleNamespace(
        id=run_id,
        workflow_id=workflow.id,
        is_completed=True,
        initial_context={},
        usage_info={
            "tts": {"CartesiaTTSService#0|||sonic-e2e": 50_000},   # 50k chars
            "stt": {"CartesiaTurnsSTTService#0|||ink-e2e": 1800},  # 0.5 hr
        },
        workflow=SimpleNamespace(organization_id=org_id),
    )

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "add_usage_audit", AsyncMock())
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)

    # Read the debit back from the ledger.
    entry = await db_session.get_entry_by_idempotency_key(f"run:{run_id}")
    assert entry is not None
    # TTS 50k*50/1M = 2.50 ; STT 1800*0.54/3600 = 0.27 ; (2.50+0.27)*1.2 = 3.324
    assert entry.amount_usd == Decimal("-3.324000")

    breakdown = entry.usage_breakdown
    assert breakdown is not None
    # Rates round-trip from the Numeric(18,6) column at 6 dp.
    assert Decimal(breakdown["tts"]["rate_per_1m"]) == Decimal("50")
    assert breakdown["tts"]["characters"] == 50_000
    assert Decimal(breakdown["stt"]["rate_per_hour"]) == Decimal("0.54")
    assert breakdown["stt"]["seconds"] == "1800"
    assert breakdown["total_usd"] == "3.324000"
