"""csv002 — alter csv_table_rows.row_data from json to jsonb.

Issue 2+5 fix: PostgreSQL's json type performs a full sequential scan on
every query. jsonb stores pre-parsed binary data and supports GIN indexes
for fast key/value lookups.

NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
Alembic wraps migrations in transactions by default, so we must use
transaction_per_migration = False for this migration via the
__requires_transaction__ pattern — handled by setting
transaction_per_migration to False in alembic env.py, OR by using
op.execute with autocommit execution options.

We use the simplest portable fix: regular CREATE INDEX (not CONCURRENTLY)
so it runs inside the transaction. For a table with existing data this
locks briefly, but it is safe and correct. On an empty table (fresh install)
it completes instantly.

Revision ID: csv002_row_data_json_to_jsonb
Revises:     csv001_add_csv_tables
"""

from typing import Sequence, Union

from alembic import op

revision: str = "csv002_row_data_json_to_jsonb"
down_revision: Union[str, None] = "csv001_add_csv_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: alter column type from json → jsonb
    # USING row_data::jsonb casts all existing data safely.
    op.execute(
        """
        ALTER TABLE csv_table_rows
        ALTER COLUMN row_data TYPE jsonb
        USING row_data::jsonb
        """
    )

    # Step 2: GIN index on row_data — regular CREATE INDEX (not CONCURRENTLY)
    # so it runs inside the Alembic transaction block without error.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_csv_table_rows_row_data_gin
        ON csv_table_rows
        USING gin (row_data jsonb_path_ops)
        """
    )

    # Step 3: composite btree index on (table_id, organization_id)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_csv_table_rows_table_org
        ON csv_table_rows (table_id, organization_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_csv_table_rows_row_data_gin")
    op.execute("DROP INDEX IF EXISTS ix_csv_table_rows_table_org")

    op.execute(
        """
        ALTER TABLE csv_table_rows
        ALTER COLUMN row_data TYPE json
        USING row_data::text::json
        """
    )
