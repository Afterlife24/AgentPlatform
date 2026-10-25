"""add 'missing' value to model_pricing_status enum

Adds a MISSING status so the sync can flag a stored LiteLLM model that has
vanished from the catalog (the provider dropped it). Display-only — it does not
block runs.

Revision ID: price002
Revises: price001
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "price002"
down_revision: Union[str, None] = "price001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL; use autocommit to be safe.
    op.execute("COMMIT")
    op.execute("ALTER TYPE model_pricing_status ADD VALUE IF NOT EXISTS 'missing'")


def downgrade() -> None:
    # PostgreSQL has no DROP VALUE for enums; removing 'missing' would require
    # recreating the type. Leave it in place on downgrade (harmless).
    pass
