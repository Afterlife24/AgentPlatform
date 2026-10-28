"""Unit tests for the dynamic model-pricing system (Stages A–E).

These cover pure logic that needs no live catalog or DB:
  - LiteLLM catalog normalization (per-token → per-1M, provider filter/alias)
  - the sync diff (new / price_change / deprecated) with a mocked DB
  - the billing per-model rate selection + fallback

The DB client is mocked throughout so the tests are hermetic.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fastapi import HTTPException

from api.enums import (
    ModelPricingChangeType,
    ModelPricingKind,
    ModelPricingUnit,
)
from api.services.pricing import catalog_sync
from api.services.pricing import pricing_service as ps


# ─────────────────────────── catalog normalization ───────────────────────────


def test_normalize_filters_to_curated_chat_and_embedding():
    raw = {
        "gpt-4.1": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 8e-06,
            "cache_read_input_token_cost": 5e-07,
        },
        "gemini-2.5-flash": {
            "litellm_provider": "gemini",
            "mode": "chat",
            "input_cost_per_token": 3e-07,
            "output_cost_per_token": 1.2e-06,
        },
        "text-embedding-3-small": {
            "litellm_provider": "openai",
            "mode": "embedding",
            "input_cost_per_token": 2e-08,
        },
        # Should be excluded: not a curated provider.
        "mistral-large": {"litellm_provider": "mistral", "mode": "chat",
                          "input_cost_per_token": 1e-06, "output_cost_per_token": 3e-06},
        # Should be excluded: STT/TTS are manual, not from catalog.
        "deepgram/nova-2": {"litellm_provider": "deepgram", "mode": "audio_transcription",
                            "input_cost_per_second": 7e-05},
        # Should be excluded: a non-curated embedding model.
        "text-embedding-3-large": {"litellm_provider": "openai", "mode": "embedding",
                                   "input_cost_per_token": 1.3e-07},
    }
    entries = catalog_sync.normalize_catalog(raw)
    by_model = {(e.provider, e.model_identifier): e for e in entries}

    # Curated chat + the single embedding model only.
    assert ("openai", "gpt-4.1") in by_model
    assert ("google", "gemini-2.5-flash") in by_model  # gemini → google alias
    assert ("openai", "text-embedding-3-small") in by_model
    assert ("mistral", "mistral-large") not in by_model
    assert ("deepgram", "deepgram/nova-2") not in by_model
    assert ("openai", "text-embedding-3-large") not in by_model


def test_normalize_converts_per_token_to_per_1m():
    raw = {
        "gpt-4.1": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 2e-06,   # $2.00 / 1M
            "output_cost_per_token": 8e-06,  # $8.00 / 1M
        },
    }
    entry = catalog_sync.normalize_catalog(raw)[0]
    assert entry.input_per_1m == Decimal("2.000000")
    assert entry.output_per_1m == Decimal("8.000000")


def test_normalize_embedding_rate():
    raw = {
        "text-embedding-3-small": {
            "litellm_provider": "openai",
            "mode": "embedding",
            "input_cost_per_token": 2e-08,  # $0.02 / 1M
        },
    }
    entry = catalog_sync.normalize_catalog(raw)[0]
    assert entry.kind == ModelPricingKind.EMBEDDING
    assert entry.embedding_per_1m == Decimal("0.020000")


def test_normalize_realtime_openai_and_gemini_four_rates():
    """Speech-to-speech realtime models from openai/gemini are normalized with
    all four per-1M rates; gemini aliases to google; grok/xai is excluded."""
    raw = {
        "gpt-realtime": {
            "litellm_provider": "openai",
            "mode": "realtime",
            "input_cost_per_token": 4e-06,          # $4 / 1M text in
            "output_cost_per_token": 1.6e-05,       # $16 / 1M text out
            "input_cost_per_audio_token": 3.2e-05,  # $32 / 1M audio in
            "output_cost_per_audio_token": 6.4e-05, # $64 / 1M audio out
        },
        "gemini-3.1-flash-live-preview": {
            "litellm_provider": "gemini",
            "mode": "realtime",
            "input_cost_per_token": 7.5e-07,
            "output_cost_per_token": 4.5e-06,
            "input_cost_per_audio_token": 3e-06,
            "output_cost_per_audio_token": 1.2e-05,
        },
        # Excluded: xai/grok realtime (dropped per product decision).
        "xai/grok-voice-3.0": {
            "litellm_provider": "xai", "mode": "realtime",
            "input_cost_per_second": 1.3e-03,
        },
    }
    entries = catalog_sync.normalize_catalog(raw)
    by_model = {(e.provider, e.model_identifier): e for e in entries}

    assert ("xai", "grok-voice-3.0") not in by_model  # grok dropped

    oa = by_model[("openai", "gpt-realtime")]
    assert oa.kind == ModelPricingKind.SPEECH_TO_SPEECH
    assert oa.rt_text_input_per_1m == Decimal("4.000000")
    assert oa.rt_text_output_per_1m == Decimal("16.000000")
    assert oa.rt_audio_input_per_1m == Decimal("32.000000")
    assert oa.rt_audio_output_per_1m == Decimal("64.000000")

    gm = by_model[("google", "gemini-3.1-flash-live-preview")]  # gemini → google
    assert gm.kind == ModelPricingKind.SPEECH_TO_SPEECH
    assert gm.rt_audio_output_per_1m == Decimal("12.000000")


def test_normalize_realtime_dedupes_prefixed_duplicates():
    """LiteLLM lists realtime models twice (bare + 'provider/' prefixed); the
    normalizer keeps a single entry per (provider, model)."""
    raw = {
        "gpt-realtime": {
            "litellm_provider": "openai", "mode": "realtime",
            "input_cost_per_token": 4e-06, "output_cost_per_token": 1.6e-05,
            "input_cost_per_audio_token": 3.2e-05, "output_cost_per_audio_token": 6.4e-05,
        },
        "openai/gpt-realtime": {
            "litellm_provider": "openai", "mode": "realtime",
            "input_cost_per_token": 4e-06, "output_cost_per_token": 1.6e-05,
            "input_cost_per_audio_token": 3.2e-05, "output_cost_per_audio_token": 6.4e-05,
        },
    }
    entries = [e for e in catalog_sync.normalize_catalog(raw) if e.model_identifier == "gpt-realtime"]
    assert len(entries) == 1


# ─────────────────────────── sync diff logic ───────────────────────────


@pytest.mark.asyncio
async def test_run_sync_classifies_new_change_deprecated(monkeypatch):
    """Diff: a new model, a changed price, and a vanished model are classified."""
    catalog = [
        catalog_sync.CatalogEntry(
            provider="openai", model_identifier="gpt-4.1", kind=ModelPricingKind.CHAT,
            input_per_1m=Decimal("2.0"), output_per_1m=Decimal("8.0"),
        ),
        catalog_sync.CatalogEntry(
            provider="openai", model_identifier="gpt-5", kind=ModelPricingKind.CHAT,
            input_per_1m=Decimal("5.0"), output_per_1m=Decimal("15.0"),
        ),
    ]
    monkeypatch.setattr(ps, "fetch_normalized_catalog", lambda: catalog)

    # Live DB: gpt-4.1 exists with a DIFFERENT output rate (→ price_change),
    # and claude-old exists but isn't in the catalog (→ deprecated).
    live_rows = [
        SimpleNamespace(provider="openai", model_identifier="gpt-4.1",
                        kind=ModelPricingKind.CHAT.value, source="litellm",
                        input_per_1m=Decimal("2.0"), output_per_1m=Decimal("7.0"),
                        cached_input_per_1m=None, embedding_per_1m=None,
                        rt_text_input_per_1m=None, rt_text_output_per_1m=None,
                        rt_audio_input_per_1m=None, rt_audio_output_per_1m=None,
                        status="active"),
        SimpleNamespace(provider="anthropic", model_identifier="claude-old",
                        kind=ModelPricingKind.CHAT.value, source="litellm",
                        input_per_1m=Decimal("3.0"), output_per_1m=Decimal("15.0"),
                        cached_input_per_1m=None, embedding_per_1m=None,
                        rt_text_input_per_1m=None, rt_text_output_per_1m=None,
                        rt_audio_input_per_1m=None, rt_audio_output_per_1m=None,
                        status="active"),
    ]

    added = []

    async def fake_list_pricing(current_only=True, kind=None):
        return live_rows

    async def fake_clear_pending():
        return 0

    async def fake_add_pending(**kwargs):
        added.append(kwargs)

    async def fake_models_in_use():
        return set()  # nothing auto-approved in this test

    status_calls = []

    async def fake_set_status(**kwargs):
        status_calls.append(kwargs)

    monkeypatch.setattr(ps.db_client, "list_pricing", fake_list_pricing)
    monkeypatch.setattr(ps.db_client, "clear_pending", fake_clear_pending)
    monkeypatch.setattr(ps.db_client, "add_pending", fake_add_pending)
    monkeypatch.setattr(ps.db_client, "set_model_status", fake_set_status)
    monkeypatch.setattr(ps, "_models_in_use", fake_models_in_use)

    result = await ps.run_sync()

    assert result["new"] == 1          # gpt-5
    assert result["price_change"] == 1  # gpt-4.1 output 7→8
    # Vanished catalog models are now flagged MISSING (status-only), not queued
    # as a DEPRECATED pending entry.
    assert result["missing"] == 1       # claude-old gone from catalog

    change_types = {(a["model_identifier"], a["change_type"].value if hasattr(a["change_type"], "value") else a["change_type"]) for a in added}
    assert ("gpt-5", ModelPricingChangeType.NEW.value) in change_types
    assert ("gpt-4.1", ModelPricingChangeType.PRICE_CHANGE.value) in change_types

    # claude-old was reconciled to MISSING via set_model_status.
    missing_ids = {c["model_identifier"] for c in status_calls}
    assert "claude-old" in missing_ids


@pytest.mark.asyncio
async def test_run_sync_auto_approves_in_use_model(monkeypatch):
    """A model AgentX currently uses is committed live (not queued as pending)."""
    catalog = [
        catalog_sync.CatalogEntry(
            provider="openai", model_identifier="gpt-4.1", kind=ModelPricingKind.CHAT,
            input_per_1m=Decimal("2.0"), output_per_1m=Decimal("8.0"),
        ),
    ]
    monkeypatch.setattr(ps, "fetch_normalized_catalog", lambda: catalog)

    upserts = []

    async def fake_list_pricing(current_only=True, kind=None):
        return []  # empty DB

    async def fake_clear_pending():
        return 0

    async def fake_add_pending(**kwargs):
        raise AssertionError("in-use model must NOT be queued as pending")

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)
        return SimpleNamespace(id=1)

    async def fake_models_in_use():
        return {("openai", "gpt-4.1")}

    monkeypatch.setattr(ps.db_client, "list_pricing", fake_list_pricing)
    monkeypatch.setattr(ps.db_client, "clear_pending", fake_clear_pending)
    monkeypatch.setattr(ps.db_client, "add_pending", fake_add_pending)
    monkeypatch.setattr(ps.db_client, "upsert_price_version", fake_upsert)
    monkeypatch.setattr(ps, "_models_in_use", fake_models_in_use)

    result = await ps.run_sync()

    assert result["auto_approved"] == 1
    assert result["new"] == 0
    assert len(upserts) == 1
    assert upserts[0]["model_identifier"] == "gpt-4.1"
    assert upserts[0]["is_enabled"] is True  # auto-enabled


# ──────────── manual STT/TTS: unit derivation + server-side bounds ────────────


def _capture_upsert(monkeypatch):
    """Stub upsert_price_version, returning the kwargs it was called with."""
    captured = {}

    async def fake_upsert(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=1,
            provider=kwargs["provider"],
            model_identifier=kwargs["model_identifier"],
            kind=kwargs["kind"].value if hasattr(kwargs["kind"], "value") else kwargs["kind"],
            source=kwargs["source"].value if hasattr(kwargs["source"], "value") else kwargs["source"],
            unit=kwargs["unit"].value if hasattr(kwargs["unit"], "value") else kwargs["unit"],
            input_per_1m=None, output_per_1m=None, cached_input_per_1m=None,
            embedding_per_1m=None,
            rt_text_input_per_1m=None, rt_text_output_per_1m=None,
            rt_audio_input_per_1m=None, rt_audio_output_per_1m=None,
            rate=kwargs.get("rate"),
            status="active", is_enabled=kwargs["is_enabled"], effective_from=None,
        )

    monkeypatch.setattr(ps.db_client, "upsert_price_version", fake_upsert)
    return captured


@pytest.mark.asyncio
async def test_add_manual_tts_stores_per_1m_char_unit(monkeypatch):
    """A TTS model is stored with the per_1m_char unit (spec Option A)."""
    captured = _capture_upsert(monkeypatch)
    await ps.add_manual_model(
        provider="cartesia", model_identifier="sonic-3",
        kind=ModelPricingKind.TTS, rate=Decimal("50.00"), is_enabled=True,
    )
    assert captured["unit"] == ModelPricingUnit.PER_1M_CHAR
    assert captured["rate"] == Decimal("50.00")


@pytest.mark.asyncio
async def test_add_manual_stt_stores_per_hour_unit(monkeypatch):
    """An STT model is stored with the per_hour unit (spec Option A)."""
    captured = _capture_upsert(monkeypatch)
    await ps.add_manual_model(
        provider="cartesia", model_identifier="ink-2",
        kind=ModelPricingKind.STT, rate=Decimal("0.54"), is_enabled=True,
    )
    assert captured["unit"] == ModelPricingUnit.PER_HOUR
    assert captured["rate"] == Decimal("0.54")


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [Decimal("0.5"), Decimal("600")])
async def test_add_manual_tts_rejects_out_of_bounds(monkeypatch, rate):
    """TTS rate outside 1–500 USD/1M chars is rejected (catches a 1000x slip)."""
    _capture_upsert(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await ps.add_manual_model(
            provider="cartesia", model_identifier="sonic-3",
            kind=ModelPricingKind.TTS, rate=rate, is_enabled=True,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [Decimal("0.001"), Decimal("25")])
async def test_add_manual_stt_rejects_out_of_bounds(monkeypatch, rate):
    """STT rate outside 0.01–20 USD/hour is rejected."""
    _capture_upsert(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await ps.add_manual_model(
            provider="cartesia", model_identifier="ink-2",
            kind=ModelPricingKind.STT, rate=rate, is_enabled=True,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_add_manual_rejects_non_speech_kind(monkeypatch):
    """Only tts/stt may be added manually; chat/embedding come from the catalog."""
    _capture_upsert(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await ps.add_manual_model(
            provider="openai", model_identifier="gpt-4.1",
            kind=ModelPricingKind.CHAT, rate=Decimal("2.0"), is_enabled=True,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_add_manual_rejects_non_positive_rate(monkeypatch):
    """A zero or negative rate is rejected before the bounds check."""
    _capture_upsert(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await ps.add_manual_model(
            provider="cartesia", model_identifier="sonic-3",
            kind=ModelPricingKind.TTS, rate=Decimal("0"), is_enabled=True,
        )
    assert exc.value.status_code == 400


# ───────── manual STT/TTS: re-pricing preserves enabled state (dropdown flow) ─────────


@pytest.mark.asyncio
async def test_repricing_existing_disabled_model_preserves_disabled(monkeypatch):
    """Changing the rate of an existing DISABLED model must not re-enable it.

    The admin "change rate" flow sends no is_enabled. The model already exists
    (and is disabled), so upsert must receive is_enabled=None to preserve state.
    """
    captured = _capture_upsert(monkeypatch)
    # Model already exists and is currently disabled.
    monkeypatch.setattr(
        ps.db_client, "get_active_price_by_model",
        AsyncMock(return_value=SimpleNamespace(id=9, is_enabled=False, rate=Decimal("50"))),
    )
    await ps.add_manual_model(
        provider="cartesia", model_identifier="sonic-3",
        kind=ModelPricingKind.TTS, rate=Decimal("60.00"),  # is_enabled omitted → None
    )
    # None is forwarded so upsert_price_version preserves the existing state.
    assert captured["is_enabled"] is None
    assert captured["rate"] == Decimal("60.00")


@pytest.mark.asyncio
async def test_repricing_existing_enabled_model_preserves_enabled(monkeypatch):
    """Re-pricing an existing ENABLED model also leaves state to upsert (None)."""
    captured = _capture_upsert(monkeypatch)
    monkeypatch.setattr(
        ps.db_client, "get_active_price_by_model",
        AsyncMock(return_value=SimpleNamespace(id=10, is_enabled=True, rate=Decimal("50"))),
    )
    await ps.add_manual_model(
        provider="cartesia", model_identifier="sonic-3",
        kind=ModelPricingKind.TTS, rate=Decimal("45.00"),
    )
    assert captured["is_enabled"] is None


@pytest.mark.asyncio
async def test_adding_brand_new_model_defaults_enabled(monkeypatch):
    """A brand-new model (no existing row) with no is_enabled defaults to enabled."""
    captured = _capture_upsert(monkeypatch)
    monkeypatch.setattr(
        ps.db_client, "get_active_price_by_model",
        AsyncMock(return_value=None),  # not in the table yet
    )
    await ps.add_manual_model(
        provider="deepgram", model_identifier="aura-brand-new",
        kind=ModelPricingKind.TTS, rate=Decimal("20.00"),
    )
    assert captured["is_enabled"] is True


@pytest.mark.asyncio
async def test_explicit_is_enabled_false_forces_disabled(monkeypatch):
    """An explicit is_enabled=False is honored (no existing-row lookup needed)."""
    captured = _capture_upsert(monkeypatch)
    # get_active_price_by_model must NOT be consulted when is_enabled is explicit.
    monkeypatch.setattr(
        ps.db_client, "get_active_price_by_model",
        AsyncMock(side_effect=AssertionError("should not be called")),
    )
    await ps.add_manual_model(
        provider="cartesia", model_identifier="sonic-3",
        kind=ModelPricingKind.TTS, rate=Decimal("50.00"), is_enabled=False,
    )
    assert captured["is_enabled"] is False
