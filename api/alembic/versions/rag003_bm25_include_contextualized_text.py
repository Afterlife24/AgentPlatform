"""expand bm25 tsvector to include contextualized_text

Revision ID: rag003_ctx_tsvector
Revises: rag002_retrieval_logs
Create Date: 2026-07-26

The original chunk_ts generated column only indexes chunk_text. This means
BM25 can't find matches in the contextualized_text which contains the model
name and product context added during ingestion.

This migration drops and recreates chunk_ts to index BOTH chunk_text and
contextualized_text, so BM25 queries like "Kobelco CKE1350 travel speed"
match chunks where the model name is only in the contextualised summary.
"""

from alembic import op


revision = "rag003_ctx_tsvector"
down_revision = "rag002_retrieval_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Increase maintenance_work_mem for this session — building a tsvector
    # generated column over many rows requires more than the default 64MB.
    op.execute("SET maintenance_work_mem = '256MB'")

    # Drop the old generated column and GIN index
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_chunk_ts")
    op.execute(
        "ALTER TABLE knowledge_base_chunks DROP COLUMN IF EXISTS chunk_ts"
    )

    # Recreate with combined text (chunk_text + contextualized_text)
    op.execute("""
        ALTER TABLE knowledge_base_chunks
        ADD COLUMN chunk_ts tsvector
            GENERATED ALWAYS AS (
                to_tsvector('english',
                    coalesce(chunk_text, '') || ' ' || coalesce(contextualized_text, '')
                )
            ) STORED
    """)

    # Recreate the GIN index for fast BM25 lookups
    op.execute("""
        CREATE INDEX ix_kb_chunks_chunk_ts
        ON knowledge_base_chunks USING GIN (chunk_ts)
    """)


def downgrade() -> None:
    # Revert to chunk_text only
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_chunk_ts")
    op.execute(
        "ALTER TABLE knowledge_base_chunks DROP COLUMN IF EXISTS chunk_ts"
    )
    op.execute("""
        ALTER TABLE knowledge_base_chunks
        ADD COLUMN chunk_ts tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(chunk_text, ''))) STORED
    """)
    op.execute("""
        CREATE INDEX ix_kb_chunks_chunk_ts
        ON knowledge_base_chunks USING GIN (chunk_ts)
    """)
