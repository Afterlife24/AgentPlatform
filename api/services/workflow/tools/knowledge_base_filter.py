"""Knowledge Base metadata filtering tool for workflow execution.

This module provides structured metadata filtering on the knowledge base,
enabling exact-match and comparison queries (e.g., "all Terex cranes above 500 tons")
without relying on vector similarity search.

Works alongside the existing semantic search tool — the LLM chooses which
tool to invoke based on the user's question.
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger
from opentelemetry import trace

from api.db import db_client
from api.services.pipecat.tracing_config import ensure_tracing


async def filter_knowledge_base(
    organization_id: int,
    document_uuids: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    correlation_id: Optional[str] = None,
    tracing_context=None,
) -> Dict[str, Any]:
    """Filter knowledge base chunks by structured metadata fields."""
    if ensure_tracing() and tracing_context:
        try:
            tracer = trace.get_tracer("pipecat")
            with tracer.start_as_current_span(
                "knowledge_base_metadata_filter", context=tracing_context
            ) as span:
                span.set_attribute("langfuse.trace.public", True)
                span.set_attribute(
                    "gen_ai.operation.name", "knowledge_base_metadata_filter"
                )
                span.set_attribute("filter.organization_id", organization_id)
                if filters:
                    span.set_attribute("filter.criteria", json.dumps(filters))
                span.set_attribute("filter.limit", limit)

                result = await _perform_filter(
                    organization_id=organization_id,
                    document_uuids=document_uuids,
                    filters=filters,
                    limit=limit,
                )

                span.set_attribute(
                    "filter.results_count", result["total_results"]
                )
                if result.get("error"):
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, result["error"])
                    )
                return result
        except Exception as e:
            logger.debug(f"Tracing setup failed for metadata filter: {e}")

    return await _perform_filter(
        organization_id=organization_id,
        document_uuids=document_uuids,
        filters=filters,
        limit=limit,
    )


async def _perform_filter(
    organization_id: int,
    document_uuids: Optional[List[str]],
    filters: Optional[Dict[str, Any]],
    limit: int,
) -> Dict[str, Any]:
    """Perform the actual metadata filtering query with automatic fallbacks."""
    try:
        limit = min(max(1, limit), 50)

        if not filters:
            return {
                "error": (
                    "No filters provided. Use retrieve_from_knowledge_base "
                    "for semantic search."
                ),
                "results": [],
                "filters_applied": {},
                "total_results": 0,
            }

        # Layer 1: Direct metadata filter
        results = await db_client.filter_chunks_by_metadata(
            organization_id=organization_id,
            document_uuids=document_uuids,
            filters=filters,
            limit=limit,
        )

        # Layer 2: Field name fallback (category ↔ equipment_type etc.)
        if not results:
            fallback_results = await _try_field_fallbacks(
                organization_id=organization_id,
                document_uuids=document_uuids,
                filters=filters,
                limit=limit,
            )
            if fallback_results is not None:
                results = fallback_results

        # Layer 3: Full-text search fallback (searches chunk_text directly)
        if not results:
            text_results = await _try_text_fallback(
                organization_id=organization_id,
                document_uuids=document_uuids,
                filters=filters,
                limit=limit,
            )
            if text_results:
                results = text_results

        logger.info(
            f"Knowledge base metadata filter: filters={filters}, "
            f"results={len(results)}, org={organization_id}"
        )

        response: Dict[str, Any] = {
            "results": results,
            "filters_applied": filters,
            "total_results": len(results),
        }

        if not results:
            response["hint"] = (
                "No results matched the metadata filters. "
                "Try using retrieve_from_knowledge_base with a natural "
                "language query instead, or broaden the filter criteria."
            )

        return response

    except Exception as e:
        logger.error(f"Error filtering knowledge base: {e}")
        return {
            "error": str(e),
            "results": [],
            "filters_applied": filters or {},
            "total_results": 0,
        }


# ---------------------------------------------------------------------------
# Layer 2: Field name equivalence fallback
# ---------------------------------------------------------------------------

_CATEGORY_EQUIVALENT_FIELDS = (
    "category", "equipment_type", "sub_category", "parent_category",
)
_CAPACITY_EQUIVALENT_FIELDS = (
    "capacity_ton", "rated_capacity_ton",
)


async def _try_field_fallbacks(
    organization_id: int,
    document_uuids: Optional[List[str]],
    filters: Dict[str, Any],
    limit: int,
) -> Optional[List[dict]]:
    """Try alternative field names when the original filter returns 0 results."""
    category_fields = [f for f in filters if f in _CATEGORY_EQUIVALENT_FIELDS]
    capacity_fields = [f for f in filters if f in _CAPACITY_EQUIVALENT_FIELDS]

    if not category_fields and not capacity_fields:
        return None

    for original_field in category_fields:
        original_value = filters[original_field]
        for alt_field in _CATEGORY_EQUIVALENT_FIELDS:
            if alt_field == original_field:
                continue
            alt_filters = dict(filters)
            del alt_filters[original_field]
            alt_filters[alt_field] = original_value
            results = await db_client.filter_chunks_by_metadata(
                organization_id=organization_id,
                document_uuids=document_uuids,
                filters=alt_filters,
                limit=limit,
            )
            if results:
                logger.info(
                    f"Field fallback worked: {original_field} -> {alt_field} "
                    f"({len(results)} results)"
                )
                return results

    for original_field in capacity_fields:
        original_value = filters[original_field]
        for alt_field in _CAPACITY_EQUIVALENT_FIELDS:
            if alt_field == original_field:
                continue
            alt_filters = dict(filters)
            del alt_filters[original_field]
            alt_filters[alt_field] = original_value
            results = await db_client.filter_chunks_by_metadata(
                organization_id=organization_id,
                document_uuids=document_uuids,
                filters=alt_filters,
                limit=limit,
            )
            if results:
                logger.info(
                    f"Field fallback worked: {original_field} -> {alt_field} "
                    f"({len(results)} results)"
                )
                return results

    return None


# ---------------------------------------------------------------------------
# Layer 3: Universal text fallback
# ---------------------------------------------------------------------------


async def _try_text_fallback(
    organization_id: int,
    document_uuids: Optional[List[str]],
    filters: Dict[str, Any],
    limit: int,
) -> Optional[List[dict]]:
    """Universal text fallback for ANY industry/data type.

    Builds multiple search strategies from strict to relaxed and tries each
    until results are found. Works for cranes, restaurants, hospitals, etc.
    """
    strategies = _build_search_strategies(filters)

    for strategy_terms in strategies:
        if not strategy_terms:
            continue
        try:
            results = await db_client.search_chunks_by_text(
                organization_id=organization_id,
                document_uuids=document_uuids,
                search_terms=strategy_terms,
                limit=limit,
            )
            if results:
                logger.info(
                    f"Text fallback found {len(results)} results "
                    f"for terms: {strategy_terms}"
                )
                return results
        except Exception as e:
            logger.warning(f"Text fallback strategy failed: {e}")
            continue

    return None


def _build_search_strategies(filters: Dict[str, Any]) -> list[list[str]]:
    """Build search strategies ordered from most specific to least specific.

    Handles all possible filter shapes for any industry:
    - Strings: direct search terms
    - Numbers: search with unit context
    - Booleans: extract field name as search term
    - In-lists: try each value
    - Ranges (gt/lt): extract other string context
    """
    string_terms: list[str] = []
    numeric_exact: list[tuple[str, float]] = []
    boolean_true_fields: list[str] = []
    in_list_terms: list[str] = []

    for field, value in filters.items():
        if isinstance(value, bool):
            if value:
                boolean_true_fields.append(field)
        elif isinstance(value, str):
            string_terms.append(value)
        elif isinstance(value, (int, float)):
            numeric_exact.append((field, value))
        elif isinstance(value, dict):
            for op, op_value in value.items():
                if op == "in" and isinstance(op_value, list):
                    in_list_terms.extend(str(v) for v in op_value if v)

    strategies: list[list[str]] = []

    # Strategy 1: All string + in-list + numeric with context + boolean fields
    all_terms: list[str] = []
    all_terms.extend(string_terms)
    all_terms.extend(in_list_terms)

    for field, value in numeric_exact:
        num_str = str(int(value)) if value == int(value) else str(value)
        if value < 10:
            field_word = _field_to_search_word(field)
            if field_word:
                all_terms.append(num_str)
                all_terms.append(field_word)
            else:
                all_terms.append(num_str)
        else:
            all_terms.append(num_str)
            unit = _get_unit_for_field(field)
            if unit:
                all_terms.append(unit)

    for field in boolean_true_fields:
        word = _field_to_search_word(field)
        if word and len(word) > 2:
            all_terms.append(word)

    all_terms = _dedupe(all_terms)
    if all_terms:
        strategies.append(all_terms)

    # Strategy 2: Only string terms (drop numeric/boolean noise)
    if string_terms and len(all_terms) > len(string_terms):
        strategies.append(_dedupe(string_terms))

    # Strategy 3: Individual in-list terms (OR-style)
    if in_list_terms and not string_terms:
        for term in in_list_terms[:3]:
            strategies.append([term])

    # Strategy 4: Purely numeric — number + unit
    if not string_terms and not in_list_terms and not boolean_true_fields:
        if numeric_exact:
            field, value = numeric_exact[0]
            num_str = str(int(value)) if value == int(value) else str(value)
            unit = _get_unit_for_field(field)
            if unit:
                strategies.append([num_str, unit])
            strategies.append([num_str])

    # Strategy 5: Boolean field names alone
    if not string_terms and not numeric_exact and boolean_true_fields:
        for field in boolean_true_fields:
            word = _field_to_search_word(field)
            if word:
                strategies.append([word])

    return strategies


def _field_to_search_word(field: str) -> str:
    """Convert field name to a useful search word.

    truck_mounted → truck mounted
    capacity_ton → capacity
    parent_category → category
    veg → veg
    bhk → bhk
    """
    # Strip unit suffixes
    for suffix in ("_ton", "_kg", "_m", "_mm", "_kph", "_usd", "_inr", "_sqft"):
        if field.endswith(suffix):
            field = field[: -len(suffix)]
            break

    # Strip common prefixes
    for prefix in ("parent_", "rated_", "max_", "min_", "total_", "is_"):
        if field.startswith(prefix):
            field = field[len(prefix):]
            break

    readable = field.replace("_", " ").strip()

    # Skip overly generic words
    if readable.lower() in ("id", "type", "status", "name", "value", "data"):
        return ""

    return readable


def _get_unit_for_field(field: str) -> str:
    """Get contextual unit word for a numeric field."""
    f = field.lower()
    if "ton" in f or "capacity" in f:
        return "ton"
    elif "height" in f or f.endswith("_m"):
        return "m"
    elif "kg" in f or "weight" in f:
        return "kg"
    elif "speed" in f or "kph" in f:
        return "km"
    elif "bhk" in f:
        return "bhk"
    elif "price" in f:
        return ""  # Prices vary too much for text search
    return ""


def _dedupe(terms: list[str]) -> list[str]:
    """Deduplicate terms preserving order, case-insensitive."""
    seen: set[str] = set()
    result: list[str] = []
    for t in terms:
        low = t.lower().strip()
        if low and low not in seen:
            seen.add(low)
            result.append(t)
    return result


# ---------------------------------------------------------------------------
# Tool definition for LLM function calling
# ---------------------------------------------------------------------------


def get_knowledge_base_filter_tool(
    document_uuids: Optional[List[str]] = None,
    available_metadata_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get metadata filter tool definition for LLM function calling."""
    if available_metadata_fields:
        fields_hint = (
            f" Available filter fields: {', '.join(available_metadata_fields)}."
        )
    else:
        fields_hint = (
            " Common filter fields include: model, manufacturer, "
            "capacity_ton, rated_capacity_ton, parent_category, equipment_type, "
            "max_speed_kph, max_boom_length_m, drive_config, sub_category, "
            "crawler, all_terrain, rough_terrain, truck_mounted."
        )

    description = (
        "Filter the knowledge base by exact metadata fields. "
        "Use this tool for enumeration or factual queries like "
        "'list all Terex cranes', 'show mobile cranes above 500 tons', "
        "'how many rough terrain cranes do we have'. "
        "Do NOT use for subjective or reasoning queries — use "
        "retrieve_from_knowledge_base for those."
        f"{fields_hint}"
    )

    return {
        "type": "function",
        "function": {
            "name": "filter_knowledge_base",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": (
                            "Metadata filters to apply. Keys are field names, "
                            "values can be: "
                            'a string/number for exact match (e.g. {"manufacturer": "Terex"}), '
                            "or an object with operator: "
                            '{"gt": number} for greater-than, '
                            '{"lt": number} for less-than, '
                            '{"gte": number} for greater-or-equal, '
                            '{"lte": number} for less-or-equal, '
                            '{"in": [values]} for matching any in list. '
                            "Multiple filters are AND-combined."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of results to return (default: 20, max: 50). "
                            "Use a higher limit for 'list all' or enumeration queries."
                        ),
                    },
                },
                "required": ["filters"],
            },
        },
    }
