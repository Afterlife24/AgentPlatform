<<<<<<< Updated upstream
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
=======
"""CSV SQL Executor — LLM writes raw SQL, this tool executes it safely.

This is the generic SQL execution tool for the CSV table feature.
The LLM receives the column schema via the workflow node prompt and
writes a PostgreSQL SELECT query. This tool:

  1. Validates the SQL is read-only (SELECT only — no DROP/DELETE/UPDATE/INSERT)
  2. Scopes the query to the correct org and csv table rows
  3. Executes it against csv_table_rows using the JSONB row_data column
  4. Returns the results back to the LLM

The workflow prompt provides the LLM with:
  - Column names and types
  - Example SQL patterns
  - Business rules (e.g. Energy Source values, Lift Type values)

This tool is generic — works for any CSV file from any client.
No CSV-specific logic lives here. All business rules stay in the workflow.

Usage in workflow prompt:
  Tool: execute_csv_sql
  Parameter: sql (string) — a PostgreSQL SELECT query against csv_data table
  The tool replaces 'csv_data' with the actual scoped csv_table_rows query.
"""

import re
from typing import Any
>>>>>>> Stashed changes

from loguru import logger

from api.db import db_client


# ---------------------------------------------------------------------------
<<<<<<< Updated upstream
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
=======
# Safety — allowed SQL patterns
# ---------------------------------------------------------------------------

# Dangerous statements that must never be executed
_FORBIDDEN_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# SQL must start with SELECT (after stripping whitespace/comments)
_SELECT_PATTERN = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


def _validate_sql(sql: str) -> tuple[bool, str]:
    """Validate that SQL is safe to execute.

    Returns (is_safe, error_message).
>>>>>>> Stashed changes
    """
    if not sql or not sql.strip():
        return False, "Empty SQL query."

<<<<<<< Updated upstream
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
=======
    # Must start with SELECT
    if not _SELECT_PATTERN.match(sql):
        return False, "Only SELECT queries are allowed."

    # Must not contain dangerous statements
    if _FORBIDDEN_PATTERNS.search(sql):
        return False, "Query contains forbidden statements (INSERT/UPDATE/DELETE/DROP etc.)"

    # Must not contain semicolons (prevents statement chaining)
    if ";" in sql:
        return False, "Semicolons are not allowed in queries."
>>>>>>> Stashed changes

    return True, ""


<<<<<<< Updated upstream
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
=======
def _scope_sql(sql: str, organization_id: int, table_ids: list[int]) -> str:
    """Replace 'csv_data' table reference with scoped csv_table_rows.

    The workflow prompt tells the LLM to query 'csv_data' as the table name.
    This function replaces that with:

        (SELECT * FROM csv_table_rows
         WHERE organization_id = <org_id>
           AND table_id IN (<ids>)) AS csv_data

    This ensures:
    - Multi-tenant isolation (only this org's data)
    - Only the correct table's rows are queried

    The LLM's query is unchanged — it still uses row_data->>'Column Name'
    syntax which works directly against csv_table_rows.
    """
    if not table_ids:
        # No tables — return a query that produces empty results
        return "SELECT NULL WHERE FALSE"

    ids_str = ", ".join(str(i) for i in table_ids)
    subquery = (
        f"(SELECT * FROM csv_table_rows "
        f"WHERE organization_id = {organization_id} "
        f"AND table_id IN ({ids_str})) AS csv_data"
    )

    # Replace 'csv_data' (as a table reference) with the scoped subquery
    # Handle: FROM csv_data, JOIN csv_data, FROM csv_data WHERE, etc.
    scoped = re.sub(
        r"\bcsv_data\b",
        subquery,
        sql,
        flags=re.IGNORECASE,
    )

    return scoped


# ---------------------------------------------------------------------------
# Tool schema — what the LLM sees
# ---------------------------------------------------------------------------

def get_csv_sql_tool(
    table_uuids: list | None = None,
    column_schema: list | None = None,
) -> dict[str, Any]:
    """Return the LLM-facing schema for execute_csv_sql."""
    n_tables = len(table_uuids) if table_uuids else 0
    table_note = (
        f" Queries across {n_tables} attached CSV table(s)."
        if n_tables else ""
    )

    # Build column hint from schema
    col_hint = ""
    if column_schema:
        parts = [f"{c['name']} ({c.get('type', 'text')})" for c in column_schema[:30]]
        col_hint = " Available columns: " + ", ".join(parts)
>>>>>>> Stashed changes
        if len(column_schema) > 30:
            col_hint += f" ... (+{len(column_schema) - 30} more)"

    description = (
<<<<<<< Updated upstream
        "Execute a SQL SELECT query against the equipment/data table. "
        "Generate a valid PostgreSQL SELECT statement based on the user's question "
        "and the column information provided in your prompt. "
        "ONLY SELECT queries are allowed — no INSERT, UPDATE, DELETE, or DDL."
    )
    if col_hint:
        description += f" Available columns: {col_hint}."
=======
        "Execute a PostgreSQL SELECT query against the CSV equipment/data table. "
        "Write standard SQL using row_data->>\\\"Column Name\\\" to access columns. "
        "Use this for any data lookup — filtering, sorting, counting, aggregating. "
        "Table name in your query: csv_data."
        f"{table_note}{col_hint}"
    )
>>>>>>> Stashed changes

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
<<<<<<< Updated upstream
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
=======
                            "A PostgreSQL SELECT query against the csv_data table. "
                            "Access columns via: row_data->>'Column Name'. "
                            "For numeric operations: regexp_replace(row_data->>'Col', '[^0-9.]', '', 'g')::float. "
                            "Example: SELECT row_data->>'Brand' AS brand, row_data->>'Model' AS model "
                            "FROM csv_data WHERE row_data->>'Brand' ILIKE '%JLG%' LIMIT 10"
                        ),
                    },
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
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
=======
    sql: str,
    organization_id: int,
    table_uuids: list[str],
) -> dict[str, Any]:
    """Execute a LLM-generated SQL query against csv_table_rows.

    Flow:
    1. Validate SQL is safe (SELECT only, no dangerous statements)
    2. Resolve table UUIDs → internal IDs scoped to this org
    3. Replace 'csv_data' with scoped subquery
    4. Execute and return results

    Args:
        sql:             The SELECT query written by the LLM.
        organization_id: Tenant isolation.
        table_uuids:     CSV table UUIDs attached to the workflow node.

    Returns:
        {"rows": [...], "total_results": int, "columns": [...]}
        or {"error": "...", "rows": [], "total_results": 0}
    """
    # Step 1: validate
    is_safe, error_msg = _validate_sql(sql)
    if not is_safe:
        logger.warning(f"execute_csv_sql rejected unsafe SQL: {error_msg} | SQL: {sql[:200]}")
        return {
            "error": f"Query rejected: {error_msg}",
            "rows": [],
            "total_results": 0,
            "columns": [],
            "sql": sql,
        }

    # Step 2: resolve UUIDs → internal table IDs
    try:
        table_ids = await db_client.csv_table_client_resolve_table_uuids(
            organization_id, table_uuids
        )
    except AttributeError:
        # Fallback: use the _resolve_table_uuids method directly
        try:
            table_ids = await db_client._resolve_csv_table_ids(organization_id, table_uuids)
        except Exception:
            table_ids = []

    if not table_ids:
        # Try resolving via raw SQL if the helper isn't available
        try:
            placeholders = ", ".join(f":uuid_{i}" for i in range(len(table_uuids)))
            params: dict = {"org_id": organization_id}
            for i, uid in enumerate(table_uuids):
                params[f"uuid_{i}"] = uid
            resolve_sql = f"""
                SELECT id FROM csv_tables
                WHERE table_uuid IN ({placeholders})
                  AND organization_id = :org_id
                  AND is_active = true
            """
            rows = await db_client.execute_raw_query(resolve_sql, params)
            table_ids = [r["id"] for r in rows]
        except Exception as e:
            logger.error(f"execute_csv_sql: failed to resolve table UUIDs: {e}")
            return {
                "error": "Could not find the CSV table. Please ensure it is attached to this node.",
                "rows": [],
                "total_results": 0,
                "columns": [],
            }

    if not table_ids:
        return {
            "error": "No CSV tables found for this query.",
>>>>>>> Stashed changes
            "rows": [],
            "total_results": 0,
            "columns": [],
        }

<<<<<<< Updated upstream
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
=======
    # Step 3: scope the SQL
    scoped_sql = _scope_sql(sql, organization_id, table_ids)
    logger.debug(f"execute_csv_sql scoped: {scoped_sql[:300]}")

    # Step 4: execute
    try:
        result_rows = await db_client.execute_raw_query(scoped_sql, {})

        # Extract column names from first row
        columns = list(result_rows[0].keys()) if result_rows else []

        logger.info(
            f"execute_csv_sql: returned {len(result_rows)} rows "
            f"for org={organization_id}, tables={table_ids}"
        )

        return {
            "rows": result_rows,
            "total_results": len(result_rows),
            "columns": columns,
            "sql_executed": scoped_sql[:300],  # for debugging
        }

    except Exception as e:
        logger.error(f"execute_csv_sql execution failed: {e} | SQL: {scoped_sql[:300]}")
>>>>>>> Stashed changes
        return {
            "error": f"Query execution failed: {str(e)}",
            "rows": [],
            "total_results": 0,
            "columns": [],
<<<<<<< Updated upstream
            "executed_sql": sql,
=======
            "sql": sql,
>>>>>>> Stashed changes
        }
