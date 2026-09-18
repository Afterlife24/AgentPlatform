"""On-demand Value Lookup Service for Relational Dataset Tool.

Implements Requirement 13:
1. Returns distinct values for a column up to 200 values.
2. Supports optional case-insensitive substring search fragment (literal match).
3. For numeric/temporal columns, returns min, max, and distinct count.
4. Executes as Query Role under isolation controls.
5. Enforces per-turn limit of 5 invocations.
"""

from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text

from api.services.db_tables.schema_provisioner import SchemaProvisioner

MAX_LOOKUP_RESULTS = 200
MAX_VAL_LENGTH = 200
MAX_LOOKUPS_PER_TURN = 5


class ValueLookupService:
    """Provides targeted value lookup for omitted or high-cardinality columns."""

    @classmethod
    async def lookup_values(
        cls,
        organization_id: int,
        table_name: str,
        column_name: str,
        column_type: str,
        allowed_relations: Set[str],
        search_fragment: Optional[str] = None,
        turn_call_count: int = 0,
    ) -> Dict[str, Any]:
        """Fetch distinct values or numeric ranges for a column.

        Args:
            organization_id: Tenant ID.
            table_name: Authorized table or view name.
            column_name: Target column name.
            column_type: Inferred/assigned type of the column.
            allowed_relations: Allowlisted relations for the node.
            search_fragment: Optional substring filter.
            turn_call_count: Number of lookup calls already made in this conversation turn.

        Returns:
            Dict containing results or error message.
        """
        # 1. Turn limit check
        if turn_call_count >= MAX_LOOKUPS_PER_TURN:
            return {
                "success": False,
                "error": f"Per-turn lookup limit reached ({MAX_LOOKUPS_PER_TURN} calls maximum).",
            }

        # 2. Allowlist check
        if table_name.lower() not in {r.lower() for r in allowed_relations}:
            return {
                "success": False,
                "error": f"Table '{table_name}' is not in the authorized relations: {sorted(list(allowed_relations))}",
            }

        schema = SchemaProvisioner.get_org_schema_name(organization_id)
        engine = SchemaProvisioner.get_query_role_engine()

        try:
            async with engine.connect() as conn:
                # Scoped read-only session
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text(f'SET LOCAL search_path = "{schema}"'))
                await conn.execute(text("SET LOCAL statement_timeout = 2000"))

                # Numeric or Temporal: min, max, distinct count (Criterion 7)
                if column_type in ("integer", "numeric", "date", "timestamp"):
                    query = text(f"""
                        SELECT MIN("{column_name}")::text AS min_val,
                               MAX("{column_name}")::text AS max_val,
                               COUNT(DISTINCT "{column_name}") AS distinct_cnt
                        FROM "{schema}"."{table_name}"
                        WHERE "{column_name}" IS NOT NULL
                    """)
                    res = await conn.execute(query)
                    row = res.fetchone()
                    return {
                        "success": True,
                        "table": table_name,
                        "column": column_name,
                        "type": column_type,
                        "min": row[0] if row else None,
                        "max": row[1] if row else None,
                        "distinct_count": row[2] if row else 0,
                    }

                # Text / Boolean: distinct values list
                where_clause = f'WHERE "{column_name}" IS NOT NULL'
                params: Dict[str, Any] = {}

                if search_fragment and search_fragment.strip():
                    # Match literally, case-insensitively
                    frag = search_fragment.strip()
                    where_clause += f' AND "{column_name}" ILIKE :frag'
                    params["frag"] = f"%{frag}%"

                # Fetch distinct count
                count_query = text(f"""
                    SELECT COUNT(DISTINCT "{column_name}")
                    FROM "{schema}"."{table_name}"
                    {where_clause}
                """)
                res_cnt = await conn.execute(count_query, params)
                total_distinct = res_cnt.fetchone()[0] or 0

                # Fetch values
                data_query = text(f"""
                    SELECT DISTINCT "{column_name}"::text
                    FROM "{schema}"."{table_name}"
                    {where_clause}
                    ORDER BY 1 ASC
                    LIMIT {MAX_LOOKUP_RESULTS}
                """)
                res_data = await conn.execute(data_query, params)
                raw_values = [r[0] for r in res_data.fetchall()]

                processed_values = []
                for v in raw_values:
                    s_val = str(v)
                    if len(s_val) > MAX_VAL_LENGTH:
                        processed_values.append({
                            "value": s_val[:MAX_VAL_LENGTH],
                            "truncated": True,
                        })
                    else:
                        processed_values.append({
                            "value": s_val,
                            "truncated": False,
                        })

                return {
                    "success": True,
                    "table": table_name,
                    "column": column_name,
                    "type": column_type,
                    "values": processed_values,
                    "returned_count": len(processed_values),
                    "total_distinct_count": total_distinct,
                    "has_more": total_distinct > MAX_LOOKUP_RESULTS,
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to retrieve column values: {str(e)}",
            }
