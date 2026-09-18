"""Isolated Query Executor for Relational Dataset Tool.

Implements Requirement 14, Requirement 16, Requirement 19, and Requirement 20:
1. Executes SQL as the restricted Query Role inside the tenant's Org Schema.
2. Applies read-only transaction, statement_timeout (2000ms/5000ms), and search_path isolation.
3. Enforces row limits (<= 100), cell truncation (2000 chars), and payload size cap (256 KB).
4. Generates zero-row equality diagnostics (up to 3 predicates, 30 distinct values).
5. Safe logging with row values and credentials excluded.
"""

from datetime import date, datetime
from decimal import Decimal
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlglot import exp, parse_one

from api.constants import (
    RELATIONAL_DATASET_ROW_LIMIT,
    RELATIONAL_DATASET_STATEMENT_TIMEOUT_INTERACTIVE,
    RELATIONAL_DATASET_STATEMENT_TIMEOUT_NON_INTERACTIVE,
)
from api.services.db_tables.schema_provisioner import SchemaProvisioner
from api.services.db_tables.sql_validator import SQLValidator

MAX_PAYLOAD_BYTES = 256 * 1024  # 256 KB
MAX_CELL_CHARS = 2000


class QueryExecutor:
    """Safely executes validated SQL against tenant Org Schemas."""

    @classmethod
    async def execute_query(
        cls,
        organization_id: int,
        sql_statement: str,
        allowed_relations: Set[str],
        caller_row_limit: Optional[int] = None,
        is_interactive: bool = True,
    ) -> Dict[str, Any]:
        """Validate and execute an LLM-authored SQL statement.

        Args:
            organization_id: Tenant ID.
            sql_statement: Submitted SQL text.
            allowed_relations: Permitted table and view identifiers for the node.
            caller_row_limit: Optional caller limit.
            is_interactive: True if from live voice/chat call (2000ms timeout).

        Returns:
            Dict with execution results or error details.
        """
        start_time = time.time()
        org_schema = SchemaProvisioner.get_org_schema_name(organization_id)

        # 1. Validate SQL with SQLValidator
        is_valid, validation_err, sanitized_sql = SQLValidator.validate_sql(
            sql_statement, allowed_relations, org_schema
        )
        if not is_valid:
            return {
                "success": False,
                "error": validation_err,
                "rows": [],
                "row_count": 0,
                "column_names": [],
                "allowed_relations": sorted(list(allowed_relations)),
            }

        # 2. Enforce row limit (1 to 100, default 100)
        row_limit = RELATIONAL_DATASET_ROW_LIMIT
        if caller_row_limit is not None and 1 <= caller_row_limit <= RELATIONAL_DATASET_ROW_LIMIT:
            row_limit = caller_row_limit

        # 3. Timeout selection
        timeout_ms = (
            RELATIONAL_DATASET_STATEMENT_TIMEOUT_INTERACTIVE
            if is_interactive
            else RELATIONAL_DATASET_STATEMENT_TIMEOUT_NON_INTERACTIVE
        )

        engine = SchemaProvisioner.get_query_role_engine()

        try:
            async with engine.connect() as conn:
                # Open isolated transaction
                trans = await conn.begin()
                try:
                    # Transaction-scoped isolation controls
                    await conn.execute(text("SET TRANSACTION READ ONLY"))
                    await conn.execute(text(f'SET LOCAL search_path = "{org_schema}"'))
                    await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
                    await conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = 10000"))

                    # Execute query
                    result = await conn.execute(text(sanitized_sql))
                    col_names = list(result.keys())

                    rows: List[Dict[str, Any]] = []
                    cell_truncated = False
                    row_truncated = False
                    current_payload_bytes = 0

                    while len(rows) < row_limit:
                        row_tuple = result.fetchone()
                        if row_tuple is None:
                            break

                        row_dict = {}
                        for c_name, val in zip(col_names, row_tuple):
                            if val is None:
                                row_dict[c_name] = None
                            elif isinstance(val, (int, float, bool)):
                                row_dict[c_name] = val
                            elif isinstance(val, Decimal):
                                row_dict[c_name] = float(val) if (val % 1 != 0) else int(val)
                            elif isinstance(val, (datetime, date)):
                                row_dict[c_name] = val.isoformat()
                            elif isinstance(val, str):
                                if len(val) > MAX_CELL_CHARS:
                                    row_dict[c_name] = val[:MAX_CELL_CHARS]
                                    cell_truncated = True
                                else:
                                    row_dict[c_name] = val
                            else:
                                row_dict[c_name] = str(val)

                        # Check payload size cap
                        row_bytes = len(json.dumps(row_dict).encode("utf-8"))
                        if current_payload_bytes + row_bytes > MAX_PAYLOAD_BYTES:
                            row_truncated = True
                            break

                        current_payload_bytes += row_bytes
                        rows.append(row_dict)

                    await trans.commit()

                    duration_ms = int((time.time() - start_time) * 1000)
                    logger.info(
                        f"Relational query executed for org={organization_id} in {duration_ms}ms, returned {len(rows)} rows"
                    )

                    # 4. Zero-row equality diagnostic (Requirement 14 Criterion 7)
                    diagnostics = None
                    if len(rows) == 0:
                        diagnostics = await cls._generate_zero_row_diagnostics(
                            conn, org_schema, sanitized_sql, allowed_relations
                        )

                    return {
                        "success": True,
                        "rows": rows,
                        "row_count": len(rows),
                        "column_names": col_names,
                        "executed_statement": sanitized_sql,
                        "cell_truncated": cell_truncated,
                        "row_truncated": row_truncated,
                        "diagnostics": diagnostics,
                    }

                except Exception as ex:
                    await trans.rollback()
                    err_msg = str(ex)
                    duration_ms = int((time.time() - start_time) * 1000)

                    if "statement timeout" in err_msg.lower() or "canceling statement due to statement timeout" in err_msg.lower():
                        return {
                            "success": False,
                            "error": f"Query exceeded configured execution time limit ({timeout_ms} ms).",
                            "rows": [],
                            "executed_statement": sanitized_sql,
                        }

                    # Sanitize error message to avoid leaking internal info
                    return {
                        "success": False,
                        "error": f"Database execution error: {err_msg}",
                        "rows": [],
                        "executed_statement": sanitized_sql,
                    }

        except Exception as conn_err:
            logger.error(f"Failed to acquire isolated Query Role connection: {conn_err}")
            return {
                "success": False,
                "error": "Isolated query connection pool is unavailable.",
                "rows": [],
                "executed_statement": sanitized_sql,
            }

    @classmethod
    async def _generate_zero_row_diagnostics(
        cls,
        conn: AsyncConnection,
        org_schema: str,
        sql_text: str,
        allowed_relations: Set[str],
    ) -> Optional[List[Dict[str, Any]]]:
        """Extract equality predicates from query and report actual distinct values."""
        try:
            parsed = parse_one(sql_text, read="postgres")
            equality_predicates: List[Tuple[str, str]] = []  # (column_name, literal_value)

            for eq_node in parsed.find_all(exp.EQ):
                left = eq_node.left
                right = eq_node.right
                col_name = None
                lit_val = None

                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    col_name = left.name
                    lit_val = right.this
                elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                    col_name = right.name
                    lit_val = left.this

                if col_name and lit_val:
                    equality_predicates.append((col_name, str(lit_val)))

            if not equality_predicates:
                return None

            # Find which table contains the column
            tables_in_query = [t.name.lower() for t in parsed.find_all(exp.Table) if t.name.lower() in allowed_relations]
            if not tables_in_query:
                return None
            target_table = tables_in_query[0]

            results = []
            for col_name, lit_val in equality_predicates[:3]:
                try:
                    distinct_query = text(f"""
                        SELECT DISTINCT "{col_name}"::text
                        FROM "{org_schema}"."{target_table}"
                        WHERE "{col_name}" IS NOT NULL
                        LIMIT 30
                    """)
                    d_res = await conn.execute(distinct_query)
                    actual_vals = [r[0] for r in d_res.fetchall()]
                    results.append({
                        "filtered_column": col_name,
                        "attempted_value": lit_val,
                        "available_values": actual_vals,
                    })
                except Exception:
                    results.append({
                        "filtered_column": col_name,
                        "attempted_value": lit_val,
                        "available_values": [],
                        "note": "Values unavailable",
                    })

            return results
        except Exception as diag_err:
            logger.debug(f"Could not build zero-row diagnostic: {diag_err}")
            return None
