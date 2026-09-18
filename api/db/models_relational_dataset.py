"""SQLAlchemy models for the Relational Dataset Tool.

Defines metadata models for DB Tables, Relationships, Computed Columns, and Table Views.
These models live in the application schema ('public'), while actual data rows
are stored in real physical PostgreSQL tables inside the tenant-specific 'csv_org_{organization_id}' schema.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from api.db.models import Base


class DbTableModel(Base):
    """Metadata for a physical PostgreSQL table created from a CSV upload in 'database' mode."""

    __tablename__ = "db_tables"

    id = Column(Integer, primary_key=True, index=True)

    # Public unique identifier for the DB Table metadata
    db_table_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Link to the knowledge base document that owns this table
    document_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
    )

    # Tenant scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Physical PostgreSQL table name inside csv_org_{organization_id}
    table_name = Column(String(63), nullable=False)
    source_filename = Column(String(500), nullable=False)

    # Lifecycle state: 'pending', 'loaded', 'loaded_degraded', 'failed'
    state = Column(String(20), nullable=False, default="pending", server_default="pending")

    row_count = Column(Integer, nullable=False, default=0)
    column_count = Column(Integer, nullable=False, default=0)

    # Column schema metadata: list of dicts with sanitized identifier, original header, assigned type, etc.
    column_schema = Column(JSON, nullable=False, default=list)

    # Outcome reports
    type_report = Column(JSON, nullable=True)
    load_report = Column(JSON, nullable=True)
    value_profile = Column(JSON, nullable=True)

    # Primary key metadata
    suggested_primary_key = Column(String(63), nullable=True)
    confirmed_primary_key = Column(String(63), nullable=True)

    # User-forced text columns for re-parse
    forced_text_columns = Column(JSON, nullable=True, default=list)

    # Failure details
    failure_reason = Column(Text, nullable=True)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")
    relationships_as_source = relationship(
        "DbTableRelationshipModel",
        foreign_keys="DbTableRelationshipModel.source_db_table_id",
        cascade="all, delete-orphan",
        back_populates="source_table",
    )
    relationships_as_target = relationship(
        "DbTableRelationshipModel",
        foreign_keys="DbTableRelationshipModel.target_db_table_id",
        cascade="all, delete-orphan",
        back_populates="target_table",
    )
    computed_columns = relationship(
        "DbTableComputedColumnModel",
        cascade="all, delete-orphan",
        back_populates="table",
    )
    views = relationship(
        "DbTableViewModel",
        cascade="all, delete-orphan",
        back_populates="table",
    )

    __table_args__ = (
        Index("ix_db_tables_org_table_name", "organization_id", "table_name"),
        Index("ix_db_tables_state", "state"),
    )


class DbTableRelationshipModel(Base):
    """Stored relationship metadata between two DB Tables belonging to the same organization."""

    __tablename__ = "db_table_relationships"

    id = Column(Integer, primary_key=True, index=True)

    relationship_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source_db_table_id = Column(
        Integer, ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_column = Column(String(63), nullable=False)

    target_db_table_id = Column(
        Integer, ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_column = Column(String(63), nullable=False)

    # Cardinality label: 'one_to_one', 'one_to_many', 'many_to_one'
    cardinality = Column(String(20), nullable=False)

    # Lifecycle state: 'suggested', 'accepted', 'rejected', 'broken'
    state = Column(String(20), nullable=False, default="suggested", server_default="suggested")

    # Metrics
    containment_ratio = Column(Float, nullable=True)
    distinct_source_count = Column(Integer, nullable=True)
    absent_source_count = Column(Integer, nullable=True)
    name_similarity_score = Column(Float, nullable=True)
    warning_message = Column(Text, nullable=True)

    # Audit
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    source_table = relationship("DbTableModel", foreign_keys=[source_db_table_id], back_populates="relationships_as_source")
    target_table = relationship("DbTableModel", foreign_keys=[target_db_table_id], back_populates="relationships_as_target")

    __table_args__ = (
        Index("ix_db_table_rel_org_state", "organization_id", "state"),
        UniqueConstraint("organization_id", "source_db_table_id", "source_column", "target_db_table_id", "target_column", name="uq_db_table_relationship_columns"),
    )


class DbTableComputedColumnModel(Base):
    """User-defined computed column rule added to a DB Table at load time."""

    __tablename__ = "db_table_computed_columns"

    id = Column(Integer, primary_key=True, index=True)

    computed_column_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    db_table_id = Column(
        Integer, ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    target_column_name = Column(String(63), nullable=False)
    result_type = Column(String(20), nullable=False)  # 'boolean', 'text', etc.
    combinator = Column(String(10), nullable=False, default="all")  # 'all', 'any'

    # Rules JSON: list of {"source_column": str, "operator": str, "operand": str}
    rules = Column(JSON, nullable=False, default=list)

    # State: 'active', 'broken'
    state = Column(String(20), nullable=False, default="active", server_default="active")

    # Audit
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    table = relationship("DbTableModel", back_populates="computed_columns")

    __table_args__ = (
        UniqueConstraint("db_table_id", "target_column_name", name="uq_db_table_computed_col_name"),
    )


class DbTableViewModel(Base):
    """User-defined filtered Table View over a DB Table inside csv_org_{organization_id}."""

    __tablename__ = "db_table_views"

    id = Column(Integer, primary_key=True, index=True)

    view_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    db_table_id = Column(
        Integer, ForeignKey("db_tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Name of the view in PostgreSQL inside csv_org_{organization_id}
    view_name = Column(String(63), nullable=False)

    # Conjunction of 1-10 rules: list of {"source_column": str, "operator": str, "operand": str}
    rules = Column(JSON, nullable=False, default=list)

    # State: 'active', 'broken'
    state = Column(String(20), nullable=False, default="active", server_default="active")

    # Audit
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    table = relationship("DbTableModel", back_populates="views")

    __table_args__ = (
        Index("ix_db_table_views_org_view_name", "organization_id", "view_name"),
    )
