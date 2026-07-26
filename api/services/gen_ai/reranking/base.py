"""Abstract base class for reranking services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRerankingService(ABC):
    """Reranks a list of candidate chunks against a query.

    All reranking implementations must inherit from this class.
    The contract is simple: take a query + list of chunks, return the same
    list sorted by descending relevance score with a ``rerank_score`` field
    added to each chunk dict.
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Score and sort chunks by relevance to the query.

        Args:
            query: The original user query (NOT the expanded variants —
                reranking always uses the original intent).
            chunks: List of chunk dicts.  Each must have at least a ``text``
                or ``chunk_text`` key.
            top_n: If provided, return only the top-n chunks after reranking.
                If None, return all chunks sorted by score.

        Returns:
            Sorted list of chunk dicts (highest relevance first), each with
            a ``rerank_score`` float field added (0.0–1.0).
        """
        ...
