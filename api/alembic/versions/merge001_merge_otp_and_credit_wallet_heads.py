"""merge otp001 and credit-wallet heads

Two migrations branch off csv003_add_value_profile, creating two Alembic heads:
  - otp001           (add otp columns to users)
  - 2a5fcf617481     (add credit wallet tables)
Neither depends on the other, so `alembic upgrade head` fails with
"Multiple head revisions are present". This no-op merge revision joins both
heads into a single new head so upgrades are linear again.

Revision ID: merge001
Revises: otp001, 2a5fcf617481
Create Date: 2026-10-24 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "merge001"
down_revision: Union[str, Sequence[str], None] = ("otp001", "price004")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No schema changes — this revision only merges two migration branches.
    pass


def downgrade() -> None:
    # No schema changes to reverse.
    pass
