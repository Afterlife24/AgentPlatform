"""add_model_pricing_tables

Dynamic model pricing (Stage A): versioned per-model pricing, per-provider
enable/disable, the sync approval queue, and the immutable usage audit log.

Revision ID: price001
Revises: mrg001_otp_wallet
Create Date: 2026-10-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "price001"
down_revision: Union[str, None] = "mrg001_otp_wallet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum value lists (kept in sync with api/enums.py).
_KIND = ("chat", "embedding", "tts", "stt")
_SOURCE = ("litellm", "manual")
_UNIT = ("per_1m_token", "per_character", "per_second")
_STATUS = ("active", "deprecated")
_CHANGE = ("new", "price_change", "deprecated")


def upgrade() -> None:
    bind = op.get_bind()

    # Create the shared enum types once.
    sa.Enum(*_KIND, name="model_pricing_kind").create(bind)
    sa.Enum(*_SOURCE, name="model_pricing_source").create(bind)
    sa.Enum(*_UNIT, name="model_pricing_unit").create(bind)
    sa.Enum(*_STATUS, name="model_pricing_status").create(bind)
    sa.Enum(*_CHANGE, name="model_pricing_change_type").create(bind)

    kind_enum = postgresql.ENUM(*_KIND, name="model_pricing_kind", create_type=False)
    source_enum = postgresql.ENUM(*_SOURCE, name="model_pricing_source", create_type=False)
    unit_enum = postgresql.ENUM(*_UNIT, name="model_pricing_unit", create_type=False)
    status_enum = postgresql.ENUM(*_STATUS, name="model_pricing_status", create_type=False)
    change_enum = postgresql.ENUM(*_CHANGE, name="model_pricing_change_type", create_type=False)

    # ── model_pricing ────────────────────────────────────────────────────────
    op.create_table(
        "model_pricing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_identifier", sa.String(length=255), nullable=False),
        sa.Column("kind", kind_enum, nullable=False),
        sa.Column("source", source_enum, nullable=False),
        sa.Column("unit", unit_enum, nullable=False),
        sa.Column("input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("output_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("cached_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("embedding_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("rate", sa.Numeric(precision=18, scale=10), nullable=True),
        sa.Column("status", status_enum, server_default=sa.text("'active'"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_pricing_provider", "model_pricing", ["provider"], unique=False)
    op.create_index("ix_model_pricing_model_identifier", "model_pricing", ["model_identifier"], unique=False)
    op.create_index("ix_model_pricing_provider_model", "model_pricing", ["provider", "model_identifier"], unique=False)
    op.create_index("ix_model_pricing_kind", "model_pricing", ["kind"], unique=False)
    op.create_index("ix_model_pricing_status", "model_pricing", ["status"], unique=False)
    op.create_index("ix_model_pricing_is_enabled", "model_pricing", ["is_enabled"], unique=False)
    op.create_index(op.f("ix_model_pricing_id"), "model_pricing", ["id"], unique=False)
    # Only one current (effective_to IS NULL) row per provider+model.
    op.create_index(
        "uq_model_pricing_current",
        "model_pricing",
        ["provider", "model_identifier"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    # ── model_provider_settings ──────────────────────────────────────────────
    op.create_table(
        "model_provider_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_model_provider_settings_provider"),
    )
    op.create_index(op.f("ix_model_provider_settings_id"), "model_provider_settings", ["id"], unique=False)
    op.create_index(op.f("ix_model_provider_settings_provider"), "model_provider_settings", ["provider"], unique=True)

    # ── model_pricing_pending ────────────────────────────────────────────────
    op.create_table(
        "model_pricing_pending",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_identifier", sa.String(length=255), nullable=False),
        sa.Column("kind", kind_enum, nullable=False),
        sa.Column("change_type", change_enum, nullable=False),
        sa.Column("proposed_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("proposed_output_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("proposed_cached_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("proposed_embedding_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("proposed_status", status_enum, nullable=True),
        sa.Column("current_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("current_output_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("current_cached_input_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("current_embedding_per_1m", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("sync_run_id", sa.String(length=64), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_pricing_pending_id"), "model_pricing_pending", ["id"], unique=False)
    op.create_index(op.f("ix_model_pricing_pending_provider"), "model_pricing_pending", ["provider"], unique=False)
    op.create_index(op.f("ix_model_pricing_pending_model_identifier"), "model_pricing_pending", ["model_identifier"], unique=False)
    op.create_index(op.f("ix_model_pricing_pending_sync_run_id"), "model_pricing_pending", ["sync_run_id"], unique=False)
    op.create_index("ix_model_pricing_pending_provider_model", "model_pricing_pending", ["provider", "model_identifier"], unique=False)
    op.create_index("ix_model_pricing_pending_sync_run", "model_pricing_pending", ["sync_run_id"], unique=False)

    # ── model_usage_audit ────────────────────────────────────────────────────
    op.create_table(
        "model_usage_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("chat_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("tts_model", sa.String(length=255), nullable=True),
        sa.Column("stt_model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
        sa.Column("tts_characters", sa.Integer(), nullable=True),
        sa.Column("stt_seconds", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("chat_pricing_version_id", sa.Integer(), nullable=True),
        sa.Column("computed_charge_usd", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_pricing_version_id"], ["model_pricing.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_usage_audit_id"), "model_usage_audit", ["id"], unique=False)
    op.create_index("ix_model_usage_audit_workflow_run_id", "model_usage_audit", ["workflow_run_id"], unique=False)
    op.create_index("ix_model_usage_audit_created_at", "model_usage_audit", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_model_usage_audit_created_at", table_name="model_usage_audit")
    op.drop_index("ix_model_usage_audit_workflow_run_id", table_name="model_usage_audit")
    op.drop_index(op.f("ix_model_usage_audit_id"), table_name="model_usage_audit")
    op.drop_table("model_usage_audit")

    op.drop_index("ix_model_pricing_pending_sync_run", table_name="model_pricing_pending")
    op.drop_index("ix_model_pricing_pending_provider_model", table_name="model_pricing_pending")
    op.drop_index(op.f("ix_model_pricing_pending_sync_run_id"), table_name="model_pricing_pending")
    op.drop_index(op.f("ix_model_pricing_pending_model_identifier"), table_name="model_pricing_pending")
    op.drop_index(op.f("ix_model_pricing_pending_provider"), table_name="model_pricing_pending")
    op.drop_index(op.f("ix_model_pricing_pending_id"), table_name="model_pricing_pending")
    op.drop_table("model_pricing_pending")

    op.drop_index(op.f("ix_model_provider_settings_provider"), table_name="model_provider_settings")
    op.drop_index(op.f("ix_model_provider_settings_id"), table_name="model_provider_settings")
    op.drop_table("model_provider_settings")

    op.drop_index("uq_model_pricing_current", table_name="model_pricing", postgresql_where=sa.text("effective_to IS NULL"))
    op.drop_index(op.f("ix_model_pricing_id"), table_name="model_pricing")
    op.drop_index("ix_model_pricing_is_enabled", table_name="model_pricing")
    op.drop_index("ix_model_pricing_status", table_name="model_pricing")
    op.drop_index("ix_model_pricing_kind", table_name="model_pricing")
    op.drop_index("ix_model_pricing_provider_model", table_name="model_pricing")
    op.drop_index("ix_model_pricing_model_identifier", table_name="model_pricing")
    op.drop_index("ix_model_pricing_provider", table_name="model_pricing")
    op.drop_table("model_pricing")

    bind = op.get_bind()
    sa.Enum(name="model_pricing_change_type").drop(bind)
    sa.Enum(name="model_pricing_status").drop(bind)
    sa.Enum(name="model_pricing_unit").drop(bind)
    sa.Enum(name="model_pricing_source").drop(bind)
    sa.Enum(name="model_pricing_kind").drop(bind)
