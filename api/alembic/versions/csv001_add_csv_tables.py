"""add csv_tables and csv_table_rows

Revision ID: csv001_add_csv_tables
Revises: rag003_ctx_tsvector
Create Date: 2026-08-14

Two tables that implement the CSV Table tool — a text-to-SQL counterpart
to the RAG knowledge-base tool. csv_tables stores metadata about each
uploaded CSV (name, column schema), csv_table_rows stores the actual
data rows as JSONB.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "csv001_add_csv_tables"
down_revision: Union[str, None] = "rag003_ctx_tsvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # csv_tables: one row per uploaded CSV file
    op.create_table(
        "csv_tables",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "table_uuid",
            sa.String(length=36),
            nullable=False,
            comment="Public identifier used in workflow node csv_table_uuids",
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False, comment="Original filename or user-supplied name"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "column_schema",
            sa.JSON(),
            nullable=False,
            server_default="{}",
            comment="JSON array of {name, type} describing each column",
        ),
        sa.Column("processing_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_csv_tables_table_uuid", "csv_tables", ["table_uuid"], unique=True)
    op.create_index("ix_csv_tables_organization_id", "csv_tables", ["organization_id"])
    op.create_index("ix_csv_tables_status", "csv_tables", ["processing_status"])

    # csv_table_rows: one row per data row in the CSV
    op.create_table(
        "csv_table_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("table_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column(
            "row_data",
            sa.JSON(),
            nullable=False,
            comment="Full CSV row as JSON object {column_name: value}",
        ),
        sa.ForeignKeyConstraint(["table_id"], ["csv_tables.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_csv_table_rows_table_id", "csv_table_rows", ["table_id"])
    op.create_index("ix_csv_table_rows_org_id", "csv_table_rows", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_csv_table_rows_org_id", table_name="csv_table_rows")
    op.drop_index("ix_csv_table_rows_table_id", table_name="csv_table_rows")
    op.drop_table("csv_table_rows")
    op.drop_index("ix_csv_tables_status", table_name="csv_tables")
    op.drop_index("ix_csv_tables_organization_id", table_name="csv_tables")
    op.drop_index("ix_csv_tables_table_uuid", table_name="csv_tables")
    op.drop_table("csv_tables")
