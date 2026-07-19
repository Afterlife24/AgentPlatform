"""Add GIN index on chunk_metadata for metadata filtering

Revision ID: a4b5c6d7e8f9
Revises: cdcf9f65913b, b7e3c9a1d2f4, f2e1d0c9b8a7
Create Date: 2025-07-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = (
    "cdcf9f65913b",
    "b7e3c9a1d2f4",
    "f2e1d0c9b8a7",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Temporarily increase maintenance_work_mem for the ALTER + index build.
    op.execute("SET maintenance_work_mem = '256MB'")

    # The chunk_metadata column is JSON type (not JSONB).
    # Convert to JSONB for proper indexing support and faster queries.
    op.execute(
        "ALTER TABLE knowledge_base_chunks "
        "ALTER COLUMN chunk_metadata TYPE jsonb USING chunk_metadata::jsonb"
    )

    # GIN index enables fast JSON containment and key lookups for filtering.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_chunks_metadata_gin "
        "ON knowledge_base_chunks USING GIN (chunk_metadata jsonb_path_ops)"
    )

    # Reset to default
    op.execute("RESET maintenance_work_mem")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_metadata_gin")
    op.execute(
        "ALTER TABLE knowledge_base_chunks "
        "ALTER COLUMN chunk_metadata TYPE json USING chunk_metadata::json"
    )
