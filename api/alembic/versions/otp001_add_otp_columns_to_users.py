"""add otp columns to users table

Revision ID: otp001
Revises: rag003
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "otp001"
down_revision: Union[str, None] = "csv002_row_data_json_to_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("otp_code", sa.String(6), nullable=True))
    op.add_column("users", sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True))
    # purpose: "signup" | "reset"
    op.add_column("users", sa.Column("otp_purpose", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "otp_purpose")
    op.drop_column("users", "otp_expires_at")
    op.drop_column("users", "otp_code")
