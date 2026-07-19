"""Knowledge Base aggregation tool for workflow execution.

This module provides server-side aggregation (COUNT, AVG, MAX, MIN, GROUP BY)
on the knowledge base metadata, enabling analytical queries like
"count cranes by manufacturer" or "average capacity by category".

Zero cost — pure PostgreSQL queries, no embeddings or LLM calls.
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from api.db import db_client


async def aggregate_knowledge_base(
    organization_id: int,
    document_uuids: Optional[List[str]] = None,
    group_by: Optional[str] = None,
    aggregate_field: Optional[str] = None,
    aggregate_function: str = "count",
    filters: Optional[Dict[str, Any]] = None,
    order_by: str = "desc",
    limit: int = 20,
) -> Dict[str, Any]:
    """Perform aggregation on knowledge base metadata.

    Args:
        organization_id: Organization ID for scoping.
        document_uuids: Optional document UUID filter.
        group_by: Field to group by (e.g., "manufacturer", "equipment_type").
        aggregate_field: Field to aggregate (e.g., "capacity_ton"). Required
            for avg/max/min/sum. Not needed for count.
        aggregate_function: One of "count", "avg", "max", "min", "sum".
        filters: Optional pre-filters before aggregation (same format as
            filter_knowledge_base).
        order_by: "desc" or "asc" for ordering results.
        limit: Max number of groups to return.

    Returns:
        Dictionary with aggregation results.
    """
    try:
        limit = min(max(1, limit), 50)

        # Validate aggregate_function
        valid_functions = ("count", "avg", "max", "min", "sum")
        if aggregate_function not in valid_functions:
            return {
                "error": f"Invalid aggregate_function. Must be one of: {valid_functions}",
                "results": [],
            }

        # For non-count aggregations, aggregate_field is required
        if aggregate_function != "count" and not aggregate_field:
            return {
                "error": f"aggregate_field is required for '{aggregate_function}' operations.",
                "results": [],
            }

        results = await db_client.aggregate_chunks_metadata(
            organization_id=organization_id,
            document_uuids=document_uuids,
            group_by=group_by,
            aggregate_field=aggregate_field,
            aggregate_function=aggregate_function,
            filters=filters,
            order_by=order_by,
            limit=limit,
        )

        logger.info(
            f"Knowledge base aggregation: group_by={group_by}, "
            f"fn={aggregate_function}({aggregate_field or '*'}), "
            f"results={len(results)}, org={organization_id}"
        )

        return {
            "results": results,
            "group_by": group_by,
            "aggregate_function": aggregate_function,
            "aggregate_field": aggregate_field,
            "total_groups": len(results),
        }

    except Exception as e:
        logger.error(f"Error in knowledge base aggregation: {e}")
        return {
            "error": str(e),
            "results": [],
        }


def get_knowledge_base_aggregate_tool(
    document_uuids: Optional[List[str]] = None,
    available_metadata_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get aggregation tool definition for LLM function calling."""
    if available_metadata_fields:
        fields_hint = (
            f" Available fields to group by or aggregate: "
            f"{', '.join(available_metadata_fields)}."
        )
    else:
        fields_hint = (
            " Common fields: manufacturer, parent_category, equipment_type, "
            "capacity_ton, sub_category."
        )

    description = (
        "Perform aggregation queries on the knowledge base (COUNT, AVG, MAX, MIN). "
        "Use this tool for analytical questions like 'count cranes by manufacturer', "
        "'average capacity per category', 'which manufacturer has the most cranes', "
        "'top 5 highest capacity cranes', 'how many cranes above 100 tons'. "
        "Do NOT use for listing specific items — use filter_knowledge_base for that."
        f"{fields_hint}"
    )

    return {
        "type": "function",
        "function": {
            "name": "aggregate_knowledge_base",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {
                        "type": "string",
                        "description": (
                            "Metadata field to group results by "
                            "(e.g., 'manufacturer', 'equipment_type', 'parent_category'). "
                            "Omit for a single aggregate across all data."
                        ),
                    },
                    "aggregate_function": {
                        "type": "string",
                        "enum": ["count", "avg", "max", "min", "sum"],
                        "description": (
                            "Aggregation function to apply. "
                            "'count' counts items per group. "
                            "'avg'/'max'/'min'/'sum' operate on aggregate_field."
                        ),
                    },
                    "aggregate_field": {
                        "type": "string",
                        "description": (
                            "Numeric metadata field to aggregate "
                            "(e.g., 'capacity_ton'). Required for avg/max/min/sum. "
                            "Not needed for count."
                        ),
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "Optional pre-filters before aggregation. Same format as "
                            "filter_knowledge_base filters."
                        ),
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": "Sort order for results. Default: desc.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max groups to return (default: 20).",
                    },
                },
                "required": ["aggregate_function"],
            },
        },
    }
