"""Database Table tool schemas and value-profile / schema helpers.

Implements Requirement 18 (Additive & Non-Regression):
Dedicated to the Relational Dataset Tool (`db_tables`).
Parallel to `csv_table.py` without modifying or affecting it.
"""

from typing import Any, Dict, List, Optional, Set
from loguru import logger

from api.db.db_table_client import DbTableClient

_LOG = "🗄️ [DbTableTool]"


def get_db_sql_tool(
    allowed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return the LLM-facing schema for execute_db_sql.

    The tool accepts a raw PostgreSQL SELECT statement. The LLM generates
    this SQL based on the Injected Schema Block provided in the prompt.
    """
    tables_hint = ""
    if allowed_tables:
        tables_hint = f" Available tables and views: {', '.join(allowed_tables)}."

    description = (
        "Execute a PostgreSQL SELECT query against the relational database tables. "
        "Write standard PostgreSQL SELECT queries with JOINs, aggregations, WHERE filters, etc. "
        "Table and column names must match the exact identifiers provided in the schema block. "
        "ONLY SELECT queries are allowed — no INSERT, UPDATE, DELETE, or DDL."
        f"{tables_hint}"
    )

    return {
        "type": "function",
        "function": {
            "name": "execute_db_sql",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "A valid PostgreSQL SELECT statement to execute. "
                            "Tables run in the tenant's schema so you can use unqualified table/view names. "
                            "Example: SELECT c.customer_name, SUM(o.total_amount) "
                            "FROM customers c JOIN orders o ON c.id = o.customer_id "
                            "WHERE o.order_date >= '2025-01-01' "
                            "GROUP BY c.customer_name ORDER BY 2 DESC LIMIT 10"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum rows to return (default 20, max 100).",
                    },
                },
                "required": ["sql"],
            },
        },
    }


def get_db_lookup_tool(
    allowed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return the LLM-facing schema for lookup_db_column_values.

    Allows the LLM to inspect actual column values or exact spellings
    before writing filtered SQL queries.
    """
    tables_hint = ""
    if allowed_tables:
        tables_hint = f" Allowed tables: {', '.join(allowed_tables)}."

    return {
        "type": "function",
        "function": {
            "name": "lookup_db_column_values",
            "description": (
                "Look up distinct values or summary stats for a column in a database table. "
                "Use this tool when you need to know the exact stored format or spelling of a value "
                "before authoring a SQL query, or when a column was omitted from the prompt's Value Profile."
                f"{tables_hint}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "The name of the table or view.",
                    },
                    "column": {
                        "type": "string",
                        "description": "The name of the column to inspect.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional search term to filter distinct values (case-insensitive substring match).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of distinct values to return (default 50, max 200).",
                    },
                },
                "required": ["table", "column"],
            },
        },
    }
