"""merge otp001 and credit wallet heads

The OTP columns (otp001) and the prepaid credit wallet tables (2a5fcf617481)
were both branched from csv003_add_value_profile, leaving two Alembic heads.
`alembic upgrade head` refuses to run with more than one head, which made the
API crash-loop on startup. This empty merge revision joins them back into a
single head. It changes no schema.

Revision ID: mrg001_otp_wallet
Revises: otp001, 2a5fcf617481
Create Date: 2026-10-27 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "mrg001_otp_wallet"
down_revision: Union[str, Sequence[str], None] = ("otp001", "2a5fcf617481")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
