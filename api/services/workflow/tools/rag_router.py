"""Deterministic RAG query router.

Classifies an incoming query into one of four routes BEFORE retrieval,
so the right pipeline branch is taken without relying on the LLM to pick
a tool at runtime.

Routes
------
- ``semantic``        — full hybrid-search + rerank pipeline (default)
- ``metadata_filter`` — exact/comparison query (list all X above Y tons)
- ``aggregation``     — count/avg/max/min/group-by query
- ``out_of_scope``    — clearly not answerable from the knowledge base

Classification strategy
-----------------------
1. Fast regex/keyword rules (zero latency, covers ~80 % of queries).
2. Optional LLM fallback for ambiguous cases when an API key is available.
   Falls back to ``semantic`` if the LLM call fails or is not configured.

Only the route name is returned — the caller decides what to do with it.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from loguru import logger


class RAGRoute(str, Enum):
    """Possible routing outcomes."""

    SEMANTIC = "semantic"
    METADATA_FILTER = "metadata_filter"
    AGGREGATION = "aggregation"
    OUT_OF_SCOPE = "out_of_scope"


# ---------------------------------------------------------------------------
# Regex pattern banks  (compiled once at import time)
# ---------------------------------------------------------------------------

# Aggregation signals: count, average, maximum, minimum, sum, group by
_AGGREGATION_PATTERNS = re.compile(
    r"\b("
    r"how many|count|total number|number of|"
    r"average|avg|mean|"
    r"maximum|max|highest|largest|biggest|"
    r"minimum|min|lowest|smallest|"
    r"sum|total|"
    r"group by|breakdown|distribution|"
    r"list all|show all|give me all|enumerate"
    r")\b",
    re.IGNORECASE,
)

# Metadata / exact-match filter signals: comparisons and field-specific terms
_METADATA_FILTER_PATTERNS = re.compile(
    r"\b("
    r"above|below|more than|less than|greater than|at least|at most|"
    r"between .* and|"
    r"exactly|equal to|equals|"
    r"capacity|tonnage|model|make|manufacturer|brand|"
    r"type of|category|series|version|"
    r"where .* is|filter by|only .* with|show .* with"
    r")\b",
    re.IGNORECASE,
)

# Out-of-scope signals: clearly conversational / non-retrieval
_OUT_OF_SCOPE_PATTERNS = re.compile(
    r"\b("
    r"hello|hi there|good morning|good afternoon|good evening|"
    r"how are you|what's up|hey|"
    r"thank you|thanks|bye|goodbye|see you|"
    r"what is your name|who are you|what can you do|"
    r"joke|tell me a story|write a poem|"
    r"weather|stock price|news|sports score"
    r")\b",
    re.IGNORECASE,
)

# Aggregation-specific numeric comparison that also often implies metadata filter
_NUMERIC_COMPARISON = re.compile(
    r"\b\d+\s*(ton|kg|lb|meter|foot|feet|kw|hp|mph|km)\b",
    re.IGNORECASE,
)

# LLM prompt for ambiguous classification
_ROUTER_SYSTEM_PROMPT = (
    "You are a query classifier for a knowledge base search system. "
    "Classify the query into exactly one of these categories:\n"
    "- semantic: general knowledge question, needs full-text search\n"
    "- metadata_filter: asks to filter items by specific field values or ranges\n"
    "- aggregation: asks to count, average, max, min, or group items\n"
    "- out_of_scope: chitchat, greetings, or unrelated to the knowledge base\n\n"
    "Output ONLY the category name, nothing else."
)


async def route_query(
    query: str,
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
) -> RAGRoute:
    """Classify a query into a RAGRoute.

    Args:
        query: The raw user query.
        llm_api_key: Optional LLM key for ambiguous fallback.
        llm_model: Optional model override.
        llm_base_url: Optional base URL override.

    Returns:
        A RAGRoute value.  Defaults to RAGRoute.SEMANTIC if classification
        is uncertain or the LLM call fails.
    """
    if not query or not query.strip():
        return RAGRoute.SEMANTIC

    query_stripped = query.strip()

    # --- Fast path: deterministic regex rules ---
    route = _classify_by_rules(query_stripped)
    if route is not None:
        logger.debug("RAG router (rules): '{}' → {}", query_stripped[:60], route)
        return route

    # --- Slow path: LLM fallback for ambiguous queries ---
    if llm_api_key:
        try:
            route = await _classify_by_llm(
                query=query_stripped,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                llm_base_url=llm_base_url,
            )
            logger.debug("RAG router (LLM): '{}' → {}", query_stripped[:60], route)
            return route
        except Exception as exc:
            logger.warning("RAG router LLM fallback failed: {}", exc)

    logger.debug(
        "RAG router: no rule matched, defaulting to semantic for '{}'",
        query_stripped[:60],
    )
    return RAGRoute.SEMANTIC


# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------

def _classify_by_rules(query: str) -> Optional[RAGRoute]:
    """Return a route if the query clearly matches a pattern, else None."""

    # Out-of-scope check first — fastest exit
    if _OUT_OF_SCOPE_PATTERNS.search(query) and len(query.split()) <= 8:
        # Short greeting / chit-chat
        return RAGRoute.OUT_OF_SCOPE

    agg_match = _AGGREGATION_PATTERNS.search(query)
    filter_match = _METADATA_FILTER_PATTERNS.search(query)
    numeric_match = _NUMERIC_COMPARISON.search(query)

    # Aggregation takes priority when both signals present with a count verb
    agg_verbs = re.search(
        r"\b(how many|count|total|average|avg|maximum|minimum|sum|group)\b",
        query,
        re.IGNORECASE,
    )
    if agg_match and agg_verbs:
        return RAGRoute.AGGREGATION

    # Metadata filter: comparison operator + a specific field or numeric
    if filter_match and (numeric_match or re.search(
        r"\b(above|below|more than|less than|at least|at most|between)\b",
        query,
        re.IGNORECASE,
    )):
        return RAGRoute.METADATA_FILTER

    # "list all X" without aggregation verbs → metadata filter
    if re.search(r"\b(list all|show all|give me all)\b", query, re.IGNORECASE):
        return RAGRoute.METADATA_FILTER

    # No strong signal — ambiguous, let LLM decide (or default to semantic)
    return None


# ---------------------------------------------------------------------------
# LLM-based fallback classifier
# ---------------------------------------------------------------------------

async def _classify_by_llm(
    *,
    query: str,
    llm_api_key: str,
    llm_model: Optional[str],
    llm_base_url: Optional[str],
) -> RAGRoute:
    """Ask the LLM to classify the query. Returns RAGRoute.SEMANTIC on parse error."""
    from openai import AsyncOpenAI

    model = llm_model or "gpt-4o-mini"
    client_kwargs: dict = {"api_key": llm_api_key}

    # Resolve Dograh proxy URL when no explicit base_url is provided
    effective_base_url = llm_base_url
    if not effective_base_url and llm_api_key and llm_api_key.startswith("oss_sk_"):
        from api.constants import MPS_API_URL
        effective_base_url = f"{MPS_API_URL}/api/v1/llm"
        model = llm_model or "default"

    if effective_base_url:
        client_kwargs["base_url"] = effective_base_url

    client = AsyncOpenAI(**client_kwargs)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {query}"},
        ],
        temperature=0.0,
        max_tokens=10,
    )

    raw = (response.choices[0].message.content or "").strip().lower()

    _ROUTE_MAP = {
        "semantic": RAGRoute.SEMANTIC,
        "metadata_filter": RAGRoute.METADATA_FILTER,
        "aggregation": RAGRoute.AGGREGATION,
        "out_of_scope": RAGRoute.OUT_OF_SCOPE,
    }
    return _ROUTE_MAP.get(raw, RAGRoute.SEMANTIC)
