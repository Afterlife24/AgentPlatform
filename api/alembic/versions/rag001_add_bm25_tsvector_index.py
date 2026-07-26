"""add bm25 tsvector gin index on knowledge_base_chunks

Revision ID: rag001_bm25_tsvector
Revises: a4b5c6d7e8f9
Create Date: 2026-07-25 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "rag001_bm25_tsvector"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add a GENERATED ALWAYS tsvector column for BM25-style full-text search.
    # PostgreSQL computes and stores it automatically on every insert/update.
    op.execute("""
        ALTER TABLE knowledge_base_chunks
        ADD COLUMN IF NOT EXISTS chunk_ts tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(chunk_text, ''))) STORED
    """)

    # GIN index makes tsvector queries fast even on large chunk tables.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_kb_chunks_chunk_ts
        ON knowledge_base_chunks USING GIN (chunk_ts)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_chunk_ts")
    op.execute(
        "ALTER TABLE knowledge_base_chunks DROP COLUMN IF EXISTS chunk_ts"
    )
