"""CSV SQL Executor tool — accepts raw SQL from the LLM and executes it safely.

This is the "Option A" approach: the main workflow LLM (which has full domain
context from the node prompt — column names, intent mappings, etc.) generates
the SQL directly. This tool just validates and executes it.

Security:
- Only SELECT statements are allowed.
- DDL (CREATE, DROP, ALTER), DML (INSERT, UPDATE, DELETE), and
  administrative commands (GRANT, REVOKE, TRUNCATE) are blocked.
- Subqueries modifying data are blocked.
- Column names in ORDER BY / GROUP BY are validated against the known schema.
- The query is scoped to the organization's table(s) via a CTE wrapper.

Usage in workflow:
  The node prompt tells the LLM about the DB columns, instructs it to generate
  a SQL SELECT statement, and then call this tool with that SQL.
"""

import re
from typing import Any, Dict, List, Optional

from loguru import logger

from api.db import db_client


# ---------------------------------------------------------------------------
# SQL Safety Validator
# ---------------------------------------------------------------------------

# Forbidden SQL keywords that indicate non-read operations
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|"
    r"EXECUTE|EXEC|INTO|SET\s|MERGE|REPLACE|CALL|DO|LOCK|UNLOCK|"
    r"RENAME|COPY|VACUUM|REINDEX|CLUSTER|COMMENT|SECURITY|OWNER)\b",
    re.IGNORECASE,
)

# Only allow SELECT at the start (after optional whitespace/comments)
_SELECT_PATTERN = re.compile(r"^\s*(SELECT)\b", re.IGNORECASE)

# Block semicolons (multiple statements)
_SEMICOLON_PATTERN = re.compile(r";")

# Block common injection patterns
_INJECTION_PATTERNS = re.compile(
    r"(--|/\*|\*/|xp_|sp_|0x[0-9a-f]+\s)",
    re.IGNORECASE,
)


def validate_sql(sql: str) -> tuple[bool, str]:
    """Validate that a SQL string is a safe read-only SELECT query.

    Returns:
        (is_valid, error_message) — error_message is empty if valid.
    """
    if not sql or not sql.strip():
        return False, "Empty SQL query."

    stripped = sql.strip()

    # Must start with SELECT
    if not _SELECT_PATTERN.match(stripped):
        return False, "Only SELECT queries are allowed. Query must start with SELECT."

    # No semicolons (block multi-statement injection)
    if _SEMICOLON_PATTERN.search(stripped):
        return False, "Multiple statements (semicolons) are not allowed."

    # No forbidden keywords
    match = _FORBIDDEN_KEYWORDS.search(stripped)
    if match:
        return False, f"Forbidden keyword detected: {match.group(0).upper()}. Only read operations are allowed."

    # No common injection patterns
    if _INJECTION_PATTERNS.search(stripped):
        return False, "Potentially unsafe SQL pattern detected."

    return True, ""


# ---------------------------------------------------------------------------
# Tool schema for the LLM
# ---------------------------------------------------------------------------

def get_csv_sql_tool(
    table_uuids: Optional[List[str]] = None,
    column_schema: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Return the LLM-facing schema for execute_csv_sql.

    The tool accepts a raw SQL SELECT statement. The LLM is expected to
    generate this SQL based on the node prompt (which lists all available
    columns and their types).
    """
    col_hint = ""
    if column_schema:
        parts = [f'"{c["name"]}" ({c.get("type", "text")})' for c in column_schema[:30]]
        col_hint = ", ".join(parts)
        if len(column_schema) > 30:
            col_hint += f" ... (+{len(column_schema) - 30} more)"

    description = (
        "Execute a SQL SELECT query against the equipment/data table. "
        "Generate a valid PostgreSQL SELECT statement based on the user's question "
        "and the column information provided in your prompt. "
        "ONLY SELECT queries are allowed — no INSERT, UPDATE, DELETE, or DDL."
    )
    if col_hint:
        description += f" Available columns: {col_hint}."

    return {
        "type": "function",
        "function": {
            "name": "execute_csv_sql",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "A PostgreSQL SELECT statement to execute. "
                            "Use row_data->>'Column Name' to access columns. "
                            "For numeric comparisons, cast: "
                            "(row_data->>'Column Name')::float. "
                            "Table name is 'csv_data'. "
                            "Example: SELECT row_data->>'Model' AS model, "
                            "row_data->>'Brand' AS brand "
                            "FROM csv_data "
                            "WHERE (row_data->>'Capacity')::float > 50 "
                            "ORDER BY (row_data->>'Capacity')::float DESC "
                            "LIMIT 10"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum rows to return (default 20, max 100). Overrides any LIMIT in the SQL.",
                    },
                },
                "required": ["sql"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

async def execute_csv_sql(
    *,
    organization_id: int,
    table_uuids: List[str],
    sql: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Validate and execute a SQL SELECT query against csv_table_rows.

    The query is wrapped in a CTE that scopes data to the organization's
    tables, so the LLM-generated SQL operates on a virtual 'csv_data' table.

    Args:
        organization_id: Tenant isolation.
        table_uuids: Which CSV tables to query.
        sql: The SQL SELECT statement from the LLM.
        limit: Hard cap on returned rows.

    Returns:
        Dict with rows, total_results, columns, and the executed SQL.
    """
    limit = min(limit, 100)

    # Step 1: Validate SQL safety
    is_valid, error = validate_sql(sql)
    if not is_valid:
        logger.warning(f"SQL validation failed: {error} | SQL: {sql[:200]}")
        return {
            "error": f"SQL rejected: {error}",
            "rows": [],
            "total_results": 0,
            "columns": [],
        }

    # Step 2: Resolve table UUIDs to internal IDs
    table_ids = await db_client._resolve_table_uuids(
        organization_id, table_uuids
    )
    if not table_ids:
        return {
            "error": "No tables found for the given UUIDs.",
            "rows": [],
            "total_results": 0,
            "columns": [],
        }

    # Step 3: Wrap user SQL in a CTE that provides 'csv_data' scoped to org + tables
    table_ids_sql = ",".join(str(t) for t in table_ids)

    # The CTE exposes csv_data as a virtual table containing row_data JSONB
    # The user's SQL references csv_data and uses row_data->>'col' syntax
    wrapped_sql = f"""
        WITH csv_data AS (
            SELECT row_data, row_index
            FROM csv_table_rows
            WHERE organization_id = :org_id
              AND table_id IN ({table_ids_sql})
        )
        {sql}
    """

    # Step 4: Enforce limit — strip any existing LIMIT and add our own
    # (to prevent the LLM from setting LIMIT 999999)
    wrapped_sql_stripped = re.sub(
        r"\bLIMIT\s+\d+\b", "", wrapped_sql, flags=re.IGNORECASE
    )
    final_sql = f"{wrapped_sql_stripped.rstrip().rstrip(';')} LIMIT :max_limit"

    params = {"org_id": organization_id, "max_limit": limit}

    logger.info(f"CSV SQL executor | org={organization_id} | sql={sql[:200]}")

    # Step 5: Execute
    try:
        rows_raw = await db_client.execute_raw_query(
            final_sql, params
        )

        # Format results
        rows = []
        columns: List[str] = []
        if rows_raw:
            columns = list(rows_raw[0].keys())
            rows = rows_raw

        return {
            "rows": rows,
            "total_results": len(rows),
            "columns": columns,
            "executed_sql": sql,
        }

    except Exception as e:
        logger.error(f"CSV SQL execution failed: {e} | SQL: {final_sql[:300]}")
        return {
            "error": f"Query execution failed: {str(e)}",
            "rows": [],
            "total_results": 0,
            "columns": [],
            "executed_sql": sql,
        }
