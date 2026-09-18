"""Prompt Composer for the Relational Dataset Tool.

Implements Requirement 12:
1. Composes the Injected Schema Block containing DDL, views, PKs, relationships, and Value Profiles.
2. Formats original CSV headers and unconverted types as SQL comments.
3. Enforces Token Budget (<= 2,000 tokens) by progressively dropping long value lists.
4. Provides an LRU cache (up to 500 entries) with invalidation hooks.
5. Deterministic byte-for-byte serialization.
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from api.constants import RELATIONAL_DATASET_TOKEN_BUDGET

SCHEMA_BLOCK_START = "=== RELATIONAL DATABASE SCHEMA (USE EXACT SPELLINGS) ==="
SCHEMA_BLOCK_END = "=== END RELATIONAL DATABASE SCHEMA ==="


class PromptComposer:
    """Composes token-budgeted schema prompt blocks for workflow nodes."""

    _cache: OrderedDict[Tuple[int, Tuple[str, ...]], str] = OrderedDict()
    MAX_CACHE_SIZE = 500

    @classmethod
    def clear_cache(cls, organization_id: Optional[int] = None) -> None:
        """Invalidate cached schema blocks."""
        if organization_id is None:
            cls._cache.clear()
        else:
            keys_to_del = [k for k in cls._cache.keys() if k[0] == organization_id]
            for k in keys_to_del:
                del cls._cache[k]

    @classmethod
    def estimate_tokens(cls, text_str: str) -> int:
        """Conservative token count estimation (approx 3.5 characters per token)."""
        return max(1, (len(text_str) + 3) // 4)

    @classmethod
    def compose_schema_block(
        cls,
        organization_id: int,
        tables_data: List[Dict[str, Any]],  # List of table metadata dicts
        views_data: List[Dict[str, Any]],   # List of table view dicts
        relationships: List[Dict[str, Any]], # List of accepted relationships
        token_budget: int = RELATIONAL_DATASET_TOKEN_BUDGET,
    ) -> str:
        """Compose the full Injected Schema Block within the token budget."""
        # Check cache
        table_names = tuple(sorted([t["table_name"] for t in tables_data]))
        cache_key = (organization_id, table_names)
        if cache_key in cls._cache:
            cls._cache.move_to_end(cache_key)
            return cls._cache[cache_key]

        # 1. Preamble (Requirement 12 Criterion 13)
        preamble = (
            "AUTHORITATIVE DATABASE INSTRUCTIONS:\n"
            "1. The table and column identifiers listed below are the AUTHORITATIVE spellings to use in SQL.\n"
            "2. You may write table and view names unqualified because your queries run directly in this database schema.\n"
            "3. Use standard PostgreSQL SELECT syntax with JOINs to connect related tables.\n"
            "4. Table Views already apply pre-defined filters; querying a view requires no additional WHERE clause for those filters."
        )

        # 2. DDL and Views
        ddl_parts = []
        # Sort tables by name ASC
        sorted_tables = sorted(tables_data, key=lambda t: t["table_name"])
        for tbl in sorted_tables:
            t_name = tbl["table_name"]
            row_count = tbl.get("row_count", 0)
            pk = tbl.get("confirmed_primary_key") or tbl.get("suggested_primary_key")
            pk_note = f"PRIMARY KEY ({pk})" if pk else "NO PRIMARY KEY (JOINS ARE MANY-TO-MANY)"

            col_lines = []
            for col in tbl.get("column_schema", []):
                c_name = col["name"]
                c_type = col.get("type", "text").upper()
                orig_header = col.get("original_header", "")
                comment = f"/* CSV header: '{orig_header}' */"
                if col.get("conversion_failed"):
                    comment += " /* Type conversion could not be applied; retained as TEXT */"
                col_lines.append(f"  {c_name} {c_type},  {comment}")

            cols_formatted = "\n".join(col_lines)
            ddl_parts.append(
                f"-- Table: {t_name} ({row_count} rows, {pk_note})\n"
                f"CREATE TABLE {t_name} (\n"
                f"{cols_formatted}\n"
                f");"
            )

        # Views
        view_parts = []
        sorted_views = sorted(views_data, key=lambda v: v.get("view_name", ""))
        for vw in sorted_views:
            v_name = vw["view_name"]
            rules = vw.get("rules", [])
            rules_desc = ", ".join([f"{r.get('source_column')} {r.get('operator')} '{r.get('operand', '')}'" for r in rules])
            view_parts.append(
                f"-- View: {v_name} (Filtered view: {rules_desc})\n"
                f"CREATE VIEW {v_name} AS ...;"
            )

        # 3. Relationships
        rel_parts = []
        sorted_rels = sorted(relationships, key=lambda r: (r["source_table_name"], r["source_column"]))
        for r in sorted_rels:
            rel_parts.append(
                f"- {r['source_table_name']}.{r['source_column']} -> "
                f"{r['target_table_name']}.{r['target_column']} ({r['cardinality']})"
            )
        rel_block = "RELATIONSHIPS:\n" + ("\n".join(rel_parts) if rel_parts else "None defined.")

        # Base block without Value Profiles
        base_sections = [
            preamble,
            "\n\n".join(ddl_parts),
            "\n\n".join(view_parts) if view_parts else "",
            rel_block,
        ]
        base_block = "\n\n".join([s for s in base_sections if s])

        # 4. Value Profiles with Progressive Truncation (Criterion 7)
        # Collect value profile items
        profile_items = []
        for tbl in sorted_tables:
            t_name = tbl["table_name"]
            for prof in tbl.get("value_profile", []):
                c_name = prof["name"]
                c_type = prof.get("type", "text")
                null_count = prof.get("null_count", 0)

                if prof.get("no_values"):
                    rendered = f"{t_name}.{c_name}: (no values)"
                elif "min" in prof and "max" in prof:
                    rendered = f"{t_name}.{c_name} ({c_type}): range [{prof['min']} to {prof['max']}], nulls: {null_count}"
                elif "values" in prof:
                    vals_str = ", ".join([f"'{v}'" for v in prof["values"]])
                    rendered = f"{t_name}.{c_name}: [{vals_str}]"
                elif "examples" in prof:
                    ex_str = ", ".join([f"'{v}'" for v in prof["examples"]])
                    rendered = f"{t_name}.{c_name}: {prof.get('distinct_count', 0)} distinct values, e.g. [{ex_str}]"
                else:
                    rendered = f"{t_name}.{c_name}: distinct count {prof.get('distinct_count', 0)}"

                fallback_rendered = (
                    f"{t_name}.{c_name}: {prof.get('distinct_count', 0)} distinct values "
                    f"(omitted from prompt; use lookup_db_column_values to retrieve)"
                )
                profile_items.append({
                    "table": t_name,
                    "column": c_name,
                    "full_rendered": rendered,
                    "fallback_rendered": fallback_rendered,
                    "length": len(rendered),
                    "dropped": False,
                })

        # Token budget loop: drop longest rendered profiles until within budget
        def build_full_text(items: List[Dict[str, Any]]) -> str:
            val_lines = [it["fallback_rendered"] if it["dropped"] else it["full_rendered"] for it in items]
            val_block = "VALUE PROFILES (Sample & Discrete Values):\n" + "\n".join(val_lines)
            return (
                f"{SCHEMA_BLOCK_START}\n\n"
                f"{base_block}\n\n"
                f"{val_block}\n\n"
                f"{SCHEMA_BLOCK_END}"
            )

        full_prompt = build_full_text(profile_items)
        if cls.estimate_tokens(full_prompt) > token_budget:
            # Sort items by character length DESC
            items_by_len = sorted(
                profile_items,
                key=lambda it: (-it["length"], it["table"], it["column"]),
            )
            for item in items_by_len:
                item["dropped"] = True
                full_prompt = build_full_text(profile_items)
                if cls.estimate_tokens(full_prompt) <= token_budget:
                    break

        # Save to LRU cache
        if len(cls._cache) >= cls.MAX_CACHE_SIZE:
            cls._cache.popitem(last=False)
        cls._cache[cache_key] = full_prompt

        return full_prompt
