"""speech-to-speech: realtime pricing kind + four per-1M-token rate columns

Adds the SPEECH_TO_SPEECH kind to model_pricing_kind and four realtime rate
columns (text-in, text-out, audio-in, audio-out, each USD per 1M tokens) so
realtime (OpenAI/Gemini) models can be synced from LiteLLM like chat and billed
with the four separate rates the vendors charge.

Revision ID: price004
Revises: price003
Create Date: 2026-10-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "price004"
down_revision: Union[str, None] = "price003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New kind enum value. ADD VALUE can't run inside a txn block on older PG,
    # so commit the migration's open transaction first.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE model_pricing_kind ADD VALUE IF NOT EXISTS 'speech_to_speech'"
    )

    op.add_column(
        "model_pricing",
        sa.Column("rt_text_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
    )
    op.add_column(
        "model_pricing",
        sa.Column("rt_text_output_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
    )
    op.add_column(
        "model_pricing",
        sa.Column("rt_audio_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
    )
    op.add_column(
        "model_pricing",
        sa.Column("rt_audio_output_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
    )

    # Mirror the four realtime rates on the pending (approval queue) table, for
    # both the proposed and current (comparison) sides.
    for col in (
        "proposed_rt_text_input_per_1m",
        "proposed_rt_text_output_per_1m",
        "proposed_rt_audio_input_per_1m",
        "proposed_rt_audio_output_per_1m",
        "current_rt_text_input_per_1m",
        "current_rt_text_output_per_1m",
        "current_rt_audio_input_per_1m",
        "current_rt_audio_output_per_1m",
    ):
        op.add_column(
            "model_pricing_pending",
            sa.Column(col, sa.Numeric(precision=18, scale=6), nullable=True),
        )


def downgrade() -> None:
    # Drop any speech-to-speech rows first (the enum value can't be removed from
    # a PG enum, but rows referencing it must go before the columns do).
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM model_pricing WHERE kind = 'speech_to_speech'"))
    for col in (
        "proposed_rt_text_input_per_1m",
        "proposed_rt_text_output_per_1m",
        "proposed_rt_audio_input_per_1m",
        "proposed_rt_audio_output_per_1m",
        "current_rt_text_input_per_1m",
        "current_rt_text_output_per_1m",
        "current_rt_audio_input_per_1m",
        "current_rt_audio_output_per_1m",
    ):
        op.drop_column("model_pricing_pending", col)
    op.drop_column("model_pricing", "rt_audio_output_per_1m")
    op.drop_column("model_pricing", "rt_audio_input_per_1m")
    op.drop_column("model_pricing", "rt_text_output_per_1m")
    op.drop_column("model_pricing", "rt_text_input_per_1m")
