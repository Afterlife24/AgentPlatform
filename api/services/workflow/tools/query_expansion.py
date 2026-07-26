"""Query expansion for RAG retrieval.

Generates alternative phrasings of the user's query before hitting the
index.  Running hybrid search over multiple query variants improves recall
— especially for short, ambiguous, or domain-specific queries where a
single phrasing may miss relevant chunks.

Strategy
--------
1. Fast path (no LLM key): return [original_query] — zero latency impact.
2. LLM path: ask the org's configured LLM for 2 alternative phrasings.
   Runs with a tight token budget and low temperature for speed + consistency.
3. Deduplication: the original query is always first; duplicates are dropped.

The result is a list of 1–3 queries.  The caller runs hybrid search for
each, then RRF-merges all candidate lists before reranking.
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

# Maximum alternative queries to generate (excluding the original).
_MAX_VARIANTS = 2

_SYSTEM_PROMPT = (
    "You are a search query optimizer for an equipment knowledge base. "
    "Given a user's question, produce alternative phrasings that would help "
    "retrieve relevant documents. "
    "IMPORTANT: Always keep specific model names, part numbers, and brand names "
    "exactly as they appear in the original query. "
    "Return ONLY a numbered list — one query per line, no explanation."
)

_USER_PROMPT_TEMPLATE = """\
Original query: {query}

Write {n} alternative search queries that ask for the same information \
using different words. KEEP all model names and brand names exactly as-is. \
Only rephrase the descriptive words around them. Be concise. Output ONLY \
the numbered queries:"""


async def expand_query(
    query: str,
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    num_variants: int = _MAX_VARIANTS,
) -> list[str]:
    """Return the original query plus up to `num_variants` LLM-generated variants.

    Args:
        query: The original user query.
        llm_api_key: API key for the LLM (OpenAI-compatible).
        llm_model: Model ID to use.  Defaults to gpt-4o-mini.
        llm_base_url: Optional base URL override.
        num_variants: How many alternative phrasings to generate.

    Returns:
        List starting with the original query, followed by unique variants.
        Minimum length is 1 (original only) when no LLM key is available or
        the LLM call fails.
    """
    if not query or not query.strip():
        return [query]

    if not llm_api_key:
        logger.debug("Query expansion skipped — no LLM API key")
        return [query]

    try:
        variants = await _call_llm(
            query=query,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            num_variants=num_variants,
        )
    except Exception as exc:
        logger.warning("Query expansion LLM call failed: {}", exc)
        return [query]

    # Deduplicate: keep original first, then unique non-empty variants
    seen: set[str] = {query.lower().strip()}
    result = [query]
    for v in variants:
        v = v.strip()
        if not v or v.lower() in seen:
            continue
        # Validate: reject variants that dropped key entities (model numbers,
        # brand names).  A variant is useless if it loses the specific entity
        # the user asked about — like "CKE1350" or "LTM1450".
        # Heuristic: any alphanumeric token ≥4 chars that looks like a model
        # number (contains both letters and digits) must be preserved.
        import re as _re
        model_tokens = _re.findall(r'\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{4,}\b', query)
        entity_preserved = all(
            tok.lower() in v.lower() for tok in model_tokens
        )
        if model_tokens and not entity_preserved:
            logger.debug(
                "Dropping variant '{}' — lost entity tokens {}", v[:60], model_tokens
            )
            continue
        seen.add(v.lower())
        result.append(v)

    logger.info(
        "Query expansion: '{}' → {} queries: {}",
        query,
        len(result),
        result,
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _call_llm(
    *,
    query: str,
    llm_api_key: str,
    llm_model: Optional[str],
    llm_base_url: Optional[str],
    num_variants: int,
) -> list[str]:
    """Call the LLM and parse the numbered list response."""
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
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT_TEMPLATE.format(
                    query=query, n=num_variants
                ),
            },
        ],
        temperature=0.3,
        max_tokens=200,
    )

    raw = response.choices[0].message.content or ""
    return _parse_numbered_list(raw)


def _parse_numbered_list(text: str) -> list[str]:
    """Extract items from a numbered list like '1. ...\n2. ...'."""
    lines = text.strip().splitlines()
    variants: list[str] = []
    for line in lines:
        # Strip leading number + punctuation: "1.", "1)", "1 -", etc.
        cleaned = re.sub(r"^\s*\d+[\.\)\-]\s*", "", line).strip()
        if cleaned:
            variants.append(cleaned)
    return variants
