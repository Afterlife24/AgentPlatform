"""SQL Validator using sqlglot for the Relational Dataset Tool.

Implements Requirement 15:
1. Parses statements with `sqlglot` (PostgreSQL dialect).
2. Enforces statement length (<= 20,000 chars) and AST nesting depth (<= 20).
3. Requires root expression to be SELECT or non-recursive WITH ... SELECT.
4. Allowlist validation: all referenced relations must be in the node's allowlist or intra-statement CTEs.
5. Rejects DDL, DML, COPY, CALL, SET, schema qualification (outside org schema), disallowed functions.
6. Rejects CTEs shadowing allowlisted relations.
7. Fails closed and returns normalized re-serialized SQL without comments.
"""

from typing import List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

MAX_SQL_LENGTH = 20_000
MAX_AST_DEPTH = 20

# Functions strictly prohibited in LLM queries
DISALLOWED_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_stat_file", "pg_ls_dir",
    "dblink", "dblink_exec", "dblink_connect", "dblink_open",
    "pg_sleep", "query_to_xml", "inet_server_addr", "inet_client_addr",
    "current_setting", "set_config", "version", "pg_database_size",
    "pg_relation_size", "pg_total_relation_size", "pg_tablespace_size",
    "lo_import", "lo_export", "pg_cancel_backend", "pg_terminate_backend",
    "pg_reload_conf", "pg_rotate_logfile"
}


class SQLValidator:
    """Validates and sanitizes SQL statements authored by LLMs."""

    @classmethod
    def validate_sql(
        cls, sql_text: str, allowed_relations: Set[str], org_schema: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate an LLM-authored SQL statement.

        Args:
            sql_text: Raw SQL statement string.
            allowed_relations: Set of authorized table and view identifiers for the node.
            org_schema: The tenant's Org Schema name (e.g. 'csv_org_1').

        Returns:
            Tuple of (is_valid, error_message, sanitized_sql).
        """
        # 1. Length check
        if not sql_text or len(sql_text) > MAX_SQL_LENGTH:
            return False, f"Statement exceeds maximum allowed length of {MAX_SQL_LENGTH} characters.", None

        # 2. Parse with sqlglot (PostgreSQL dialect)
        try:
            parsed_list = sqlglot.parse(sql_text, read="postgres")
        except Exception as e:
            return False, f"SQL syntax error: {str(e)}", None

        # 3. Exactly one statement required
        if not parsed_list or len(parsed_list) != 1:
            return False, "A single statement is required.", None

        expression = parsed_list[0]
        if expression is None:
            return False, "Statement could not be parsed.", None

        # 4. Check AST depth
        if cls._get_ast_depth(expression) > MAX_AST_DEPTH:
            return False, f"Statement expression nesting depth exceeds limit of {MAX_AST_DEPTH} levels.", None

        # 5. Root expression check: SELECT or non-recursive WITH ... SELECT
        if isinstance(expression, exp.Select):
            root_select = expression
        elif isinstance(expression, exp.With):
            if expression.args.get("recursive"):
                return False, "RECURSIVE CTEs are not permitted.", None
            if not isinstance(expression.this, exp.Select):
                return False, "Root expression of WITH statement must be a SELECT.", None
            root_select = expression.this
        else:
            return False, "Statement must be a read-only SELECT statement.", None

        # 6. Reject any data-modifying or administrative expressions
        forbidden_nodes = (
            exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
            exp.Alter, exp.Command, exp.Set, exp.Copy
        )
        for node in expression.walk():
            if isinstance(node, forbidden_nodes):
                return False, f"Disallowed statement type: {node.key.upper()}.", None

        # 7. Collect CTE names
        cte_names: Set[str] = set()
        for with_clause in expression.find_all(exp.With):
            for cte in with_clause.expressions:
                if isinstance(cte, exp.CTE):
                    cte_name = cte.alias_or_name.lower()
                    # Criterion 13: CTE cannot shadow an allowlisted relation
                    if cte_name in allowed_relations:
                        return False, f"Common table expression '{cte_name}' shadows an allowed relation identifier.", None
                    cte_names.add(cte_name)

        # 8. Check all referenced tables and views
        allowed_lower = {r.lower() for r in allowed_relations}
        for table_node in expression.find_all(exp.Table):
            t_name = table_node.name.lower()

            # Schema qualification check (Criterion 8)
            schema_part = table_node.db
            if schema_part:
                if schema_part.lower() != org_schema.lower():
                    return False, f"Disallowed schema qualification '{schema_part}'. Only relations in '{org_schema}' are permitted.", None

            # Must be either an allowed relation or a defined CTE
            if t_name not in allowed_lower and t_name not in cte_names:
                return False, (
                    f"Disallowed relation '{t_name}'. "
                    f"This node only permits access to: {sorted(list(allowed_relations))}"
                ), None

        # 9. Function check (Criterion 12)
        for func_node in expression.find_all(exp.Anonymous, exp.Func):
            f_name = func_node.name.lower()
            if f_name in DISALLOWED_FUNCTIONS:
                return False, f"Disallowed function call: '{f_name}'.", None
            # Check schema qualification on function
            if hasattr(func_node, "db") and func_node.db and func_node.db.lower() == "pg_catalog":
                return False, f"Direct 'pg_catalog' function qualification is disallowed.", None

        # 10. Re-serialize without comments (Criterion 14)
        sanitized_sql = expression.sql(dialect="postgres", comments=False)
        return True, None, sanitized_sql

    @classmethod
    def _get_ast_depth(cls, node: exp.Expression, current_depth: int = 1) -> int:
        """Calculate maximum nesting depth of an expression tree."""
        if current_depth > MAX_AST_DEPTH + 1:
            return current_depth

        max_child_depth = current_depth
        for child in node.iter_expressions():
            d = cls._get_ast_depth(child, current_depth + 1)
            if d > max_child_depth:
                max_child_depth = d

        return max_child_depth
