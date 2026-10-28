"""LiteLLM pricing catalog fetch + normalization (Stage B).

Chat and embedding rates are sourced from LiteLLM's public pricing registry
(a single JSON file). We fetch it over HTTP, filter to the curated chat
providers (+ the one embedding model), and normalize LiteLLM's per-token rates
to the per-1M-token unit the rest of the system uses.

STT/TTS are NOT handled here — those are entered manually by an admin.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from loguru import logger

from api.enums import ModelPricingKind

# Public LiteLLM pricing registry. Single source for chat + embedding rates.
LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Curated chat providers (Section 1 allow-list). LiteLLM tags each model with a
# `litellm_provider`; we keep only chat models from these.
CURATED_CHAT_PROVIDERS = {"openai", "anthropic", "gemini", "google", "groq"}

# Normalize LiteLLM provider names to our ServiceProviders values. LiteLLM uses
# "gemini" for Google AI Studio models; our provider key is "google".
_PROVIDER_ALIASES = {
    "gemini": "google",
}

# Speech-to-speech realtime providers we support (OpenAI + Google; Grok dropped
# per product decision). Only mode=="realtime" models from these are synced; the
# per-second-only providers and vertex/azure/bedrock duplicates are excluded.
CURATED_REALTIME_PROVIDERS = {"openai", "gemini"}

# The single embedding model we bill on (OpenAI). Its rate drives the embedding
# term in the billing formula.
EMBEDDING_PROVIDER = "openai"
EMBEDDING_MODEL = "text-embedding-3-small"

_MILLION = Decimal("1000000")


@dataclass
class CatalogEntry:
    """A normalized pricing entry fetched from LiteLLM."""

    provider: str
    model_identifier: str
    kind: ModelPricingKind
    input_per_1m: Optional[Decimal] = None
    output_per_1m: Optional[Decimal] = None
    cached_input_per_1m: Optional[Decimal] = None
    embedding_per_1m: Optional[Decimal] = None
    # Speech-to-speech realtime rates (per 1M tokens). Set only for realtime kind.
    rt_text_input_per_1m: Optional[Decimal] = None
    rt_text_output_per_1m: Optional[Decimal] = None
    rt_audio_input_per_1m: Optional[Decimal] = None
    rt_audio_output_per_1m: Optional[Decimal] = None


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _per_1m(per_token) -> Optional[Decimal]:
    """Convert a LiteLLM per-token cost to a per-1M-token rate."""
    d = _to_decimal(per_token)
    if d is None:
        return None
    return (d * _MILLION).quantize(Decimal("0.000001"))


def fetch_raw_catalog(url: str = LITELLM_PRICING_URL, timeout: int = 20) -> dict:
    """Fetch and parse the LiteLLM pricing JSON. Raises on network/parse error."""
    logger.info("Fetching LiteLLM pricing catalog from {}", url)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Unexpected LiteLLM catalog shape (expected a JSON object)")
    return data


def normalize_catalog(raw: dict) -> list[CatalogEntry]:
    """Filter + normalize the raw catalog to curated chat models + the embedding model.

    Returns one CatalogEntry per kept model with per-1M rates.
    """
    entries: list[CatalogEntry] = []

    for model_id, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        litellm_provider = meta.get("litellm_provider")
        mode = meta.get("mode")

        # Chat models from curated providers.
        if mode == "chat" and litellm_provider in CURATED_CHAT_PROVIDERS:
            provider = _PROVIDER_ALIASES.get(litellm_provider, litellm_provider)
            entries.append(
                CatalogEntry(
                    provider=provider,
                    model_identifier=_strip_provider_prefix(model_id),
                    kind=ModelPricingKind.CHAT,
                    input_per_1m=_per_1m(meta.get("input_cost_per_token")),
                    output_per_1m=_per_1m(meta.get("output_cost_per_token")),
                    cached_input_per_1m=_per_1m(meta.get("cache_read_input_token_cost")),
                )
            )
            continue

        # The single embedding model.
        if (
            mode == "embedding"
            and litellm_provider == EMBEDDING_PROVIDER
            and _strip_provider_prefix(model_id) == EMBEDDING_MODEL
        ):
            entries.append(
                CatalogEntry(
                    provider=EMBEDDING_PROVIDER,
                    model_identifier=EMBEDDING_MODEL,
                    kind=ModelPricingKind.EMBEDDING,
                    embedding_per_1m=_per_1m(meta.get("input_cost_per_token")),
                )
            )
            continue

        # Speech-to-speech realtime models from curated providers. These bill
        # text and audio tokens at separate rates, in and out (four rates).
        if mode == "realtime" and litellm_provider in CURATED_REALTIME_PROVIDERS:
            provider = _PROVIDER_ALIASES.get(litellm_provider, litellm_provider)
            entries.append(
                CatalogEntry(
                    provider=provider,
                    model_identifier=_strip_provider_prefix(model_id),
                    kind=ModelPricingKind.SPEECH_TO_SPEECH,
                    rt_text_input_per_1m=_per_1m(meta.get("input_cost_per_token")),
                    rt_text_output_per_1m=_per_1m(meta.get("output_cost_per_token")),
                    rt_audio_input_per_1m=_per_1m(meta.get("input_cost_per_audio_token")),
                    rt_audio_output_per_1m=_per_1m(meta.get("output_cost_per_audio_token")),
                )
            )
            continue

    # Dedupe by (provider, model_identifier, kind): LiteLLM lists some models
    # twice (bare id and "provider/"-prefixed). Keep the first occurrence.
    seen: set[tuple] = set()
    deduped: list[CatalogEntry] = []
    for e in entries:
        key = (e.provider, e.model_identifier, e.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    entries = deduped

    logger.info(
        "Normalized LiteLLM catalog: {} curated entries ({} chat, {} embedding, {} s2s)",
        len(entries),
        sum(1 for e in entries if e.kind == ModelPricingKind.CHAT),
        sum(1 for e in entries if e.kind == ModelPricingKind.EMBEDDING),
        sum(1 for e in entries if e.kind == ModelPricingKind.SPEECH_TO_SPEECH),
    )
    return entries


def _strip_provider_prefix(model_id: str) -> str:
    """LiteLLM sometimes keys models as 'provider/model'. Keep the model part."""
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def fetch_normalized_catalog() -> list[CatalogEntry]:
    """Fetch + normalize in one call."""
    return normalize_catalog(fetch_raw_catalog())
