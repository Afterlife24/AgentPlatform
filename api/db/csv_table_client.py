"""Database client for CSV table operations.

Mirrors the KnowledgeBaseClient pattern: all DB access goes through
async SQLAlchemy sessions; raw SQL uses parameterised text() queries.

Fixes applied:
  Issue  1: SQL injection — order_by, group_by, aggregate_field are validated
            against known_columns before being interpolated into SQL strings.
  Issue  3: create_csv_table_with_uuid() — new method that accepts a caller-
            supplied UUID (created in /upload-url) so /process never generates
            a second UUID.
  Issue  7: list_csv_tables() now accepts limit/offset for pagination.
  New:      update_csv_table_name() — allows /process to rename the table if
            the caller supplies a display name override.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, select, text, update

from api.db.base_client import BaseDBClient
from api.db.models import CsvTableModel, CsvTableRowModel


class CsvTableClient(BaseDBClient):
    """All DB operations for the CSV Table tool."""

    # ------------------------------------------------------------------
    # SQL injection guard  (Issue 1)
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_col(col_name: str, known_columns: Optional[List[str]]) -> Optional[str]:
        """Return col_name only if it exists in known_columns.

        If known_columns is None/empty (caller didn't provide schema), the
        column is allowed through — this preserves backward-compatibility for
        callers that don't pass the schema yet.

        Returns None if the column is not in the known list, which causes the
        caller to skip the ORDER BY / GROUP BY clause entirely rather than
        risk SQL injection.
        """
        if not known_columns:
            return col_name  # no schema provided — allow (backward-compat)
        if col_name in known_columns:
            return col_name
        logger.warning(
            f"CSV query: column '{col_name}' not in known schema {known_columns[:5]}… "
            "— dropping from SQL to prevent injection"
        )
        return None

    # ------------------------------------------------------------------
    # Table metadata
    # ------------------------------------------------------------------

    async def create_csv_table(
        self,
        *,
        organization_id: int,
        created_by: int,
        name: str,
    ) -> CsvTableModel:
        """Create a csv_tables record with a server-generated UUID.

        Kept for backward-compatibility with the KB ingestion path
        (knowledge_base_processing.py). New direct-upload flow should
        use create_csv_table_with_uuid() instead.
        """
        return await self.create_csv_table_with_uuid(
            table_uuid=str(uuid.uuid4()),
            organization_id=organization_id,
            created_by=created_by,
            name=name,
        )

    async def create_csv_table_with_uuid(
        self,
        *,
        table_uuid: str,
        organization_id: int,
        created_by: int,
        name: str,
    ) -> CsvTableModel:
        """Create a csv_tables record with a CALLER-SUPPLIED UUID.

        Issue 3 fix: /upload-url generates the UUID, stores it here as
        'pending', and returns it to the client. /process then fetches
        this record by uuid — it never creates a second record.
        """
        async with self.async_session() as session:
            table = CsvTableModel(
                table_uuid=table_uuid,
                organization_id=organization_id,
                created_by=created_by,
                name=name,
                processing_status="pending",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(table)
            await session.commit()
            await session.refresh(table)
            return table

    async def get_csv_table_by_uuid(
        self, table_uuid: str, organization_id: int
    ) -> Optional[CsvTableModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(CsvTableModel).where(
                    CsvTableModel.table_uuid == table_uuid,
                    CsvTableModel.organization_id == organization_id,
                    CsvTableModel.is_active.is_(True),
                )
            )
            return result.scalars().first()

    async def get_csv_table_by_id(self, table_id: int) -> Optional[CsvTableModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(CsvTableModel).where(CsvTableModel.id == table_id)
            )
            return result.scalars().first()

    async def list_csv_tables(
        self,
        organization_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CsvTableModel]:
        """List active CSV tables for an org, newest first.

        Issue 7 fix: added limit/offset for pagination.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(CsvTableModel)
                .where(
                    CsvTableModel.organization_id == organization_id,
                    CsvTableModel.is_active.is_(True),
                )
                .order_by(CsvTableModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all())

    async def update_csv_table_status(
        self,
        table_id: int,
        status: str,
        *,
        row_count: int = 0,
        column_schema: Optional[List[Dict]] = None,
        error: Optional[str] = None,
    ) -> None:
        values: Dict[str, Any] = {
            "processing_status": status,
            "updated_at": datetime.now(UTC),
        }
        if row_count:
            values["row_count"] = row_count
        if column_schema is not None:
            values["column_schema"] = column_schema
        if error is not None:
            values["processing_error"] = error

        async with self.async_session() as session:
            await session.execute(
                update(CsvTableModel)
                .where(CsvTableModel.id == table_id)
                .values(**values)
            )
            await session.commit()

    async def update_csv_table_name(self, table_id: int, name: str) -> None:
        """Update the display name of a CSV table."""
        async with self.async_session() as session:
            await session.execute(
                update(CsvTableModel)
                .where(CsvTableModel.id == table_id)
                .values(name=name, updated_at=datetime.now(UTC))
            )
            await session.commit()

    async def delete_csv_table(self, table_uuid: str, organization_id: int) -> bool:
        """Soft-delete a csv_table record."""
        async with self.async_session() as session:
            result = await session.execute(
                update(CsvTableModel)
                .where(
                    CsvTableModel.table_uuid == table_uuid,
                    CsvTableModel.organization_id == organization_id,
                )
                .values(is_active=False, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # Row data
    # ------------------------------------------------------------------

    async def insert_csv_rows(
        self,
        table_id: int,
        organization_id: int,
        rows: List[Dict[str, Any]],
    ) -> None:
        """Bulk-insert parsed CSV rows."""
        async with self.async_session() as session:
            row_objects = [
                CsvTableRowModel(
                    table_id=table_id,
                    organization_id=organization_id,
                    row_index=i,
                    row_data=row,
                )
                for i, row in enumerate(rows)
            ]
            session.add_all(row_objects)
            await session.commit()

    async def delete_csv_rows(self, table_id: int) -> None:
        """Delete all rows for a table (called before re-upload).

        Issue 4 fix: ensures re-processing is idempotent — no duplicate rows.
        """
        async with self.async_session() as session:
            await session.execute(
                delete(CsvTableRowModel).where(CsvTableRowModel.table_id == table_id)
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Query execution (used by the LLM tool)
    # ------------------------------------------------------------------

    async def query_csv_table_rows(
        self,
        organization_id: int,
        table_uuids: List[str],
        filters: Optional[Dict[str, Any]] = None,
        columns: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_dir: str = "asc",
        limit: int = 20,
        known_columns: Optional[List[str]] = None,  # Issue 1: SQL injection guard
    ) -> Dict[str, Any]:
        """Execute a structured query against csv_table_rows.

        Args:
            organization_id: Tenant isolation.
            table_uuids:     Tables to search.
            filters:         {col: value} or {col: {op: value}} conditions.
            columns:         Columns to return (None = all).
            order_by:        Column to sort by — validated against known_columns.
            order_dir:       "asc" or "desc".
            limit:           Max rows (capped at 100).
            known_columns:   Full list of valid column names from the table
                             schema. Used to validate order_by before SQL
                             interpolation (Issue 1 SQL injection fix).
        """
        limit = min(limit, 100)

        table_ids = await self._resolve_table_uuids(organization_id, table_uuids)
        if not table_ids:
            return {"rows": [], "total_results": 0, "columns": []}

        where_parts = [
            "r.organization_id = :org_id",
            f"r.table_id IN ({','.join(str(t) for t in table_ids)})",
        ]
        params: Dict[str, Any] = {"org_id": organization_id, "limit": limit}

        if filters:
            for i, (col, val) in enumerate(filters.items()):
                col_expr = f"r.row_data->>:col_{i}"
                params[f"col_{i}"] = col

                if isinstance(val, dict):
                    op      = list(val.keys())[0]
                    operand = list(val.values())[0]
                    num_cast = (
                        f"(CASE WHEN ({col_expr}) ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                        f"THEN ({col_expr})::float ELSE NULL END)"
                    )
                    op_map = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
                    if op in op_map:
                        # Store as float in params — avoids mixing :name::float
                        # (SQLAlchemy named param + PostgreSQL cast = syntax error)
                        try:
                            params[f"val_{i}"] = float(operand)
                        except (TypeError, ValueError):
                            params[f"val_{i}"] = operand
                        where_parts.append(f"{num_cast} {op_map[op]} :val_{i}")
                    elif op == "in" and isinstance(operand, list):
                        placeholders = ", ".join(
                            f":val_{i}_{j}" for j in range(len(operand))
                        )
                        for j, item in enumerate(operand):
                            params[f"val_{i}_{j}"] = str(item)
                        where_parts.append(f"LOWER({col_expr}) IN ({placeholders})")
                    elif op == "contains":
                        params[f"val_{i}"] = f"%{operand}%"
                        where_parts.append(f"LOWER({col_expr}) LIKE LOWER(:val_{i})")
                else:
                    params[f"val_{i}"] = str(val)
                    where_parts.append(f"LOWER({col_expr}) = LOWER(:val_{i})")

        # Issue 1: validate order_by against known schema before interpolation
        order_clause = ""
        if order_by:
            safe = self._safe_col(order_by, known_columns)
            if safe:
                dir_sql = "DESC" if order_dir.lower() == "desc" else "ASC"
                order_clause = (
                    f"ORDER BY (CASE WHEN (r.row_data->>'{safe}') "
                    f"~ '^-?[0-9]+(\\.[0-9]+)?$' "
                    f"THEN (r.row_data->>'{safe}')::float ELSE NULL END) "
                    f"{dir_sql} NULLS LAST, "
                    f"r.row_data->>'{safe}' {dir_sql}"
                )

        where_sql = " AND ".join(where_parts)
        sql = f"""
            SELECT r.row_data, r.row_index
            FROM csv_table_rows r
            WHERE {where_sql}
            {order_clause}
            LIMIT :limit
        """
        logger.info(f"csv_table SQL where_parts: {where_parts}")
        logger.info(f"csv_table params keys: {list(params.keys())}")

        try:
            rows_raw = await self.execute_raw_query(sql, params)
            rows = [r["row_data"] for r in rows_raw]

            count_sql = (
                f"SELECT COUNT(*) AS cnt FROM csv_table_rows r WHERE {where_sql}"
            )
            count_params = {k: v for k, v in params.items() if k != "limit"}
            count_result = await self.execute_raw_query(count_sql, count_params)
            total = count_result[0]["cnt"] if count_result else len(rows)

            col_names: List[str] = []
            if rows:
                col_names = list(rows[0].keys())
                if columns:
                    rows = [{c: r.get(c) for c in columns} for r in rows]
                    col_names = columns

            return {"rows": rows, "total_results": total, "columns": col_names}

        except Exception as e:
            logger.error(f"csv_table query failed: {e}")
            return {"error": str(e), "rows": [], "total_results": 0, "columns": []}

    async def aggregate_csv_table_rows(
        self,
        organization_id: int,
        table_uuids: List[str],
        aggregate_function: str,
        aggregate_field: Optional[str],
        group_by: Optional[str],
        filters: Optional[Dict[str, Any]] = None,
        order_by: str = "desc",
        limit: int = 20,
        known_columns: Optional[List[str]] = None,  # Issue 1: SQL injection guard
    ) -> Dict[str, Any]:
        """Run COUNT/SUM/AVG/MAX/MIN + optional GROUP BY against csv_table_rows.

        Issue 1 fix: aggregate_field and group_by are validated against
        known_columns before being interpolated into SQL f-strings.
        """
        limit = min(limit, 100)
        agg_fn = aggregate_function.upper()
        if agg_fn not in ("COUNT", "SUM", "AVG", "MAX", "MIN"):
            return {"error": f"Unsupported aggregate function: {agg_fn}", "results": []}

        table_ids = await self._resolve_table_uuids(organization_id, table_uuids)
        if not table_ids:
            return {"results": [], "total_results": 0}

        where_parts = [
            "r.organization_id = :org_id",
            f"r.table_id IN ({','.join(str(t) for t in table_ids)})",
        ]
        params: Dict[str, Any] = {"org_id": organization_id, "limit": limit}

        if filters:
            for i, (col, val) in enumerate(filters.items()):
                params[f"col_{i}"] = col
                params[f"val_{i}"] = str(val)
                where_parts.append(
                    f"LOWER(r.row_data->>:col_{i}) = LOWER(:val_{i})"
                )

        where_sql = " AND ".join(where_parts)

        # Issue 1: validate aggregate_field before interpolation
        if agg_fn == "COUNT" and not aggregate_field:
            agg_expr  = "COUNT(*)"
            agg_alias = "count"
        else:
            safe_field = self._safe_col(aggregate_field or "", known_columns)
            if not safe_field:
                return {
                    "error": f"Column '{aggregate_field}' not found in table schema.",
                    "results": [],
                }
            num_expr  = (
                f"(CASE WHEN (r.row_data->>'{safe_field}') "
                f"~ '^-?[0-9]+(\\.[0-9]+)?$' "
                f"THEN (r.row_data->>'{safe_field}')::float ELSE NULL END)"
            )
            agg_expr  = f"{agg_fn}({num_expr})"
            agg_alias = agg_fn.lower()

        if group_by:
            # Issue 1: validate group_by before interpolation
            safe_group = self._safe_col(group_by, known_columns)
            if not safe_group:
                return {
                    "error": f"Column '{group_by}' not found in table schema.",
                    "results": [],
                }
            group_expr = f"r.row_data->>'{safe_group}'"
            dir_sql    = "DESC" if order_by.lower() == "desc" else "ASC"
            sql = f"""
                SELECT {group_expr} AS group_value, {agg_expr} AS {agg_alias}
                FROM csv_table_rows r
                WHERE {where_sql}
                GROUP BY {group_expr}
                ORDER BY {agg_alias} {dir_sql} NULLS LAST
                LIMIT :limit
            """
        else:
            sql = f"""
                SELECT {agg_expr} AS {agg_alias}
                FROM csv_table_rows r
                WHERE {where_sql}
            """

        try:
            results = await self.execute_raw_query(sql, params)
            return {"results": results, "total_results": len(results)}
        except Exception as e:
            logger.error(f"csv_table aggregation failed: {e}")
            return {"error": str(e), "results": []}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_table_uuids(
        self, organization_id: int, table_uuids: List[str]
    ) -> List[int]:
        """Resolve public table UUIDs → internal integer IDs."""
        if not table_uuids:
            return []
        placeholders = ", ".join(f":uuid_{i}" for i in range(len(table_uuids)))
        params: Dict[str, Any] = {"org_id": organization_id}
        for i, uid in enumerate(table_uuids):
            params[f"uuid_{i}"] = uid
        sql = f"""
            SELECT id FROM csv_tables
            WHERE table_uuid IN ({placeholders})
              AND organization_id = :org_id
              AND is_active = true
        """
        rows = await self.execute_raw_query(sql, params)
        return [r["id"] for r in rows]
