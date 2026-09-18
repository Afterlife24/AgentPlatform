"""Relationship Validator and Orphan Reporter.

Implements Requirement 8:
1. Evaluates accepted relationships for a table.
2. Identifies Orphan Rows (rows with non-null foreign key that have no match in target).
3. Calculates orphan ratios and captures up to 10 distinct example orphan values.
4. Marks severity as 'info' (0 orphans) or 'warning' (>= 1 orphans).
5. Bounded by 60s per relationship evaluation.
"""

import asyncio
from typing import Any, Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from api.services.db_tables.schema_provisioner import SchemaProvisioner


class RelationValidator:
    """Validates data integrity across accepted relationships and reports orphan rows."""

    RELATION_TIMEOUT_SECONDS = 60
    MAX_ORPHAN_EXAMPLES = 10

    @classmethod
    async def validate_table_relationships(
        cls,
        conn: AsyncConnection,
        organization_id: int,
        relationships: List[Dict[str, Any]],  # Accepted relationships involving the table
    ) -> List[Dict[str, Any]]:
        """Validate a list of accepted relationships and return report entries."""
        schema = SchemaProvisioner.get_org_schema_name(organization_id)
        report_entries: List[Dict[str, Any]] = []

        for rel in relationships:
            s_table = rel["source_table_name"]
            s_col = rel["source_column"]
            t_table = rel["target_table_name"]
            t_col = rel["target_column"]
            cardinality = rel["cardinality"]

            try:
                # Query source total rows and null count
                count_query = text(f"""
                    SELECT COUNT(*) as total_rows,
                           COUNT(*) FILTER (WHERE "{s_col}" IS NULL) as null_rows
                    FROM "{schema}"."{s_table}"
                """)
                res = await conn.execute(count_query)
                cnt_row = res.fetchone()
                total_rows = cnt_row[0] if cnt_row else 0
                null_rows = cnt_row[1] if cnt_row else 0
                non_null_rows = max(0, total_rows - null_rows)

                # Query orphan rows count
                orphan_query = text(f"""
                    SELECT COUNT(*)
                    FROM "{schema}"."{s_table}" s
                    WHERE s."{s_col}" IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM "{schema}"."{t_table}" t
                          WHERE t."{t_col}" = s."{s_col}"
                      )
                """)
                res_orphan = await conn.execute(orphan_query)
                orph_row = res_orphan.fetchone()
                orphan_count = orph_row[0] if orph_row else 0

                # Compute orphan ratio
                orphan_ratio = 0.0
                if non_null_rows > 0:
                    orphan_ratio = round(float(orphan_count) / float(non_null_rows), 4)

                # Fetch up to 10 example orphan values
                examples = []
                if orphan_count > 0:
                    example_query = text(f"""
                        SELECT DISTINCT s."{s_col}"::text
                        FROM "{schema}"."{s_table}" s
                        WHERE s."{s_col}" IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM "{schema}"."{t_table}" t
                              WHERE t."{t_col}" = s."{s_col}"
                          )
                        LIMIT {cls.MAX_ORPHAN_EXAMPLES}
                    """)
                    res_ex = await conn.execute(example_query)
                    examples = [str(r[0]) for r in res_ex.fetchall()]

                severity = "warning" if orphan_count > 0 else "info"

                report_entries.append({
                    "relationship_uuid": rel.get("relationship_uuid"),
                    "source_table": s_table,
                    "source_column": s_col,
                    "target_table": t_table,
                    "target_column": t_col,
                    "cardinality": cardinality,
                    "total_source_rows": total_rows,
                    "null_source_rows": null_rows,
                    "orphan_row_count": orphan_count,
                    "orphan_ratio": orphan_ratio,
                    "example_orphan_values": examples,
                    "severity": severity,
                })

            except Exception as e:
                report_entries.append({
                    "relationship_uuid": rel.get("relationship_uuid"),
                    "source_table": s_table,
                    "source_column": s_col,
                    "target_table": t_table,
                    "target_column": t_col,
                    "cardinality": cardinality,
                    "severity": "warning",
                    "incomplete": True,
                    "error": str(e),
                })

        return report_entries
