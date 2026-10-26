"""speech billing: rate metadata + ledger usage_breakdown + seed STT/TTS rates

Adds rate provenance columns (rate_last_checked_at, notes, updated_by) to
model_pricing, a usage_breakdown JSON column to credit_ledger_entries, and
seeds default STT/TTS vendor rates (speech-billing spec §6) in the new units:
  - TTS rate = USD per 1,000,000 characters
  - STT rate = USD per hour of audio

Revision ID: price003
Revises: price002
Create Date: 2026-10-23

"""
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "price003"
down_revision: Union[str, None] = "price002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Seed rates (October 2026 vendor list prices, converted to our units).
# TTS -> USD per 1,000,000 characters ; STT -> USD per hour of audio.
# (provider, kind, model, rate, notes)
_SEED = [
    # Deepgram TTS
    ("deepgram", "tts", "aura-1", "15.00", "Deepgram aura-1 list price"),
    ("deepgram", "tts", "aura-2", "30.00", "Deepgram aura-2 list price"),
    ("deepgram", "tts", "flux-tts", "45.00", "Deepgram flux-tts list price"),
    # Deepgram STT (regular, NOT promo — spec §6.1)
    ("deepgram", "stt", "nova-3", "0.46", "Deepgram nova-3 mono regular rate (not promo)"),
    ("deepgram", "stt", "nova-3-multi", "0.55", "Deepgram nova-3 multilingual regular rate"),
    ("deepgram", "stt", "flux", "0.46", "Deepgram flux English regular rate"),
    ("deepgram", "stt", "flux-multi", "0.47", "Deepgram flux multilingual rate"),
    # ElevenLabs TTS
    ("elevenlabs", "tts", "eleven_flash_v2_5", "40.00", "ElevenLabs Flash/Turbo tier"),
    ("elevenlabs", "tts", "eleven_turbo_v2_5", "40.00", "ElevenLabs Flash/Turbo tier"),
    ("elevenlabs", "tts", "eleven_v3", "80.00", "ElevenLabs v3 tier"),
    ("elevenlabs", "tts", "eleven_multilingual_v2", "80.00", "ElevenLabs v2 multilingual tier"),
    # ElevenLabs STT
    ("elevenlabs", "stt", "scribe_v2_realtime", "0.39", "ElevenLabs Scribe v2 Realtime"),
    ("elevenlabs", "stt", "scribe_v2", "0.22", "ElevenLabs Scribe v2 batch"),
    # Cartesia — priced at PRO tier on purpose (account is on Free = $0). Pro is
    # the dearest paid tier, so upgrading later only improves margin. Verify
    # $5/100K credits on cartesia.ai/pricing before go-live (spec §6.3).
    ("cartesia", "tts", "sonic-3.5", "50.00",
     "Cartesia Pro tier ($5/100K credits = $0.00005/credit, ~1 credit/char). "
     "Account is on Free (marginal cost $0); Pro is dearest paid tier so upgrades improve margin. Verify before go-live."),
    ("cartesia", "tts", "sonic-3", "50.00", "Cartesia Pro tier; see sonic-3.5 note"),
    ("cartesia", "stt", "ink-whisper", "0.18",
     "Cartesia Pro tier: 1 credit/sec * $0.00005 * 3600 = $0.18/hr. See sonic-3.5 note."),
    ("cartesia", "stt", "ink-2", "0.54",
     "Cartesia Pro tier: 3 credits/sec * $0.00005 * 3600 = $0.54/hr. See sonic-3.5 note."),
]


def upgrade() -> None:
    # New unit enum values for the spec units (per-1M-char TTS, per-hour STT).
    # ADD VALUE can't run in a txn block on older PG, so commit first.
    op.execute("COMMIT")
    op.execute("ALTER TYPE model_pricing_unit ADD VALUE IF NOT EXISTS 'per_1m_char'")
    op.execute("ALTER TYPE model_pricing_unit ADD VALUE IF NOT EXISTS 'per_hour'")

    op.add_column(
        "model_pricing",
        sa.Column("rate_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("model_pricing", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("model_pricing", sa.Column("updated_by", sa.String(length=255), nullable=True))

    op.add_column(
        "credit_ledger_entries",
        sa.Column("usage_breakdown", sa.JSON(), nullable=True),
    )

    # Widen rate scale from 10 to 6 (spec units are coarse: per-1M / per-hour).
    op.alter_column(
        "model_pricing",
        "rate",
        type_=sa.Numeric(precision=18, scale=6),
        existing_type=sa.Numeric(precision=18, scale=10),
        existing_nullable=True,
    )

    # Seed default STT/TTS rates (idempotent: skip a provider+model that already
    # has a current row). Seeded models are ACTIVE but DISABLED so the operator
    # explicitly enables what they use (matches the chat-model flow).
    conn = op.get_bind()
    now = datetime.now(UTC)
    for provider, kind, model, rate, notes in _SEED:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM model_pricing WHERE provider=:p AND model_identifier=:m "
                "AND effective_to IS NULL"
            ),
            {"p": provider, "m": model},
        ).first()
        if exists:
            continue
        unit = "per_1m_char" if kind == "tts" else "per_hour"
        conn.execute(
            sa.text(
                """
                INSERT INTO model_pricing
                    (provider, model_identifier, kind, source, unit, rate, status,
                     is_enabled, effective_from, created_at, updated_at,
                     rate_last_checked_at, notes)
                VALUES
                    (:provider, :model, :kind, 'manual', :unit, :rate, 'active',
                     false, :now, :now, :now, :now, :notes)
                """
            ),
            {
                "provider": provider, "model": model, "kind": kind,
                "unit": unit, "rate": rate, "now": now, "notes": notes,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM model_pricing WHERE source='manual' AND kind IN ('tts','stt')"
        )
    )
    op.alter_column(
        "model_pricing",
        "rate",
        type_=sa.Numeric(precision=18, scale=10),
        existing_type=sa.Numeric(precision=18, scale=6),
        existing_nullable=True,
    )
    op.drop_column("credit_ledger_entries", "usage_breakdown")
    op.drop_column("model_pricing", "updated_by")
    op.drop_column("model_pricing", "notes")
    op.drop_column("model_pricing", "rate_last_checked_at")
