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
    if not api_key:
        logger.debug(
            "No API key provided for reranking — skipping reranker construction"
        )
        return None

    # Dograh proxy keys (oss_sk_*) return empty responses for scoring prompts.
    # Skip reranking entirely for these — RRF ordering is sufficient.
    if api_key.startswith("oss_sk_"):
        logger.debug(
            "Dograh proxy key detected — skipping reranker (not supported)"
        )
        return None

    logger.debug(
        "Building LLM reranking service (model={})", model or "gpt-4o-mini"
    )
    return LLMRerankingService(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )
