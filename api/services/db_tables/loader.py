"""Two-Pass Transactional Loader for the Relational Dataset Tool.

Implements Requirement 4, Requirement 10, and Requirement 17:
1. Two-pass load:
   - Pass 1: Creates table with text columns, copies rows in source order with `_row_id`.
   - Pass 2: Alters columns to inferred types with per-column savepoints.
2. Degraded mode fallback:
   - Failing type conversions roll back to savepoint and keep the column as text.
   - Sets state to 'loaded' (0 failures) or 'loaded_degraded' (>=1 conversion failures).
3. Value Profile calculation and candidate-key index creation.
4. Preserves pre-re-parse table on failure during re-parse operations.
"""

import csv
import io
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from api.services.db_tables.identifier_sanitizer import IdentifierSanitizer
from api.services.db_tables.key_detector import KeyDetector
from api.services.db_tables.schema_provisioner import SchemaProvisioner
from api.services.db_tables.type_inferrer import TypeInferrer


class TableLoader:
    """Executes atomic and degraded loads for CSV files into PostgreSQL tables."""

    MAX_DATA_ROWS = 1_000_000
    LOAD_TIMEOUT_SECONDS = 300

    @classmethod
    async def load_csv_stream(
        cls,
        conn: AsyncConnection,
        organization_id: int,
        table_name: str,
        csv_content: str,
        forced_text_columns: Optional[List[str]] = None,
        replacement_table_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a full two-pass transactional load for a CSV string.

        Args:
            conn: SQLAlchemy AsyncConnection running inside an active transaction.
            organization_id: Tenant ID.
            table_name: Target physical table name in csv_org_{org_id}.
            csv_content: Raw CSV string.
            forced_text_columns: Optional list of column names forced to text.
            replacement_table_name: Temporary table name when rebuilding/re-parsing.

        Returns:
            Dict containing state ('loaded', 'loaded_degraded', 'failed'), row_count,
            column_schema, type_report, load_report, and value_profile.
        """
        start_time = time.time()
        actual_table_name = replacement_table_name or table_name
        schema_name = await SchemaProvisioner.ensure_org_schema(conn, organization_id)
        forced_set = set(forced_text_columns or [])

        # 1. Parse CSV header
        # Check UTF-8 BOM
        if csv_content.startswith("\ufeff"):
            csv_content = csv_content[1:]

        # Delimiter validation (Requirement 17 Criterion 11)
        first_line = csv_content.splitlines()[0] if csv_content.splitlines() else ""
        if not first_line.strip():
            return {
                "state": "failed",
                "failure_reason": "Header row is required. The file is empty or has no content.",
            }

        reader = csv.reader(io.StringIO(csv_content))
        try:
            raw_headers = next(reader)
        except StopIteration:
            return {
                "state": "failed",
                "failure_reason": "Header row is required. No header row found.",
            }

        if len(raw_headers) == 1 and (";" in first_line or "\t" in first_line):
            return {
                "state": "failed",
                "failure_reason": "Comma is the only supported delimiter. Please re-save the file with comma-separated fields.",
            }

        # Check for empty headers
        if not any(h.strip() for h in raw_headers):
            return {
                "state": "failed",
                "failure_reason": "Header row is required. Every field of the header row is empty.",
            }

        # 2. Sanitize column headers
        sanitized_headers = IdentifierSanitizer.sanitize_headers(raw_headers)
        col_names = [s[0] for s in sanitized_headers]

        # 3. Read data rows (up to MAX_DATA_ROWS)
        data_rows: List[List[str]] = []
        load_report_entries: List[Dict[str, Any]] = []
        expected_cols = len(raw_headers)

        for row_idx, row in enumerate(reader, 1):
            if row_idx > cls.MAX_DATA_ROWS:
                raise ValueError(f"CSV exceeds maximum allowed data rows ({cls.MAX_DATA_ROWS}).")

            # Handle surplus or missing fields (Requirement 17 Criteria 6 & 9)
            if len(row) > expected_cols:
                surplus = len(row) - expected_cols
                load_report_entries.append({
                    "severity": "warning",
                    "category": "surplus_fields",
                    "row": row_idx,
                    "message": f"Row {row_idx} had {surplus} surplus field(s) discarded.",
                })
                row = row[:expected_cols]
            elif len(row) < expected_cols:
                missing = expected_cols - len(row)
                load_report_entries.append({
                    "severity": "warning",
                    "category": "missing_fields",
                    "row": row_idx,
                    "message": f"Row {row_idx} had {missing} missing field(s) loaded as null.",
                })
                row = row + [""] * missing

            data_rows.append(row)

        row_count = len(data_rows)

        # 4. Infer types per column
        type_reports = []
        column_schemas = []
        for col_idx, (col_name, orig_header) in enumerate(sanitized_headers):
            col_values = [(r_idx + 1, data_rows[r_idx][col_idx]) for r_idx in range(row_count)]
            forced = "text" if col_name in forced_set else None
            inference = TypeInferrer.infer_column(col_name, orig_header, col_values, forced_type=forced)
            type_reports.append(inference)
            column_schemas.append({
                "name": col_name,
                "original_header": orig_header,
                "type": inference["type"],
                "null_count": inference["null_count"],
                "distinct_count": inference["distinct_count"],
            })

        # 5. PASS 1: Create table with text columns and copy rows
        col_defs = ['"_row_id" INTEGER']
        for col_name in col_names:
            col_defs.append(f'"{col_name}" TEXT')

        create_sql = f'CREATE TABLE "{schema_name}"."{actual_table_name}" ({", ".join(col_defs)})'
        await conn.execute(text(create_sql))

        # Insert data rows in batches
        if data_rows:
            insert_cols = ['"_row_id"'] + [f'"{c}"' for c in col_names]
            placeholders = [":_row_id"] + [f":c_{i}" for i in range(len(col_names))]
            insert_sql = f'INSERT INTO "{schema_name}"."{actual_table_name}" ({", ".join(insert_cols)}) VALUES ({", ".join(placeholders)})'

            batch_size = 500
            for i in range(0, len(data_rows), batch_size):
                batch = data_rows[i : i + batch_size]
                params_list = []
                for b_idx, r in enumerate(batch):
                    p = {"_row_id": i + b_idx + 1}
                    for c_idx, val in enumerate(r):
                        # Store normalized null or trimmed string
                        if TypeInferrer.is_null_value(val):
                            p[f"c_{c_idx}"] = None
                        else:
                            p[f"c_{c_idx}"] = str(val).strip()
                    params_list.append(p)
                await conn.execute(text(insert_sql), params_list)

        # 6. PASS 2: Alter column types with per-column savepoints
        conversion_failures = 0
        for schema_item in column_schemas:
            c_name = schema_item["name"]
            c_type = schema_item["type"]

            if c_type == "text":
                continue

            savepoint_name = f"sp_{c_name[:20]}"
            await conn.execute(text(f"SAVEPOINT {savepoint_name}"))

            alter_sql = cls._build_alter_type_sql(schema_name, actual_table_name, c_name, c_type)

            try:
                await conn.execute(text(alter_sql))
                await conn.execute(text(f"RELEASE SAVEPOINT {savepoint_name}"))
            except Exception as e:
                # Rollback to savepoint and leave as text (degraded)
                await conn.execute(text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
                conversion_failures += 1
                schema_item["type"] = "text"
                schema_item["conversion_failed"] = True

                load_report_entries.append({
                    "severity": "warning",
                    "category": "type_conversion_failure",
                    "column": c_name,
                    "attempted_type": c_type,
                    "message": (
                        f"Column '{c_name}' could not be converted to {c_type} and was retained as text. "
                        f"Database error: {str(e)}"
                    ),
                    "corrective_action": "Force column to text or correct non-conforming data values.",
                })

        # 7. Grant SELECT to Query Role
        await SchemaProvisioner.grant_select_to_query_role(conn, organization_id, actual_table_name)

        # 8. Evaluate Primary Keys & Candidate Keys
        key_eval = KeyDetector.evaluate_table_keys(row_count, column_schemas)
        candidate_keys = key_eval["candidate_keys"]
        suggested_pk = key_eval["suggested_primary_key"]

        # Create indexes on candidate keys (Requirement 4 Criterion 14)
        for cand_key in candidate_keys:
            idx_name = f"ix_{actual_table_name}_{cand_key}"[:63]
            await conn.execute(
                text(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{schema_name}"."{actual_table_name}" ("{cand_key}")')
            )

        # Update planner statistics
        await conn.execute(text(f'ANALYZE "{schema_name}"."{actual_table_name}"'))

        # 9. Compute Value Profile
        value_profile = cls._build_value_profile(data_rows, column_schemas)

        # 10. Determine terminal state
        if conversion_failures > 0:
            terminal_state = "loaded_degraded"
        else:
            terminal_state = "loaded"

        return {
            "state": terminal_state,
            "row_count": row_count,
            "column_count": len(column_schemas),
            "column_schema": column_schemas,
            "type_report": type_reports,
            "load_report": {
                "entries": load_report_entries[:1000],
                "total_entries": len(load_report_entries),
                "is_truncated": len(load_report_entries) > 1000,
                "review_required": len(load_report_entries) > 100,
            },
            "value_profile": value_profile,
            "candidate_keys": candidate_keys,
            "suggested_primary_key": suggested_pk,
        }

    @classmethod
    def _build_alter_type_sql(cls, schema: str, table: str, column: str, target_type: str) -> str:
        """Construct PostgreSQL ALTER COLUMN TYPE statement with USING clause."""
        if target_type == "integer":
            using_clause = f'NULLIF(TRIM("{column}"), \'\')::bigint'
            pg_type = "BIGINT"
        elif target_type == "numeric":
            using_clause = f'NULLIF(TRIM("{column}"), \'\')::numeric'
            pg_type = "NUMERIC"
        elif target_type == "boolean":
            using_clause = (
                f'CASE WHEN LOWER(TRIM("{column}")) IN (\'true\', \'yes\', \'t\', \'y\') THEN TRUE '
                f'WHEN LOWER(TRIM("{column}")) IN (\'false\', \'no\', \'f\', \'n\') THEN FALSE '
                f'ELSE NULL END'
            )
            pg_type = "BOOLEAN"
        elif target_type == "date":
            using_clause = f'NULLIF(TRIM("{column}"), \'\')::date'
            pg_type = "DATE"
        elif target_type == "timestamp":
            using_clause = f'NULLIF(TRIM("{column}"), \'\')::timestamp'
            pg_type = "TIMESTAMP"
        else:
            using_clause = f'"{column}"'
            pg_type = "TEXT"

        return (
            f'ALTER TABLE "{schema}"."{table}" '
            f'ALTER COLUMN "{column}" TYPE {pg_type} USING ({using_clause})'
        )

    @classmethod
    def _build_value_profile(cls, data_rows: List[List[str]], columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute the Value Profile for each column according to Requirement 12."""
        profiles = []
        for col_idx, col in enumerate(columns):
            name = col["name"]
            c_type = col["type"]

            values: List[str] = []
            for r in data_rows:
                v = r[col_idx]
                if not TypeInferrer.is_null_value(v):
                    values.append(str(v).strip())

            if not values:
                profiles.append({
                    "name": name,
                    "type": c_type,
                    "null_count": len(data_rows),
                    "distinct_count": 0,
                    "no_values": True,
                })
                continue

            distinct_sorted = sorted(list(set(values)))
            distinct_count = len(distinct_sorted)

            if c_type in ("integer", "numeric"):
                try:
                    num_vals = [float(x) for x in values if x]
                    profiles.append({
                        "name": name,
                        "type": c_type,
                        "null_count": len(data_rows) - len(values),
                        "distinct_count": distinct_count,
                        "min": min(num_vals) if num_vals else None,
                        "max": max(num_vals) if num_vals else None,
                    })
                except Exception:
                    profiles.append({
                        "name": name,
                        "type": c_type,
                        "null_count": len(data_rows) - len(values),
                        "distinct_count": distinct_count,
                        "values": distinct_sorted[:30],
                    })
            elif c_type in ("date", "timestamp"):
                profiles.append({
                    "name": name,
                    "type": c_type,
                    "null_count": len(data_rows) - len(values),
                    "distinct_count": distinct_count,
                    "min": distinct_sorted[0],
                    "max": distinct_sorted[-1],
                })
            else:
                # Text or Boolean
                if distinct_count <= 30:
                    profiles.append({
                        "name": name,
                        "type": c_type,
                        "null_count": len(data_rows) - len(values),
                        "distinct_count": distinct_count,
                        "values": [v[:60] for v in distinct_sorted],
                    })
                else:
                    profiles.append({
                        "name": name,
                        "type": c_type,
                        "null_count": len(data_rows) - len(values),
                        "distinct_count": distinct_count,
                        "examples": [v[:60] for v in distinct_sorted[:3]],
                    })

        return profiles
