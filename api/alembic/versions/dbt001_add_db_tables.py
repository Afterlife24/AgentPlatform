"""dbt001 - add relational dataset tool tables and query role

Adds db_tables, db_table_relationships, db_table_computed_columns,
and db_table_views tables to support physical typed tables and relational queries.

Revision ID: dbt001_add_db_tables
Revises: csv003_add_value_profile
"""

from typing import Sequence, Union
import os

import sqlalchemy as sa
from alembic import op

revision: str = "dbt001_add_db_tables"
down_revision: Union[str, None] = "csv003_add_value_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Ensure Query Role exists
    query_role = os.getenv("RELATIONAL_DATASET_QUERY_ROLE", "csv_query_role")
    query_pass = os.getenv("RELATIONAL_DATASET_QUERY_ROLE_PASSWORD", "csv_query_pass_secure_2026")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{query_role}') THEN
                CREATE ROLE {query_role} WITH LOGIN PASSWORD '{query_pass}';
            END IF;
        END
        $$;
        """
    )

    # 2. Create db_tables
    op.create_table(
        "db_tables",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("db_table_uuid", sa.String(length=36), nullable=False),
        sa.Column("document_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.String(length=63), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("column_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("column_schema", sa.JSON(), nullable=False),
        sa.Column("type_report", sa.JSON(), nullable=True),
        sa.Column("load_report", sa.JSON(), nullable=True),
        sa.Column("value_profile", sa.JSON(), nullable=True),
        sa.Column("suggested_primary_key", sa.String(length=63), nullable=True),
        sa.Column("confirmed_primary_key", sa.String(length=63), nullable=True),
        sa.Column("forced_text_columns", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_db_tables_id", "db_tables", ["id"])
    op.create_index("ix_db_tables_db_table_uuid", "db_tables", ["db_table_uuid"], unique=True)
    op.create_index("ix_db_tables_document_uuid", "db_tables", ["document_uuid"], unique=True)
    op.create_index("ix_db_tables_organization_id", "db_tables", ["organization_id"])
    op.create_index("ix_db_tables_org_table_name", "db_tables", ["organization_id", "table_name"])
    op.create_index("ix_db_tables_state", "db_tables", ["state"])

    # 3. Create db_table_relationships
    op.create_table(
        "db_table_relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relationship_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("source_db_table_id", sa.Integer(), nullable=False),
        sa.Column("source_column", sa.String(length=63), nullable=False),
        sa.Column("target_db_table_id", sa.Integer(), nullable=False),
        sa.Column("target_column", sa.String(length=63), nullable=False),
        sa.Column("cardinality", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="suggested", nullable=False),
        sa.Column("containment_ratio", sa.Float(), nullable=True),
        sa.Column("distinct_source_count", sa.Integer(), nullable=True),
        sa.Column("absent_source_count", sa.Integer(), nullable=True),
        sa.Column("name_similarity_score", sa.Float(), nullable=True),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_db_table_id"], ["db_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_db_table_id"], ["db_tables.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_db_table_id",
            "source_column",
            "target_db_table_id",
            "target_column",
            name="uq_db_table_relationship_columns",
        ),
    )
    op.create_index("ix_db_table_relationships_id", "db_table_relationships", ["id"])
    op.create_index("ix_db_table_relationships_uuid", "db_table_relationships", ["relationship_uuid"], unique=True)
    op.create_index("ix_db_table_relationships_org_id", "db_table_relationships", ["organization_id"])
    op.create_index("ix_db_table_relationships_source_id", "db_table_relationships", ["source_db_table_id"])
    op.create_index("ix_db_table_relationships_target_id", "db_table_relationships", ["target_db_table_id"])
    op.create_index("ix_db_table_rel_org_state", "db_table_relationships", ["organization_id", "state"])

    # 4. Create db_table_computed_columns
    op.create_table(
        "db_table_computed_columns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("computed_column_uuid", sa.String(length=36), nullable=False),
        sa.Column("db_table_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("target_column_name", sa.String(length=63), nullable=False),
        sa.Column("result_type", sa.String(length=20), nullable=False),
        sa.Column("combinator", sa.String(length=10), server_default="all", nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["db_table_id"], ["db_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("db_table_id", "target_column_name", name="uq_db_table_computed_col_name"),
    )
    op.create_index("ix_db_table_computed_columns_id", "db_table_computed_columns", ["id"])
    op.create_index("ix_db_table_computed_columns_uuid", "db_table_computed_columns", ["computed_column_uuid"], unique=True)
    op.create_index("ix_db_table_computed_columns_table_id", "db_table_computed_columns", ["db_table_id"])
    op.create_index("ix_db_table_computed_columns_org_id", "db_table_computed_columns", ["organization_id"])

    # 5. Create db_table_views
    op.create_table(
        "db_table_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("view_uuid", sa.String(length=36), nullable=False),
        sa.Column("db_table_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("view_name", sa.String(length=63), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["db_table_id"], ["db_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_db_table_views_id", "db_table_views", ["id"])
    op.create_index("ix_db_table_views_uuid", "db_table_views", ["view_uuid"], unique=True)
    op.create_index("ix_db_table_views_table_id", "db_table_views", ["db_table_id"])
    op.create_index("ix_db_table_views_org_id", "db_table_views", ["organization_id"])
    op.create_index("ix_db_table_views_org_view_name", "db_table_views", ["organization_id", "view_name"])


def downgrade() -> None:
    op.drop_table("db_table_views")
    op.drop_table("db_table_computed_columns")
    op.drop_table("db_table_relationships")
    op.drop_table("db_tables")
