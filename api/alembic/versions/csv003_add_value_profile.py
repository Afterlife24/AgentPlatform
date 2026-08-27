"""csv003 — add csv_tables.value_profile

Persists the per-column value profile that gets injected into the node
system prompt, so the LLM sees the table's actual VALUES and never has to
guess one. Nullable: tables ingested before this migration have NULL here
and are profiled lazily on first node entry, then backfilled.

Revision ID: csv003_add_value_profile
Revises:     csv002_row_data_json_to_jsonb
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "csv003_add_value_profile"
down_revision: Union[str, None] = "csv002_row_data_json_to_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no server_default: NULL means "not profiled yet" and is the
    # signal the lazy fallback keys off. A default of '[]' would be
    # indistinguishable from "profiled and every column was skipped".
    op.add_column(
        "csv_tables",
        sa.Column(
            "value_profile",
            sa.JSON(),
            nullable=True,
            comment="Per-column value profile injected into the node prompt",
        ),
    )


def downgrade() -> None:
    op.drop_column("csv_tables", "value_profile")
