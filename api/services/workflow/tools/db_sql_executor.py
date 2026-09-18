"""Relational Database SQL Executor and Value Lookup tool implementations.

Implements Requirement 10, Requirement 11, Requirement 18:
Executes queries via QueryExecutor and lookups via ValueLookupService
with full tenant isolation, query timeout, and safety validation.
"""

from typing import Any, Dict, List, Optional, Set
from loguru import logger

from api.db.db_table_client import DbTableClient
from api.services.db_tables.query_executor import QueryExecutor
from api.services.db_tables.value_lookup import ValueLookupService

_LOG = "🗄️ [DbSqlExecutor]"


async def execute_db_sql(
    *,
    organization_id: int,
    allowed_relations: Set[str],
    sql: str,
    limit: int = 20,
    is_interactive: bool = True,
) -> Dict[str, Any]:
    """Validate and safely execute a SQL SELECT query against tenant DB tables/views.

    Args:
        organization_id: Tenant isolation scope.
        allowed_relations: Allowlisted table and view identifiers for the node.
        sql: The SQL SELECT statement authored by the LLM.
        limit: Hard row cap requested by the caller (default 20, max 100).
        is_interactive: Whether query originates from interactive session (2000ms timeout)
            or non-interactive (5000ms timeout).

    Returns:
        Dict with rows, total_results, columns, executed_sql, and any diagnostic info.
    """
    logger.info(
        f"{_LOG} execute_db_sql | org={organization_id} "
        f"allowed_relations={allowed_relations} limit={limit}"
    )
    logger.debug(f"{_LOG} SQL received:\n{sql}")

    if not sql or not sql.strip():
        return {
            "error": "No SQL statement provided.",
            "rows": [],
            "total_results": 0,
            "columns": [],
            "executed_sql": "",
        }

    try:
        result = await QueryExecutor.execute_query(
            organization_id=organization_id,
            sql_statement=sql,
            allowed_relations=allowed_relations,
            caller_row_limit=limit,
            is_interactive=is_interactive,
        )
        return result
    except Exception as e:
        logger.error(f"{_LOG} execution exception: {e}")
        return {
            "error": f"Database query execution failed: {str(e)}",
            "rows": [],
            "total_results": 0,
            "columns": [],
            "executed_sql": sql,
        }


async def lookup_db_column_values(
    *,
    organization_id: int,
    table_name: str,
    column_name: str,
    allowed_relations: Set[str],
    search: Optional[str] = None,
    limit: int = 50,
    turn_call_count: int = 0,
) -> Dict[str, Any]:
    """Look up distinct values or ranges for a column in a database table.

    Args:
        organization_id: Tenant isolation scope.
        table_name: Authorized table or view name.
        column_name: Target column name.
        allowed_relations: Permitted table and view identifiers.
        search: Optional substring filter.
        limit: Max values to return (default 50, max 200).
        turn_call_count: Invocations made in current turn (max 5).

    Returns:
        Dict containing distinct values or summary stats.
    """
    logger.info(
        f"{_LOG} lookup_db_column_values | org={organization_id} "
        f"table={table_name} col={column_name} search={search} turn_count={turn_call_count}"
    )

    # Determine column type if table exists
    column_type = "text"
    try:
        table = await DbTableClient.get_table_by_name(organization_id, table_name)
        if table and table.column_schema:
            for col in table.column_schema:
                if col.get("name", "").lower() == column_name.lower():
                    column_type = col.get("type", "text")
                    break
    except Exception as e:
        logger.debug(f"{_LOG} failed to resolve column schema from table model: {e}")

    try:
        return await ValueLookupService.lookup_values(
            organization_id=organization_id,
            table_name=table_name,
            column_name=column_name,
            column_type=column_type,
            allowed_relations=allowed_relations,
            search_fragment=search,
            turn_call_count=turn_call_count,
        )
    except Exception as e:
        logger.error(f"{_LOG} lookup exception: {e}")
        return {
            "success": False,
            "error": f"Value lookup failed: {str(e)}",
        }
