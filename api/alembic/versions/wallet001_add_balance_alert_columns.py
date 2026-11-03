"""add balance alert tracking columns to organization_credit_accounts

Tracks when the low-balance warning and balance-exhausted emails were sent so
each alert is delivered once per drop. NULL means the alert is armed.

Revision ID: wallet001
Revises: merge001
Create Date: 2026-11-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wallet001"
down_revision: Union[str, None] = "merge001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organization_credit_accounts",
        sa.Column(
            "low_balance_alert_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the low-balance warning email was sent. NULL = armed",
        ),
    )
    op.add_column(
        "organization_credit_accounts",
        sa.Column(
            "exhausted_alert_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the balance-exhausted email was sent. NULL = armed",
        ),
    )


def downgrade() -> None:
    op.drop_column("organization_credit_accounts", "exhausted_alert_sent_at")
    op.drop_column("organization_credit_accounts", "low_balance_alert_sent_at")
