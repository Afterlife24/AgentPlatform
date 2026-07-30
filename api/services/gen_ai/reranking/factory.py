"""Factory for building a reranking service from org config.

Currently only one implementation exists (LLMRerankingService), but the
factory pattern makes it trivial to add Cohere Rerank or a local
cross-encoder later — callers never need to change.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from .base import BaseRerankingService
from .llm_service import LLMRerankingService


def build_reranking_service(
    *,
    api_key: Optional[str],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Optional[BaseRerankingService]:
    """Build a reranking service if an API key is available.

    Args:
        api_key: LLM API key (reusing the org's existing LLM key — no
            separate reranking API key needed).
        model: Model to use for scoring.  Defaults to gpt-4o-mini inside
            LLMRerankingService if not supplied.
        base_url: Optional base URL override.

    Returns:
        A configured BaseRerankingService, or None if no API key is provided
        or if the key is a Dograh proxy key (which doesn't support scoring prompts).
    """
    # Reranking is disabled for ALL providers.
    # The LLM reranker added latency and inconsistency without reliable
    # quality gains (the Dograh proxy returns empty scores, and even with a
    # real key the extra LLM round-trips per query slowed responses). RRF
    # ordering from the hybrid dense + BM25 search is used directly instead.
    # To re-enable, restore the provider-specific construction below.
    logger.debug("Reranking disabled globally — using RRF order")
    return None
