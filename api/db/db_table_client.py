"""Database client for Relational Dataset Tool operations.

Handles all persistence for DbTableModel, DbTableRelationshipModel,
DbTableComputedColumnModel, and DbTableViewModel.
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models_relational_dataset import (
    DbTableComputedColumnModel,
    DbTableModel,
    DbTableRelationshipModel,
    DbTableViewModel,
)
from api.services.db_tables.schema_provisioner import SchemaProvisioner


class DbTableClient(BaseDBClient):
    """Async database client for the Relational Dataset Tool."""

    # ------------------------------------------------------------------
    # DB Table CRUD
    # ------------------------------------------------------------------

    async def create_db_table(
        self,
        *,
        document_uuid: str,
        organization_id: int,
        created_by: int,
        table_name: str,
        source_filename: str,
    ) -> DbTableModel:
        """Create a new db_tables metadata record."""
        async with self.async_session() as session:
            record = DbTableModel(
                document_uuid=document_uuid,
                organization_id=organization_id,
                created_by=created_by,
                table_name=table_name,
                source_filename=source_filename,
                state="pending",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_db_table_by_document_uuid(
        self, document_uuid: str, organization_id: int
    ) -> Optional[DbTableModel]:
        """Fetch a DB table by knowledge base document_uuid or db_table_uuid and org."""
        async with self.async_session() as session:
            stmt = select(DbTableModel).where(
                or_(
                    DbTableModel.document_uuid == document_uuid,
                    DbTableModel.db_table_uuid == document_uuid,
                ),
                DbTableModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_db_table_by_id(
        self, table_id: int, organization_id: int
    ) -> Optional[DbTableModel]:
        """Fetch a DB table by primary key ID and org."""
        async with self.async_session() as session:
            stmt = select(DbTableModel).where(
                DbTableModel.id == table_id,
                DbTableModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_db_table_by_name(
        self, table_name: str, organization_id: int
    ) -> Optional[DbTableModel]:
        """Fetch a DB table by table_name and org."""
        async with self.async_session() as session:
            stmt = select(DbTableModel).where(
                DbTableModel.table_name == table_name,
                DbTableModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_db_tables(
        self, organization_id: int, limit: int = 50, offset: int = 0
    ) -> List[DbTableModel]:
        """List all DB tables for an organization."""
        async with self.async_session() as session:
            stmt = (
                select(DbTableModel)
                .where(DbTableModel.organization_id == organization_id)
                .order_by(DbTableModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_db_table_loaded(
        self,
        table_id: int,
        *,
        state: str,
        row_count: int,
        column_count: int,
        column_schema: List[Dict[str, Any]],
        type_report: Optional[List[Dict[str, Any]]] = None,
        load_report: Optional[Dict[str, Any]] = None,
        value_profile: Optional[List[Dict[str, Any]]] = None,
        suggested_primary_key: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Update DB table after load completes."""
        async with self.async_session() as session:
            stmt = (
                update(DbTableModel)
                .where(DbTableModel.id == table_id)
                .values(
                    state=state,
                    row_count=row_count,
                    column_count=column_count,
                    column_schema=column_schema,
                    type_report=type_report,
                    load_report=load_report,
                    value_profile=value_profile,
                    suggested_primary_key=suggested_primary_key,
                    failure_reason=failure_reason,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def set_confirmed_primary_key(
        self, document_uuid: str, organization_id: int, column_name: str
    ) -> Optional[DbTableModel]:
        """Store the confirmed primary key choice."""
        async with self.async_session() as session:
            stmt = (
                update(DbTableModel)
                .where(
                    DbTableModel.document_uuid == document_uuid,
                    DbTableModel.organization_id == organization_id,
                )
                .values(
                    confirmed_primary_key=column_name,
                    updated_at=datetime.now(UTC),
                )
                .returning(DbTableModel)
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.scalar_one_or_none()

    async def set_forced_text_columns(
        self, document_uuid: str, organization_id: int, forced_columns: List[str]
    ) -> None:
        """Store user override forcing columns to text."""
        async with self.async_session() as session:
            stmt = (
                update(DbTableModel)
                .where(
                    DbTableModel.document_uuid == document_uuid,
                    DbTableModel.organization_id == organization_id,
                )
                .values(
                    forced_text_columns=forced_columns,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def delete_db_table(self, document_uuid: str, organization_id: int) -> bool:
        """Drop physical table & views from Org Schema and delete all metadata records in one transaction."""
        async with self.async_session() as session:
            stmt = select(DbTableModel).where(
                DbTableModel.document_uuid == document_uuid,
                DbTableModel.organization_id == organization_id,
            )
            res = await session.execute(stmt)
            tbl = res.scalar_one_or_none()
            if not tbl:
                return False

            table_name = tbl.table_name
            schema_name = SchemaProvisioner.get_org_schema_name(organization_id)

            # Drop physical table (CASCADE drops dependent views)
            try:
                await session.execute(
                    text(f'DROP TABLE IF EXISTS "{schema_name}"."{table_name}" CASCADE')
                )
            except Exception as e:
                logger.error(f"Failed to drop physical table {schema_name}.{table_name}: {e}")
                raise e

            # Delete metadata record (foreign keys cascade)
            await session.delete(tbl)
            await session.commit()
            return True

    # ------------------------------------------------------------------
    # Relationships CRUD
    # ------------------------------------------------------------------

    async def create_relationship(
        self,
        *,
        organization_id: int,
        source_db_table_id: int,
        source_column: str,
        target_db_table_id: int,
        target_column: str,
        cardinality: str,
        state: str = "accepted",
        containment_ratio: Optional[float] = None,
        distinct_source_count: Optional[int] = None,
        absent_source_count: Optional[int] = None,
        name_similarity_score: Optional[float] = None,
        warning_message: Optional[str] = None,
    ) -> DbTableRelationshipModel:
        """Create or save a relationship record."""
        async with self.async_session() as session:
            rel = DbTableRelationshipModel(
                organization_id=organization_id,
                source_db_table_id=source_db_table_id,
                source_column=source_column,
                target_db_table_id=target_db_table_id,
                target_column=target_column,
                cardinality=cardinality,
                state=state,
                containment_ratio=containment_ratio,
                distinct_source_count=distinct_source_count,
                absent_source_count=absent_source_count,
                name_similarity_score=name_similarity_score,
                warning_message=warning_message,
            )
            session.add(rel)
            await session.commit()
            await session.refresh(rel)
            return rel

    async def list_relationships(
        self, organization_id: int, status: Optional[str] = None, state: Optional[str] = None
    ) -> List[DbTableRelationshipModel]:
        """List relationships for an organization with eager loading of tables."""
        filter_state = state or status
        async with self.async_session() as session:
            stmt = (
                select(DbTableRelationshipModel)
                .options(
                    selectinload(DbTableRelationshipModel.source_table),
                    selectinload(DbTableRelationshipModel.target_table),
                )
                .where(DbTableRelationshipModel.organization_id == organization_id)
            )
            if filter_state:
                stmt = stmt.where(DbTableRelationshipModel.state == filter_state)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_relationships_for_org(
        self, organization_id: int, state: Optional[str] = None
    ) -> List[DbTableRelationshipModel]:
        """List relationships for an organization."""
        async with self.async_session() as session:
            stmt = select(DbTableRelationshipModel).where(
                DbTableRelationshipModel.organization_id == organization_id
            )
            if state:
                stmt = stmt.where(DbTableRelationshipModel.state == state)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_relationship_state(
        self, relationship_uuid: str, organization_id: int, state: str
    ) -> Optional[DbTableRelationshipModel]:
        """Update state (accepted/rejected) of a relationship."""
        async with self.async_session() as session:
            stmt = (
                update(DbTableRelationshipModel)
                .where(
                    DbTableRelationshipModel.relationship_uuid == relationship_uuid,
                    DbTableRelationshipModel.organization_id == organization_id,
                )
                .values(state=state, updated_at=datetime.now(UTC))
                .returning(DbTableRelationshipModel)
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.scalar_one_or_none()

    async def delete_relationship(self, relationship_uuid: str, organization_id: int) -> bool:
        """Delete a relationship record."""
        async with self.async_session() as session:
            stmt = delete(DbTableRelationshipModel).where(
                DbTableRelationshipModel.relationship_uuid == relationship_uuid,
                DbTableRelationshipModel.organization_id == organization_id,
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount > 0

    # ------------------------------------------------------------------
    # Computed Columns & Views CRUD
    # ------------------------------------------------------------------

    async def create_computed_column_record(
        self,
        *,
        db_table_id: int,
        organization_id: int,
        target_column_name: str,
        result_type: str,
        combinator: str,
        rules: List[Dict[str, Any]],
    ) -> DbTableComputedColumnModel:
        """Create a computed column metadata record."""
        async with self.async_session() as session:
            record = DbTableComputedColumnModel(
                db_table_id=db_table_id,
                organization_id=organization_id,
                target_column_name=target_column_name,
                result_type=result_type,
                combinator=combinator,
                rules=rules,
                state="active",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_computed_columns(
        self, db_table_id: int, organization_id: int
    ) -> List[DbTableComputedColumnModel]:
        """List computed columns for a table."""
        async with self.async_session() as session:
            stmt = select(DbTableComputedColumnModel).where(
                DbTableComputedColumnModel.db_table_id == db_table_id,
                DbTableComputedColumnModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_table_view_record(
        self,
        *,
        db_table_id: int,
        organization_id: int,
        view_name: str,
        rules: List[Dict[str, Any]],
    ) -> DbTableViewModel:
        """Create a table view metadata record."""
        async with self.async_session() as session:
            record = DbTableViewModel(
                db_table_id=db_table_id,
                organization_id=organization_id,
                view_name=view_name,
                rules=rules,
                state="active",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_views(
        self, db_table_id: int, organization_id: int
    ) -> List[DbTableViewModel]:
        """List views for a table."""
        async with self.async_session() as session:
            stmt = select(DbTableViewModel).where(
                DbTableViewModel.db_table_id == db_table_id,
                DbTableViewModel.organization_id == organization_id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_view(self, view_uuid: str, organization_id: int) -> bool:
        """Drop view from Org Schema and delete record."""
        async with self.async_session() as session:
            stmt = select(DbTableViewModel).where(
                DbTableViewModel.view_uuid == view_uuid,
                DbTableViewModel.organization_id == organization_id,
            )
            res = await session.execute(stmt)
            vw = res.scalar_one_or_none()
            if not vw:
                return False

            schema_name = SchemaProvisioner.get_org_schema_name(organization_id)
            try:
                await session.execute(text(f'DROP VIEW IF EXISTS "{schema_name}"."{vw.view_name}"'))
            except Exception as e:
                logger.warning(f"Could not drop view {schema_name}.{vw.view_name}: {e}")

            await session.delete(vw)
            await session.commit()
            return True


db_table_client = DbTableClient()
