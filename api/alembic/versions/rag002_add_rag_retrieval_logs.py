"""add rag_retrieval_logs table for feedback loop

Revision ID: rag002_retrieval_logs
Revises: rag001_bm25_tsvector
Create Date: 2026-07-25 12:01:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "rag002_retrieval_logs"
down_revision: Union[str, None] = "rag001_bm25_tsvector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_retrieval_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("expanded_queries", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("route", sa.String(50), nullable=False, server_default="semantic"),
        sa.Column("retrieved_chunks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("avg_rerank_score", sa.Float(), nullable=True),
        sa.Column("candidates_fetched", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_rag_logs_organization_id", "rag_retrieval_logs", ["organization_id"]
    )
    op.create_index(
        "ix_rag_logs_workflow_run_id", "rag_retrieval_logs", ["workflow_run_id"]
    )
    op.create_index("ix_rag_logs_route", "rag_retrieval_logs", ["route"])
    op.create_index("ix_rag_logs_created_at", "rag_retrieval_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_rag_logs_created_at", table_name="rag_retrieval_logs")
    op.drop_index("ix_rag_logs_route", table_name="rag_retrieval_logs")
    op.drop_index("ix_rag_logs_workflow_run_id", table_name="rag_retrieval_logs")
    op.drop_index("ix_rag_logs_organization_id", table_name="rag_retrieval_logs")
    op.drop_table("rag_retrieval_logs")
