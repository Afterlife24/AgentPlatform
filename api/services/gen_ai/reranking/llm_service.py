"""LLM-based reranking service.

Uses the org's configured LLM (OpenAI-compatible) as a cross-encoder:
for each candidate chunk we ask the model to score its relevance to the
query on a 0–10 scale, then sort descending.

Why LLM-based instead of a local sentence-transformer cross-encoder?
- No extra pip dependency (sentence-transformers pulls ~1 GB of weights).
- The API is already wired for the org's LLM config.
- gpt-4o-mini is fast and cheap enough for scoring 20 candidates.
- Falls back gracefully to RRF ordering if the LLM call fails.

Concurrency: all chunks are scored in parallel (bounded semaphore) so
latency ≈ one LLM call, not N sequential calls.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from loguru import logger

from .base import BaseRerankingService

# Parallel scoring requests per rerank call
_RERANK_CONCURRENCY = 10

_SYSTEM_PROMPT = (
    "You are a relevance scoring system. "
    "Given a query and a passage, respond with ONLY a number from 0 to 10. "
    "10 means the passage directly answers the query. "
    "0 means completely unrelated. "
    "Respond with ONLY the number, nothing else."
)

_USER_PROMPT_TEMPLATE = """\
Query: {query}

Passage: {passage}

Score:"""


class LLMRerankingService(BaseRerankingService):
    """Reranks chunks by asking the LLM to score each one against the query."""

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model or "gpt-4o-mini"
        self._base_url = base_url

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Score all chunks in parallel then sort by descending relevance.

        Falls back to the original order with rerank_score=0.0 if scoring
        fails entirely, so the caller always gets a usable list.
        """
        if not chunks:
            return chunks

        semaphore = asyncio.Semaphore(_RERANK_CONCURRENCY)

        async def _score_one(chunk: dict[str, Any]) -> dict[str, Any]:
            passage = (
                chunk.get("text")
                or chunk.get("contextualized_text")
                or chunk.get("chunk_text")
                or ""
            )
            if not passage.strip():
                return {**chunk, "rerank_score": 0.0}

            async with semaphore:
                try:
                    score = await self._call_llm(query=query, passage=passage)
                except Exception as exc:
                    logger.warning(
                        "Rerank scoring failed for chunk {}: {}",
                        chunk.get("id"),
                        exc,
                    )
                    score = 0.0

            return {**chunk, "rerank_score": score}

        scored = await asyncio.gather(*[_score_one(c) for c in chunks])

        # Sort descending by rerank score
        sorted_chunks = sorted(
            scored, key=lambda c: c.get("rerank_score", 0.0), reverse=True
        )

        logger.info(
            "Reranked {} chunks for query '{}' — top score: {:.2f}, bottom: {:.2f}",
            len(sorted_chunks),
            query[:60],
            sorted_chunks[0].get("rerank_score", 0.0) if sorted_chunks else 0.0,
            sorted_chunks[-1].get("rerank_score", 0.0) if sorted_chunks else 0.0,
        )

        if top_n is not None:
            return sorted_chunks[:top_n]
        return sorted_chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_llm(self, *, query: str, passage: str) -> float:
        """Ask the LLM for a 0-10 relevance score. Returns float 0.0–1.0."""
        from openai import AsyncOpenAI

        client_kwargs: dict = {"api_key": self._api_key}

        # Resolve Dograh proxy URL when no explicit base_url is provided.
        effective_base_url = self._base_url
        if not effective_base_url and self._api_key and self._api_key.startswith("oss_sk_"):
            from api.constants import MPS_API_URL
            effective_base_url = f"{MPS_API_URL}/api/v1/llm"

        if effective_base_url:
            client_kwargs["base_url"] = effective_base_url

        # Use "default" model name for Dograh proxy
        model = self._model
        if not self._base_url and self._api_key and self._api_key.startswith("oss_sk_"):
            model = "default"

        client = AsyncOpenAI(**client_kwargs)

        # Truncate passage — first 600 chars gives enough context.
        # Use 600 not 400 because JSON-chunked text has whitespace overhead.
        passage_trimmed = passage[:600]

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _USER_PROMPT_TEMPLATE.format(
                            query=query, passage=passage_trimmed
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=10,  # More room for the model to respond
            )
        except Exception as exc:
            logger.warning("Reranker LLM call failed: {}", exc)
            return 0.0

        raw = (response.choices[0].message.content or "").strip()

        # Extract first number (int or float) from the response
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if match:
            raw_score = min(10.0, max(0.0, float(match.group(1))))
            score = round(raw_score / 10.0, 2)  # Normalise to 0.0–1.0
            if score > 0:
                logger.debug(
                    "Reranker: '{}' → {} (raw='{}')", query[:30], score, raw[:10]
                )
            return score

        logger.warning(
            "Reranker non-numeric response: '{}' for query '{}'", raw[:80], query[:40]
        )
        return 0.0
