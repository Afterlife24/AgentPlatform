"""Derivation Builder for Computed Columns and Table Views.

Implements Requirement 9:
1. Validates rules for computed columns and Table Views.
2. Supports operators: equals, not_equals, contains, not_contains, greater_than, less_than, is_null, is_not_null.
3. Compiles SQL expressions for ADD COLUMN ... GENERATED or UPDATE/ALTER, and CREATE VIEW.
4. Enforces operator-type compatibility and tenant safety.
"""

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from api.services.db_tables.identifier_sanitizer import IdentifierSanitizer
from api.services.db_tables.schema_provisioner import SchemaProvisioner

VALID_OPERATORS = {
    "equals", "not_equals", "contains", "not_contains",
    "greater_than", "less_than", "is_null", "is_not_null"
}


class DerivationBuilder:
    """Builds SQL definitions for computed columns and Table Views."""

    MAX_RULES = 10
    MAX_COMPUTED_COLUMNS_PER_TABLE = 10
    MAX_VIEWS_PER_TABLE = 5

    @classmethod
    def validate_rules(
        cls, rules: List[Dict[str, Any]], column_type_map: Dict[str, str]
    ) -> Tuple[bool, Optional[str]]:
        """Validate rule structure and operator compatibility with column types."""
        if not rules or len(rules) > cls.MAX_RULES:
            return False, f"Rules must contain between 1 and {cls.MAX_RULES} elements."

        for rule in rules:
            src_col = rule.get("source_column")
            op = rule.get("operator")
            operand = rule.get("operand")

            if not src_col or src_col not in column_type_map:
                return False, f"Source column '{src_col}' does not exist on table. Valid columns: {list(column_type_map.keys())}"

            if op not in VALID_OPERATORS:
                return False, f"Operator '{op}' is not supported. Supported operators: {sorted(list(VALID_OPERATORS))}"

            if op not in ("is_null", "is_not_null"):
                if operand is None or not (1 <= len(str(operand)) <= 200):
                    return False, f"Operand for operator '{op}' must be between 1 and 200 characters."

            # Requirement 9 Criterion 10: Incompatible operator/type checks
            c_type = column_type_map[src_col]
            if op in ("contains", "not_contains") and c_type != "text":
                return False, f"Operator '{op}' is only supported on 'text' columns, but '{src_col}' is '{c_type}'."

            if op in ("greater_than", "less_than") and c_type in ("text", "boolean"):
                return False, f"Operator '{op}' is not supported on '{c_type}' column '{src_col}'."

        return True, None

    @classmethod
    def build_rule_predicate(cls, rule: Dict[str, Any], column_type_map: Dict[str, str]) -> str:
        """Compile a single rule into a PostgreSQL SQL predicate expression."""
        col = rule["source_column"]
        op = rule["operator"]
        c_type = column_type_map[col]
        operand = rule.get("operand", "")

        # Safe SQL string literal escaping (double single-quotes)
        escaped_val = str(operand).replace("'", "''")

        if op == "is_null":
            return f'("{col}" IS NULL)'
        elif op == "is_not_null":
            return f'("{col}" IS NOT NULL)'
        elif op == "equals":
            # Case-sensitive
            if c_type in ("integer", "numeric"):
                return f'("{col}" IS NULL OR "{col}" = {escaped_val})'
            return f'("{col}" IS NULL OR "{col}" = \'{escaped_val}\')'
        elif op == "not_equals":
            # Case-sensitive
            if c_type in ("integer", "numeric"):
                return f'("{col}" IS NULL OR "{col}" != {escaped_val})'
            return f'("{col}" IS NULL OR "{col}" != \'{escaped_val}\')'
        elif op == "contains":
            # Case-insensitive
            return f'("{col}" IS NULL OR "{col}" ILIKE \'%{escaped_val}%\')'
        elif op == "not_contains":
            # Case-insensitive
            return f'("{col}" IS NULL OR "{col}" NOT ILIKE \'%{escaped_val}%\')'
        elif op == "greater_than":
            if c_type in ("integer", "numeric"):
                return f'("{col}" IS NULL OR "{col}" > {escaped_val})'
            return f'("{col}" IS NULL OR "{col}" > \'{escaped_val}\')'
        elif op == "less_than":
            if c_type in ("integer", "numeric"):
                return f'("{col}" IS NULL OR "{col}" < {escaped_val})'
            return f'("{col}" IS NULL OR "{col}" < \'{escaped_val}\')'

        return "TRUE"

    @classmethod
    async def create_table_view(
        cls,
        conn: AsyncConnection,
        organization_id: int,
        view_name: str,
        underlying_table_name: str,
        rules: List[Dict[str, Any]],
        column_type_map: Dict[str, str],
    ) -> str:
        """Create a PostgreSQL view in csv_org_{org_id}."""
        schema = SchemaProvisioner.get_org_schema_name(organization_id)
        predicates = [cls.build_rule_predicate(r, column_type_map) for r in rules]
        where_clause = " AND ".join(predicates) if predicates else "TRUE"

        # Exclude _row_id from view select
        cols = [f'"{c}"' for c in column_type_map.keys() if c != "_row_id"]
        select_clause = ", ".join(cols) if cols else "*"

        sql = f"""
            CREATE OR REPLACE VIEW "{schema}"."{view_name}" AS
            SELECT {select_clause}
            FROM "{schema}"."{underlying_table_name}"
            WHERE {where_clause}
        """
        await conn.execute(text(sql))
        await SchemaProvisioner.grant_select_to_query_role(conn, organization_id, view_name)
        return view_name

    @classmethod
    async def apply_computed_column(
        cls,
        conn: AsyncConnection,
        organization_id: int,
        table_name: str,
        col_name: str,
        result_type: str,
        combinator: str,
        rules: List[Dict[str, Any]],
        column_type_map: Dict[str, str],
    ) -> None:
        """Add and populate a computed column on a DB Table."""
        schema = SchemaProvisioner.get_org_schema_name(organization_id)
        pg_type = "BOOLEAN" if result_type == "boolean" else "TEXT"

        # Add column if not exists
        await conn.execute(
            text(f'ALTER TABLE "{schema}"."{table_name}" ADD COLUMN IF NOT EXISTS "{col_name}" {pg_type}')
        )

        # Build calculation
        predicates = [cls.build_rule_predicate(r, column_type_map) for r in rules]
        joiner = " AND " if combinator == "all" else " OR "
        calc_expr = joiner.join(predicates) if predicates else "TRUE"

        if result_type == "boolean":
            update_sql = f"""
                UPDATE "{schema}"."{table_name}"
                SET "{col_name}" = CASE WHEN ({calc_expr}) THEN TRUE ELSE FALSE END
            """
        else:
            update_sql = f"""
                UPDATE "{schema}"."{table_name}"
                SET "{col_name}" = CASE WHEN ({calc_expr}) THEN 'true' ELSE 'false' END
            """
        await conn.execute(text(update_sql))
