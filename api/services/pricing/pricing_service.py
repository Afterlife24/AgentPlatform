"""Dynamic model pricing service (Stage B).

Business logic for:
  - running a catalog sync (fetch LiteLLM → diff vs. live → populate pending),
  - approving pending diffs (versioned write into model_pricing),
  - manual STT/TTS CRUD,
  - listing models and toggling enable/disable (model + provider).

Billing never changes until an admin approves a pending diff — the sync only
writes to the pending queue.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from loguru import logger

from api.db import db_client
from api.enums import (
    ModelPricingChangeType,
    ModelPricingKind,
    ModelPricingSource,
    ModelPricingStatus,
    ModelPricingUnit,
)
from api.services.pricing.catalog_sync import CatalogEntry, fetch_normalized_catalog


def _rates_differ(a: Optional[Decimal], b: Optional[Decimal]) -> bool:
    """Compare two nullable rates for a meaningful difference."""
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return Decimal(a) != Decimal(b)


async def _models_in_use() -> set[tuple[str, str]]:
    """Collect (provider, model) pairs AgentX is CURRENTLY configured to use.

    Reads every org's resolved effective config and gathers the chat (llm) and
    embedding model identifiers. These are auto-approved + enabled on sync so an
    operator never has to hand-approve the model their agents already run on.

    Returns provider/model pairs, provider normalized to our enum values
    (google for Gemini). Never raises — a bad org config is skipped.
    """
    from api.db import db_client
    from api.services.configuration.ai_model_configuration import (
        get_resolved_ai_model_configuration,
    )

    pairs: set[tuple[str, str]] = set()
    try:
        orgs = await db_client.list_organizations_with_email()
    except Exception as e:
        logger.warning("Could not enumerate orgs for in-use models: {}", e)
        return pairs

    for org in orgs:
        org_id = org.get("organization_id")
        if org_id is None:
            continue
        try:
            resolved = await get_resolved_ai_model_configuration(
                user_id=None, organization_id=org_id
            )
            eff = resolved.effective
        except Exception:
            continue

        for section_name in ("llm", "embeddings"):
            section = getattr(eff, section_name, None)
            if section is None:
                continue
            provider = getattr(section, "provider", None)
            model = getattr(section, "model", None)
            provider_val = (
                provider.value if hasattr(provider, "value") else provider
            )
            if provider_val and model:
                pairs.add((str(provider_val), str(model)))

    return pairs


async def run_sync() -> dict:
    """Fetch the LiteLLM catalog and compute a diff into the pending queue.

    Returns a summary {sync_run_id, new, price_change, deprecated, total}.
    Does NOT touch live pricing. Replaces any previous pending diff.
    """
    try:
        catalog = fetch_normalized_catalog()
    except Exception as e:
        logger.error("Pricing sync failed to fetch/normalize catalog: {}", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch the pricing catalog. Please try again.",
        ) from e

    sync_run_id = uuid.uuid4().hex[:16]

    # Current live LiteLLM-synced rows (chat + embedding + speech-to-speech),
    # keyed by (provider, model). TTS/STT are manual and never synced here.
    live = {
        (p.provider, p.model_identifier): p
        for p in await db_client.list_pricing(current_only=True)
        if p.kind
        in (
            ModelPricingKind.CHAT.value,
            ModelPricingKind.EMBEDDING.value,
            ModelPricingKind.SPEECH_TO_SPEECH.value,
        )
    }
    catalog_keys = {(e.provider, e.model_identifier) for e in catalog}

    # Models AgentX is currently configured to use → auto-approve + enable so
    # the operator never has to hand-approve the model their agents run on.
    in_use = await _models_in_use()

    # Fresh diff each run.
    await db_client.clear_pending()

    counts = {"new": 0, "price_change": 0, "missing": 0, "auto_approved": 0}

    for entry in catalog:
        key = (entry.provider, entry.model_identifier)
        current = live.get(key)
        if current is None:
            if key in in_use:
                # Currently-used model → commit straight to live pricing,
                # active AND enabled, bypassing the pending queue entirely.
                await db_client.upsert_price_version(
                    provider=entry.provider,
                    model_identifier=entry.model_identifier,
                    kind=entry.kind,
                    source=ModelPricingSource.LITELLM,
                    unit=ModelPricingUnit.PER_1M_TOKEN,
                    input_per_1m=entry.input_per_1m,
                    output_per_1m=entry.output_per_1m,
                    cached_input_per_1m=entry.cached_input_per_1m,
                    embedding_per_1m=entry.embedding_per_1m,
                    rt_text_input_per_1m=entry.rt_text_input_per_1m,
                    rt_text_output_per_1m=entry.rt_text_output_per_1m,
                    rt_audio_input_per_1m=entry.rt_audio_input_per_1m,
                    rt_audio_output_per_1m=entry.rt_audio_output_per_1m,
                    status=ModelPricingStatus.ACTIVE,
                    is_enabled=True,
                )
                counts["auto_approved"] += 1
                continue
            # Other newly discovered model → needs admin approval first.
            await db_client.add_pending(
                provider=entry.provider,
                model_identifier=entry.model_identifier,
                kind=entry.kind,
                change_type=ModelPricingChangeType.NEW,
                sync_run_id=sync_run_id,
                proposed_input_per_1m=entry.input_per_1m,
                proposed_output_per_1m=entry.output_per_1m,
                proposed_cached_input_per_1m=entry.cached_input_per_1m,
                proposed_embedding_per_1m=entry.embedding_per_1m,
                proposed_rt_text_input_per_1m=entry.rt_text_input_per_1m,
                proposed_rt_text_output_per_1m=entry.rt_text_output_per_1m,
                proposed_rt_audio_input_per_1m=entry.rt_audio_input_per_1m,
                proposed_rt_audio_output_per_1m=entry.rt_audio_output_per_1m,
                proposed_status=ModelPricingStatus.ACTIVE,
            )
            counts["new"] += 1
            continue

        # An already-live model that AgentX is using must stay enabled — force
        # it on if it was left disabled, so the in-use model is always available.
        if key in in_use and not current.is_enabled:
            await db_client.set_model_enabled(current.id, True)
            counts["auto_approved"] += 1

        # Existing model: did any rate change? (chat/embedding + the four
        # speech-to-speech realtime rates).
        changed = (
            _rates_differ(entry.input_per_1m, current.input_per_1m)
            or _rates_differ(entry.output_per_1m, current.output_per_1m)
            or _rates_differ(entry.cached_input_per_1m, current.cached_input_per_1m)
            or _rates_differ(entry.embedding_per_1m, current.embedding_per_1m)
            or _rates_differ(entry.rt_text_input_per_1m, current.rt_text_input_per_1m)
            or _rates_differ(entry.rt_text_output_per_1m, current.rt_text_output_per_1m)
            or _rates_differ(entry.rt_audio_input_per_1m, current.rt_audio_input_per_1m)
            or _rates_differ(entry.rt_audio_output_per_1m, current.rt_audio_output_per_1m)
        )
        if changed:
            await db_client.add_pending(
                provider=entry.provider,
                model_identifier=entry.model_identifier,
                kind=entry.kind,
                change_type=ModelPricingChangeType.PRICE_CHANGE,
                sync_run_id=sync_run_id,
                proposed_input_per_1m=entry.input_per_1m,
                proposed_output_per_1m=entry.output_per_1m,
                proposed_cached_input_per_1m=entry.cached_input_per_1m,
                proposed_embedding_per_1m=entry.embedding_per_1m,
                proposed_rt_text_input_per_1m=entry.rt_text_input_per_1m,
                proposed_rt_text_output_per_1m=entry.rt_text_output_per_1m,
                proposed_rt_audio_input_per_1m=entry.rt_audio_input_per_1m,
                proposed_rt_audio_output_per_1m=entry.rt_audio_output_per_1m,
                proposed_status=ModelPricingStatus.ACTIVE,
                current_input_per_1m=current.input_per_1m,
                current_output_per_1m=current.output_per_1m,
                current_cached_input_per_1m=current.cached_input_per_1m,
                current_embedding_per_1m=current.embedding_per_1m,
                current_rt_text_input_per_1m=current.rt_text_input_per_1m,
                current_rt_text_output_per_1m=current.rt_text_output_per_1m,
                current_rt_audio_input_per_1m=current.rt_audio_input_per_1m,
                current_rt_audio_output_per_1m=current.rt_audio_output_per_1m,
            )
            counts["price_change"] += 1

    # Reconcile status of every STORED litellm chat/embedding model against the
    # catalog (status-only; never blocks runs). A model still in the catalog is
    # marked ACTIVE; one that has vanished (provider dropped it) is marked
    # MISSING so the admin sees it inline. Manual STT/TTS are not in `live`, so
    # they're never touched here. `missing` counts those newly flagged this run.
    for key, current in live.items():
        present = key in catalog_keys
        desired = (
            ModelPricingStatus.ACTIVE.value if present else ModelPricingStatus.MISSING.value
        )
        # Don't override an admin-set DEPRECATED; only reconcile active<->missing.
        if current.status == ModelPricingStatus.DEPRECATED.value:
            continue
        if current.status != desired:
            await db_client.set_model_status(
                provider=current.provider,
                model_identifier=current.model_identifier,
                status=(
                    ModelPricingStatus.ACTIVE if present else ModelPricingStatus.MISSING
                ),
            )
            if not present:
                counts["missing"] += 1

    total = counts["new"] + counts["price_change"]
    logger.info(
        "Pricing sync {} done: new={} price_change={} missing={} auto_approved={}",
        sync_run_id,
        counts["new"],
        counts["price_change"],
        counts["missing"],
        counts["auto_approved"],
    )

    # Telemetry: record each sync so catalog-change volume is observable.
    try:
        from api.enums import PostHogEvent
        from api.services.posthog_client import capture_event

        capture_event(
            distinct_id="pricing-sync",
            event=PostHogEvent.MODEL_PRICING_SYNCED.value,
            properties={
                "sync_run_id": sync_run_id,
                "new": counts["new"],
                "price_change": counts["price_change"],
                "missing": counts["missing"],
                "auto_approved": counts["auto_approved"],
                "total": total,
            },
        )
    except Exception as e:
        logger.warning("Failed to send pricing-sync PostHog event: {}", e)

    return {"sync_run_id": sync_run_id, **counts, "total": total}


async def approve_pending(pending_id: int) -> dict:
    """Apply one pending diff to live pricing (versioned write).

    - new / price_change → upsert a new version with the proposed rates.
    - deprecated         → mark the current row deprecated.
    Then remove the pending row. Returns a short summary.
    """
    pending = await db_client.get_pending_by_id(pending_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending change not found")

    change_type = pending.change_type
    kind = ModelPricingKind(pending.kind)

    if change_type == ModelPricingChangeType.DEPRECATED.value:
        await db_client.set_model_status(
            provider=pending.provider,
            model_identifier=pending.model_identifier,
            status=ModelPricingStatus.DEPRECATED,
        )
    else:
        unit = ModelPricingUnit.PER_1M_TOKEN  # chat/embedding/speech-to-speech are token-based
        await db_client.upsert_price_version(
            provider=pending.provider,
            model_identifier=pending.model_identifier,
            kind=kind,
            source=ModelPricingSource.LITELLM,
            unit=unit,
            input_per_1m=pending.proposed_input_per_1m,
            output_per_1m=pending.proposed_output_per_1m,
            cached_input_per_1m=pending.proposed_cached_input_per_1m,
            embedding_per_1m=pending.proposed_embedding_per_1m,
            rt_text_input_per_1m=pending.proposed_rt_text_input_per_1m,
            rt_text_output_per_1m=pending.proposed_rt_text_output_per_1m,
            rt_audio_input_per_1m=pending.proposed_rt_audio_input_per_1m,
            rt_audio_output_per_1m=pending.proposed_rt_audio_output_per_1m,
            status=ModelPricingStatus.ACTIVE,
        )

    await db_client.delete_pending(pending_id)
    return {
        "approved_id": pending_id,
        "provider": pending.provider,
        "model_identifier": pending.model_identifier,
        "change_type": change_type,
    }


async def reject_pending(pending_id: int) -> dict:
    """Discard a pending diff without applying it."""
    ok = await db_client.delete_pending(pending_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pending change not found")
    return {"rejected_id": pending_id}


async def add_manual_model(
    *,
    provider: str,
    model_identifier: str,
    kind: ModelPricingKind,
    rate: Decimal,
    is_enabled: Optional[bool] = None,
) -> dict:
    """Add (or re-price) a manual STT/TTS model with its per-unit rate.

    is_enabled semantics:
      - None  → preserve an existing model's enabled state; a brand-new model
                defaults to enabled (so "Add new" in the admin UI is usable).
                This is what the "change rate" flow sends, so re-pricing a
                disabled model never silently re-enables it.
      - True/False → force that enabled state (explicit admin intent).
    """
    if kind not in (ModelPricingKind.TTS, ModelPricingKind.STT):
        raise HTTPException(
            status_code=400,
            detail="Manual models must be of kind 'tts' or 'stt'.",
        )
    if rate is None or Decimal(rate) <= 0:
        raise HTTPException(status_code=400, detail="rate must be a positive number.")

    # Server-side sanity bounds (spec §8.2) — a stored rate is TTS USD/1M-chars
    # or STT USD/hour. A 1000x unit-conversion mistake lands outside these, so
    # this is the backstop behind the admin UI's unit dropdown + preview.
    rate_dec = Decimal(rate)
    if kind == ModelPricingKind.TTS and not (Decimal("1") <= rate_dec <= Decimal("500")):
        raise HTTPException(
            status_code=400,
            detail=(
                "TTS rate must be between 1.00 and 500.00 USD per 1M characters. "
                "Real rates sit around 15–80. Check the unit."
            ),
        )
    if kind == ModelPricingKind.STT and not (Decimal("0.01") <= rate_dec <= Decimal("20")):
        raise HTTPException(
            status_code=400,
            detail=(
                "STT rate must be between 0.01 and 20.00 USD per hour. "
                "Real rates sit around 0.13–0.55. Check the unit."
            ),
        )

    unit = (
        ModelPricingUnit.PER_1M_CHAR
        if kind == ModelPricingKind.TTS
        else ModelPricingUnit.PER_HOUR
    )

    # Resolve the enabled state to apply. When the caller didn't specify one
    # (the rate-change flow), preserve an existing model's state and default a
    # brand-new model to enabled. upsert_price_version preserves on None, but
    # defaults a new row to DISABLED — so for the new-model case we must pass
    # True explicitly to keep "Add new" models usable out of the box.
    resolved_enabled = is_enabled
    if resolved_enabled is None:
        existing = await db_client.get_active_price_by_model(model_identifier, kind=kind)
        if existing is None:
            resolved_enabled = True  # brand-new model → enabled on add
        # else leave as None so upsert preserves the current enabled state.

    row = await db_client.upsert_price_version(
        provider=provider,
        model_identifier=model_identifier,
        kind=kind,
        source=ModelPricingSource.MANUAL,
        unit=unit,
        rate=Decimal(rate),
        status=ModelPricingStatus.ACTIVE,
        is_enabled=resolved_enabled,
    )
    return _pricing_row_to_dict(row)


async def delete_model(pricing_id: int) -> dict:
    """Remove a pricing row (used for manual STT/TTS deletes)."""
    ok = await db_client.delete_pricing(pricing_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"deleted_id": pricing_id}


async def set_model_enabled(pricing_id: int, is_enabled: bool) -> dict:
    row = await db_client.set_model_enabled(pricing_id, is_enabled)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return _pricing_row_to_dict(row)


async def set_provider_enabled(provider: str, is_enabled: bool) -> dict:
    row = await db_client.set_provider_enabled(provider, is_enabled)
    return {"provider": row.provider, "is_enabled": row.is_enabled}


async def list_models() -> dict:
    """Return all current pricing rows + provider settings for the admin table."""
    rows = await db_client.list_pricing(current_only=True)
    providers = await db_client.list_provider_settings()
    return {
        "models": [_pricing_row_to_dict(r) for r in rows],
        "providers": [
            {"provider": p.provider, "is_enabled": p.is_enabled} for p in providers
        ],
    }


async def list_pending() -> dict:
    rows = await db_client.list_pending()
    return {"pending": [_pending_row_to_dict(r) for r in rows]}


def _pricing_row_to_dict(r) -> dict:
    return {
        "id": r.id,
        "provider": r.provider,
        "model_identifier": r.model_identifier,
        "kind": r.kind,
        "source": r.source,
        "unit": r.unit,
        "input_per_1m": r.input_per_1m,
        "output_per_1m": r.output_per_1m,
        "cached_input_per_1m": r.cached_input_per_1m,
        "embedding_per_1m": r.embedding_per_1m,
        "rt_text_input_per_1m": r.rt_text_input_per_1m,
        "rt_text_output_per_1m": r.rt_text_output_per_1m,
        "rt_audio_input_per_1m": r.rt_audio_input_per_1m,
        "rt_audio_output_per_1m": r.rt_audio_output_per_1m,
        "rate": r.rate,
        "status": r.status,
        "is_enabled": r.is_enabled,
        "effective_from": r.effective_from,
    }


def _pending_row_to_dict(r) -> dict:
    return {
        "id": r.id,
        "provider": r.provider,
        "model_identifier": r.model_identifier,
        "kind": r.kind,
        "change_type": r.change_type,
        "proposed_input_per_1m": r.proposed_input_per_1m,
        "proposed_output_per_1m": r.proposed_output_per_1m,
        "proposed_cached_input_per_1m": r.proposed_cached_input_per_1m,
        "proposed_embedding_per_1m": r.proposed_embedding_per_1m,
        "proposed_rt_text_input_per_1m": r.proposed_rt_text_input_per_1m,
        "proposed_rt_text_output_per_1m": r.proposed_rt_text_output_per_1m,
        "proposed_rt_audio_input_per_1m": r.proposed_rt_audio_input_per_1m,
        "proposed_rt_audio_output_per_1m": r.proposed_rt_audio_output_per_1m,
        "proposed_status": r.proposed_status,
        "current_input_per_1m": r.current_input_per_1m,
        "current_output_per_1m": r.current_output_per_1m,
        "current_cached_input_per_1m": r.current_cached_input_per_1m,
        "current_embedding_per_1m": r.current_embedding_per_1m,
        "current_rt_text_input_per_1m": r.current_rt_text_input_per_1m,
        "current_rt_text_output_per_1m": r.current_rt_text_output_per_1m,
        "current_rt_audio_input_per_1m": r.current_rt_audio_input_per_1m,
        "current_rt_audio_output_per_1m": r.current_rt_audio_output_per_1m,
        "detected_at": r.detected_at,
    }
