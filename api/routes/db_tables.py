"""FastAPI routes for the Relational Dataset Tool (/api/v1/db-tables).

Implements endpoints for inspecting and configuring DB Tables, Primary Keys,
Relationships, Orphan validation, Computed Columns, Table Views, and Re-parsing.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from api.db.db_table_client import DbTableClient
from api.db.knowledge_base_client import KnowledgeBaseClient
from api.schemas.db_tables import (
    ConfirmPrimaryKeySchema,
    CreateComputedColumnSchema,
    CreateRelationshipSchema,
    CreateTableViewSchema,
    DbTableResponseSchema,
    RelationshipResponseSchema,
    ReparseTableSchema,
    UpdateRelationshipStatusSchema,
)
from api.services.auth.depends import get_user
from api.services.db_tables.derivation_builder import DerivationBuilder
from api.services.db_tables.loader import TableLoader
from api.services.db_tables.prompt_composer import PromptComposer
from api.services.db_tables.relation_detector import RelationDetector
from api.services.db_tables.relation_validator import RelationValidator
from api.services.db_tables.schema_provisioner import SchemaProvisioner
from api.services.storage import storage_fs

router = APIRouter(prefix="/db-tables", tags=["db-tables"])
db_table_client = DbTableClient()
kb_client = KnowledgeBaseClient()


@router.get("", response_model=List[DbTableResponseSchema])
async def list_db_tables(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user=Depends(get_user),
):
    """List all DB Tables for the authenticated user's organization."""
    org_id = user.selected_organization_id
    tables = await db_table_client.list_db_tables(org_id, limit=limit, offset=offset)
    return [
        DbTableResponseSchema(
            db_table_uuid=t.db_table_uuid,
            document_uuid=t.document_uuid,
            table_name=t.table_name,
            source_filename=t.source_filename,
            state=t.state,
            row_count=t.row_count,
            column_count=t.column_count,
            column_schema=t.column_schema,
            type_report=t.type_report,
            load_report=t.load_report,
            value_profile=t.value_profile,
            suggested_primary_key=t.suggested_primary_key,
            confirmed_primary_key=t.confirmed_primary_key,
            forced_text_columns=t.forced_text_columns,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tables
    ]


@router.get("/{document_uuid}", response_model=DbTableResponseSchema)
async def get_db_table(document_uuid: str, user=Depends(get_user)):
    """Fetch details of a single DB Table by its document_uuid."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    return DbTableResponseSchema(
        db_table_uuid=tbl.db_table_uuid,
        document_uuid=tbl.document_uuid,
        table_name=tbl.table_name,
        source_filename=tbl.source_filename,
        state=tbl.state,
        row_count=tbl.row_count,
        column_count=tbl.column_count,
        column_schema=tbl.column_schema,
        type_report=tbl.type_report,
        load_report=tbl.load_report,
        value_profile=tbl.value_profile,
        suggested_primary_key=tbl.suggested_primary_key,
        confirmed_primary_key=tbl.confirmed_primary_key,
        forced_text_columns=tbl.forced_text_columns,
        created_at=tbl.created_at,
        updated_at=tbl.updated_at,
    )


@router.post("/{document_uuid}/primary-key")
async def confirm_primary_key(
    document_uuid: str,
    payload: ConfirmPrimaryKeySchema,
    user=Depends(get_user),
):
    """Confirm a primary key selection for a DB Table."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    # Find the column in column_schema
    col_entry = None
    for col in tbl.column_schema:
        if col["name"] == payload.column_name:
            col_entry = col
            break

    if not col_entry:
        raise HTTPException(
            status_code=400,
            detail=f"Column '{payload.column_name}' does not exist on table '{tbl.table_name}'.",
        )

    # Validate candidate key status (Requirement 5 Criterion 7)
    if not col_entry.get("is_candidate_key", False):
        dup_count = col_entry.get("duplicate_count", 0)
        null_count = col_entry.get("null_count", 0)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Column '{payload.column_name}' cannot be confirmed as Primary Key because it is not unique. "
                f"Duplicate count: {dup_count}, Null count: {null_count}."
            ),
        )

    updated = await db_table_client.set_confirmed_primary_key(
        document_uuid, org_id, payload.column_name
    )
    PromptComposer.clear_cache(org_id)
    return {"success": True, "confirmed_primary_key": payload.column_name}


@router.post("/{document_uuid}/reparse")
async def reparse_db_table(
    document_uuid: str,
    payload: ReparseTableSchema,
    user=Depends(get_user),
):
    """Trigger a re-parse and rebuild of a DB Table from its retained CSV file (Requirement 10)."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    # Fetch document from KB to get storage key
    doc = await kb_client.get_document_by_uuid(document_uuid, org_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Source knowledge base document not found.")

    # Save forced text columns if provided
    forced_cols = payload.forced_text_columns or tbl.forced_text_columns or []
    if payload.forced_text_columns is not None:
        await db_table_client.set_forced_text_columns(document_uuid, org_id, forced_cols)

    # Read source CSV from storage (Requirement 10 Criterion 1: within 30s)
    s3_key = doc.s3_key
    try:
        csv_bytes = await storage_fs.aread_file(s3_key)
        csv_content = csv_bytes.decode("utf-8-sig")
    except Exception as read_err:
        logger.error(f"Failed to read CSV from storage for re-parse: {read_err}")
        raise HTTPException(
            status_code=409,
            detail=f"Stored object for DB Table is absent from storage or unreadable: {read_err}",
        )

    # Build replacement table in temporary table name
    temp_table_name = f"{tbl.table_name}_reparse_tmp"[:63]
    schema_name = SchemaProvisioner.get_org_schema_name(org_id)

    async with db_table_client.engine.connect() as conn:
        trans = await conn.begin()
        try:
            # Drop old temp table if exists
            await conn.execute(text(f'DROP TABLE IF EXISTS "{schema_name}"."{temp_table_name}" CASCADE'))

            # Execute load into temp table
            load_result = await TableLoader.load_csv_stream(
                conn=conn,
                organization_id=org_id,
                table_name=tbl.table_name,
                csv_content=csv_content,
                forced_text_columns=forced_cols,
                replacement_table_name=temp_table_name,
            )

            if load_result["state"] == "failed":
                await trans.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Re-parse failed: {load_result.get('failure_reason', 'Unknown error')}",
                )

            # Atomic swap: drop old table, rename temp table to table_name
            await conn.execute(text(f'DROP TABLE IF EXISTS "{schema_name}"."{tbl.table_name}" CASCADE'))
            await conn.execute(text(f'ALTER TABLE "{schema_name}"."{temp_table_name}" RENAME TO "{tbl.table_name}"'))
            await trans.commit()

            # Update db_tables record
            await db_table_client.update_db_table_loaded(
                tbl.id,
                state=load_result["state"],
                row_count=load_result["row_count"],
                column_count=load_result["column_count"],
                column_schema=load_result["column_schema"],
                type_report=load_result["type_report"],
                load_report=load_result["load_report"],
                value_profile=load_result["value_profile"],
                suggested_primary_key=load_result["suggested_primary_key"],
            )

            PromptComposer.clear_cache(org_id)

            return {
                "success": True,
                "state": load_result["state"],
                "row_count": load_result["row_count"],
                "column_count": load_result["column_count"],
            }

        except HTTPException:
            raise
        except Exception as e:
            await trans.rollback()
            # Clean up temp table
            try:
                await session.execute(text(f'DROP TABLE IF EXISTS "{schema_name}"."{temp_table_name}" CASCADE'))
                await session.commit()
            except Exception:
                pass
            logger.error(f"Rebuild failed; preserved pre-re-parse table: {e}")
            raise HTTPException(
                status_code=409,
                detail=f"Rebuild failed and was abandoned. Pre-re-parse table preserved. Error: {str(e)}",
            )


@router.get("/{document_uuid}/relationships", response_model=List[RelationshipResponseSchema])
async def list_table_relationships(document_uuid: str, user=Depends(get_user)):
    """List relationships involving this table."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    all_rels = await db_table_client.list_relationships_for_org(org_id)
    table_rels = [
        r for r in all_rels
        if r.source_db_table_id == tbl.id or r.target_db_table_id == tbl.id
    ]

    return [
        RelationshipResponseSchema(
            relationship_uuid=r.relationship_uuid,
            source_column=r.source_column,
            target_column=r.target_column,
            cardinality=r.cardinality,
            state=r.state,
            containment_ratio=r.containment_ratio,
            distinct_source_count=r.distinct_source_count,
            absent_source_count=r.absent_source_count,
            name_similarity_score=r.name_similarity_score,
            warning_message=r.warning_message,
            created_at=r.created_at,
        )
        for r in table_rels
    ]


@router.post("/relationships", response_model=RelationshipResponseSchema)
async def create_relationship(payload: CreateRelationshipSchema, user=Depends(get_user)):
    """Create or define a relationship between two DB Tables (Requirement 7)."""
    org_id = user.selected_organization_id

    # 1. Capacity limit check: max 50 relationships in suggested/accepted (Criterion 3)
    existing_rels = await db_table_client.list_relationships_for_org(org_id)
    active_count = len([r for r in existing_rels if r.state in ("suggested", "accepted")])
    if active_count >= 50:
        raise HTTPException(
            status_code=400,
            detail="Organization has reached the maximum limit of 50 active relationships.",
        )

    # 2. Verify source & target belong to user's org (Criterion 4)
    s_tbl = await db_table_client.get_db_table_by_document_uuid(payload.source_document_uuid, org_id)
    t_tbl = await db_table_client.get_db_table_by_document_uuid(payload.target_document_uuid, org_id)
    if not s_tbl or not t_tbl:
        raise HTTPException(status_code=400, detail="Source or target DB Table not found in your organization.")
    if s_tbl.id == t_tbl.id:
        raise HTTPException(status_code=400, detail="Self-referencing relationships are not permitted.")

    # 3. Verify columns exist (Criterion 5)
    s_cols = {c["name"]: c["type"] for c in s_tbl.column_schema}
    t_cols = {c["name"]: c["type"] for c in t_tbl.column_schema}
    if payload.source_column not in s_cols:
        raise HTTPException(
            status_code=400,
            detail=f"Source column '{payload.source_column}' not found. Valid columns: {list(s_cols.keys())}",
        )
    if payload.target_column not in t_cols:
        raise HTTPException(
            status_code=400,
            detail=f"Target column '{payload.target_column}' not found. Valid columns: {list(t_cols.keys())}",
        )

    # 4. Check type comparability (Criterion 10)
    s_type = s_cols[payload.source_column]
    t_type = t_cols[payload.target_column]
    if not RelationDetector.are_types_comparable(s_type, t_type):
        raise HTTPException(
            status_code=400,
            detail=f"Column types are not comparable: source '{payload.source_column}' is {s_type}, target '{payload.target_column}' is {t_type}.",
        )

    # 5. Check duplicate relationship (Criterion 9)
    for r in existing_rels:
        if (
            r.source_db_table_id == s_tbl.id
            and r.source_column == payload.source_column
            and r.target_db_table_id == t_tbl.id
            and r.target_column == payload.target_column
            and r.state in ("suggested", "accepted")
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Relationship already exists between these columns in state '{r.state}'.",
            )

    # 6. Candidate key check on target column (Criterion 11)
    target_cand_keys = [c["name"] for c in t_tbl.column_schema if c.get("is_candidate_key")]
    warning = None
    if payload.target_column not in target_cand_keys:
        warning = f"Target column '{payload.target_column}' is not marked as a unique candidate key."

    # Create indexes on both columns (Requirement 20 Criterion 8)
    schema = SchemaProvisioner.get_org_schema_name(org_id)
    async with db_table_client.async_session() as session:
        conn = await session.connection()
        try:
            s_idx = f"ix_rel_{s_tbl.table_name}_{payload.source_column}"[:63]
            t_idx = f"ix_rel_{t_tbl.table_name}_{payload.target_column}"[:63]
            await conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{s_idx}" ON "{schema}"."{s_tbl.table_name}" ("{payload.source_column}")'))
            await conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{t_idx}" ON "{schema}"."{t_tbl.table_name}" ("{payload.target_column}")'))
            await session.commit()
        except Exception as e:
            logger.warning(f"Could not create relationship indexes: {e}")

    # Create record in state 'accepted'
    record = await db_table_client.create_relationship(
        organization_id=org_id,
        source_db_table_id=s_tbl.id,
        source_column=payload.source_column,
        target_db_table_id=t_tbl.id,
        target_column=payload.target_column,
        cardinality=payload.cardinality,
        state="accepted",
        warning_message=warning,
    )

    PromptComposer.clear_cache(org_id)

    return RelationshipResponseSchema(
        relationship_uuid=record.relationship_uuid,
        source_column=record.source_column,
        target_column=record.target_column,
        cardinality=record.cardinality,
        state=record.state,
        warning_message=record.warning_message,
        created_at=record.created_at,
    )


@router.patch("/relationships/{relationship_uuid}")
async def update_relationship_status(
    relationship_uuid: str,
    payload: UpdateRelationshipStatusSchema,
    user=Depends(get_user),
):
    """Accept or reject a relationship (Requirement 7 Criteria 1 & 2)."""
    org_id = user.selected_organization_id
    if payload.state not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="State must be 'accepted' or 'rejected'.")

    updated = await db_table_client.update_relationship_state(
        relationship_uuid, org_id, payload.state
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Relationship not found.")

    PromptComposer.clear_cache(org_id)
    return {"success": True, "relationship_uuid": relationship_uuid, "state": payload.state}


@router.delete("/relationships/{relationship_uuid}")
async def delete_relationship(relationship_uuid: str, user=Depends(get_user)):
    """Delete a relationship record."""
    org_id = user.selected_organization_id
    deleted = await db_table_client.delete_relationship(relationship_uuid, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Relationship not found.")

    PromptComposer.clear_cache(org_id)
    return {"success": True}


@router.get("/{document_uuid}/validate-relationships")
async def validate_relationships(document_uuid: str, user=Depends(get_user)):
    """Run orphan row validation report for accepted relationships (Requirement 8)."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    all_rels = await db_table_client.list_relationships_for_org(org_id, state="accepted")
    rel_targets = []
    for r in all_rels:
        if r.source_db_table_id == tbl.id or r.target_db_table_id == tbl.id:
            s_t = await db_table_client.get_db_table_by_id(r.source_db_table_id, org_id)
            t_t = await db_table_client.get_db_table_by_id(r.target_db_table_id, org_id)
            if s_t and t_t:
                rel_targets.append({
                    "relationship_uuid": r.relationship_uuid,
                    "source_table_name": s_t.table_name,
                    "source_column": r.source_column,
                    "target_table_name": t_t.table_name,
                    "target_column": r.target_column,
                    "cardinality": r.cardinality,
                })

    if not rel_targets:
        return {"report": []}

    async with db_table_client.async_session() as session:
        conn = await session.connection()
        report = await RelationValidator.validate_table_relationships(conn, org_id, rel_targets)
        return {"report": report}


@router.post("/{document_uuid}/computed-columns")
async def add_computed_column(
    document_uuid: str, payload: CreateComputedColumnSchema, user=Depends(get_user)
):
    """Add a computed column to a DB Table (Requirement 9)."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    existing_comp = await db_table_client.list_computed_columns(tbl.id, org_id)
    if len(existing_comp) >= DerivationBuilder.MAX_COMPUTED_COLUMNS_PER_TABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Table has reached the maximum of {DerivationBuilder.MAX_COMPUTED_COLUMNS_PER_TABLE} computed columns.",
        )

    col_map = {c["name"]: c["type"] for c in tbl.column_schema}
    is_valid, err = DerivationBuilder.validate_rules(payload.rules, col_map)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err)

    # Apply computed column in PostgreSQL
    async with db_table_client.async_session() as session:
        conn = await session.connection()
        await DerivationBuilder.apply_computed_column(
            conn=conn,
            organization_id=org_id,
            table_name=tbl.table_name,
            col_name=payload.target_column_name,
            result_type=payload.result_type,
            combinator=payload.combinator,
            rules=payload.rules,
            column_type_map=col_map,
        )
        await session.commit()

    record = await db_table_client.create_computed_column_record(
        db_table_id=tbl.id,
        organization_id=org_id,
        target_column_name=payload.target_column_name,
        result_type=payload.result_type,
        combinator=payload.combinator,
        rules=payload.rules,
    )

    PromptComposer.clear_cache(org_id)
    return {"success": True, "computed_column_uuid": record.computed_column_uuid}


@router.post("/{document_uuid}/views")
async def create_table_view(
    document_uuid: str, payload: CreateTableViewSchema, user=Depends(get_user)
):
    """Create a filtered Table View over a DB Table (Requirement 9)."""
    org_id = user.selected_organization_id
    tbl = await db_table_client.get_db_table_by_document_uuid(document_uuid, org_id)
    if not tbl:
        raise HTTPException(status_code=404, detail="DB Table not found.")

    existing_views = await db_table_client.list_views(tbl.id, org_id)
    if len(existing_views) >= DerivationBuilder.MAX_VIEWS_PER_TABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Table has reached the maximum of {DerivationBuilder.MAX_VIEWS_PER_TABLE} views.",
        )

    col_map = {c["name"]: c["type"] for c in tbl.column_schema}
    is_valid, err = DerivationBuilder.validate_rules(payload.rules, col_map)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err)

    # Create view in PostgreSQL
    async with db_table_client.async_session() as session:
        conn = await session.connection()
        await DerivationBuilder.create_table_view(
            conn=conn,
            organization_id=org_id,
            view_name=payload.view_name,
            underlying_table_name=tbl.table_name,
            rules=payload.rules,
            column_type_map=col_map,
        )
        await session.commit()

    record = await db_table_client.create_table_view_record(
        db_table_id=tbl.id,
        organization_id=org_id,
        view_name=payload.view_name,
        rules=payload.rules,
    )

    PromptComposer.clear_cache(org_id)
    return {"success": True, "view_uuid": record.view_uuid, "view_name": record.view_name}
