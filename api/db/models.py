import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

from api.constants import DEFAULT_CAMPAIGN_RETRY_CONFIG

from ..enums import (
    CallType,
    CreditLedgerEntryType,
    IntegrationAction,
    ModelPricingChangeType,
    ModelPricingKind,
    ModelPricingSource,
    ModelPricingStatus,
    ModelPricingUnit,
    ToolCategory,
    ToolStatus,
    TriggerState,
    WebhookCredentialType,
    WorkflowRunState,
    WorkflowStatus,
)

Base = declarative_base()


# TODO: remove workflow_defintion after migration, remove nullable workflow_defintion_id from Workflow and Workflowrun


# Association table for many-to-many relationship between users and organizations
organization_users_association = Table(
    "organization_users",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column(
        "organization_id", Integer, ForeignKey("organizations.id"), primary_key=True
    ),
)


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    workflows = relationship("WorkflowModel", back_populates="user")
    selected_organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    selected_organization = relationship("OrganizationModel", back_populates="users")
    organizations = relationship(
        "OrganizationModel",
        secondary=organization_users_association,
        back_populates="users",
    )
    is_superuser = Column(Boolean, default=False)
    email = Column(String, nullable=True)
    password_hash = Column(String, nullable=True)
    # OTP fields for email verification (signup) and password reset
    otp_code = Column(String(6), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_purpose = Column(String(16), nullable=True)  # "signup" | "reset"

    __table_args__ = (
        Index(
            "ix_users_email_lower",
            func.lower(email),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


class UserConfigurationModel(Base):
    """Per-user keyed JSON store, mirroring organization_configurations.

    Keys are defined in UserConfigurationKey. The legacy v1 AI model
    configuration lives under MODEL_CONFIGURATION; last_validated_at is only
    meaningful for that key.
    """

    __tablename__ = "user_configurations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    key = Column(String, nullable=False)
    configuration = Column(JSON, nullable=False, default=dict)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="_user_configuration_key_uc"),
    )


# New Organization model
class OrganizationModel(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Deprecated: MPS owns quota and credit ledger state.
    quota_type = Column(
        Enum("monthly", "annual", name="quota_type"),
        nullable=False,
        default="monthly",
        server_default=text("'monthly'::quota_type"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_dograh_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_reset_day = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_start_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )

    price_per_second_usd = Column(Float, nullable=True)

    # Relationships
    users = relationship(
        "UserModel",
        secondary=organization_users_association,
        back_populates="organizations",
    )
    integrations = relationship("IntegrationModel", back_populates="organization")
    usage_cycles = relationship(
        "OrganizationUsageCycleModel", back_populates="organization"
    )
    configurations = relationship(
        "OrganizationConfigurationModel", back_populates="organization"
    )
    api_keys = relationship("APIKeyModel", back_populates="organization")


class APIKeyModel(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    key_prefix = Column(String, nullable=False)  # Store first 8 chars for display
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("OrganizationModel", back_populates="api_keys")
    created_by_user = relationship("UserModel")

    # Indexes for performance
    __table_args__ = (
        Index("ix_api_keys_organization_id", "organization_id"),
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_active", "is_active"),
    )


class OrganizationConfigurationModel(Base):
    __tablename__ = "organization_configurations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    key = Column(String, nullable=False)
    value = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel", back_populates="configurations")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="_organization_key_uc"),
        Index("ix_organization_configurations_organization_id", "organization_id"),
    )


class TelephonyConfigurationModel(Base):
    __tablename__ = "telephony_configurations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)
    credentials = Column(JSON, nullable=False, default=dict)
    is_default_outbound = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    phone_numbers = relationship(
        "TelephonyPhoneNumberModel",
        back_populates="configuration",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "name", name="uq_telephony_configurations_org_name"
        ),
        Index("ix_telephony_configurations_org", "organization_id"),
        Index(
            "uq_telephony_configurations_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default_outbound = true"),
        ),
    )


class TelephonyPhoneNumberModel(Base):
    __tablename__ = "telephony_phone_numbers"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    telephony_configuration_id = Column(
        Integer,
        ForeignKey("telephony_configurations.id", ondelete="CASCADE"),
        nullable=False,
    )
    address = Column(String(255), nullable=False)
    address_normalized = Column(String(255), nullable=False)
    address_type = Column(String(16), nullable=False)
    country_code = Column(String(2), nullable=True)
    label = Column(String(64), nullable=True)
    inbound_workflow_id = Column(
        Integer,
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_default_caller_id = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    extra_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    configuration = relationship(
        "TelephonyConfigurationModel", back_populates="phone_numbers"
    )
    inbound_workflow = relationship("WorkflowModel")

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "address_normalized",
            name="uq_phone_numbers_org_address",
        ),
        Index("ix_phone_numbers_config", "telephony_configuration_id"),
        Index(
            "ix_phone_numbers_workflow",
            "inbound_workflow_id",
            postgresql_where=text("inbound_workflow_id IS NOT NULL"),
        ),
        Index(
            "ix_phone_numbers_inbound_lookup",
            "address_normalized",
            "organization_id",
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "uq_phone_numbers_default_caller",
            "telephony_configuration_id",
            unique=True,
            postgresql_where=text("is_default_caller_id = true"),
        ),
    )


class IntegrationModel(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(
        String, nullable=False, index=True
    )  # External connection ID
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    provider = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    connection_details = Column(JSON, nullable=False, default=dict)
    action = Column(String, nullable=False, default=IntegrationAction.ALL_CALLS.value)

    # Relationships
    organization = relationship("OrganizationModel", back_populates="integrations")


class WorkflowDefinitionModel(Base):
    __tablename__ = "workflow_definitions"
    id = Column(Integer, primary_key=True, index=True)
    workflow_hash = Column(String, nullable=True)  # Legacy, no longer used
    workflow_json = Column(JSON, nullable=False, default=dict)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=True)
    is_current = Column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Versioning columns
    status = Column(
        String,
        nullable=False,
        default="published",
        server_default=text("'published'"),
    )  # draft | published | archived
    version_number = Column(
        Integer, nullable=True
    )  # Sequential per workflow, display only
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Full behavioral snapshot (moved from WorkflowModel to enable versioning)
    workflow_configurations = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    template_context_variables = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Table constraints and indexes — unique hash constraint removed (no more dedup)
    __table_args__ = (
        Index("ix_workflow_definitions_workflow_status", "workflow_id", "status"),
    )

    # Relationships
    workflow = relationship(
        "WorkflowModel",
        back_populates="definitions",
        foreign_keys=[workflow_id],
    )
    workflow_runs = relationship("WorkflowRunModel", back_populates="definition")


class FolderModel(Base):
    """A folder for grouping workflows (agents) within an organization.

    Folders are flat (no nesting) and org-scoped. A workflow belongs to at
    most one folder via ``WorkflowModel.folder_id``; a NULL folder_id means
    the workflow is "Uncategorized".
    """

    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    organization = relationship("OrganizationModel")
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    workflows = relationship("WorkflowModel", back_populates="folder")

    # Folder names must be unique within an organization.
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_folder_org_name"),
    )


class WorkflowModel(Base):
    __tablename__ = "workflows"
    id = Column(Integer, primary_key=True, index=True)
    workflow_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("UserModel", back_populates="workflows")
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    organization = relationship("OrganizationModel")
    # Optional folder for grouping in the agents list. NULL = "Uncategorized".
    # ON DELETE SET NULL: deleting a folder un-files its agents, never deletes them.
    folder_id = Column(
        Integer,
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    folder = relationship("FolderModel", back_populates="workflows")
    name = Column(String, index=True, nullable=False)
    status = Column(
        Enum(*[status.value for status in WorkflowStatus], name="workflow_status"),
        nullable=False,
        default=WorkflowStatus.ACTIVE.value,
        server_default=text("'active'::workflow_status"),
    )
    workflow_definition = Column(JSON, nullable=False, default=dict)
    template_context_variables = Column(JSON, nullable=False, default=dict)
    call_disposition_codes = Column(JSON, nullable=False, default=dict)
    workflow_configurations = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    runs = relationship("WorkflowRunModel", back_populates="workflow")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Pointer to the currently-live (published) version
    released_definition_id = Column(
        Integer,
        ForeignKey("workflow_definitions.id", use_alter=True),
        nullable=True,
    )
    released_definition = relationship(
        "WorkflowDefinitionModel",
        foreign_keys=[released_definition_id],
        uselist=False,
        viewonly=True,
    )

    # All versions / historical definitions of this workflow
    definitions = relationship(
        "WorkflowDefinitionModel",
        back_populates="workflow",
        foreign_keys="WorkflowDefinitionModel.workflow_id",
    )

    # Relationship to fetch the current (is_current=True) definition
    # Kept for backward compatibility during transition
    current_definition = relationship(
        "WorkflowDefinitionModel",
        primaryjoin=lambda: and_(
            WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
            WorkflowDefinitionModel.is_current.is_(True),
        ),
        uselist=False,
        viewonly=True,
    )

    @property
    def current_definition_id(self):
        """Return ID of the current workflow definition (helper for backwards-compat)."""
        current_def = self.__dict__.get("current_definition")
        if current_def is not None:
            return current_def.id

        # If relationship is not loaded, we cannot safely access definitions without
        # risking an implicit lazy load on a detached instance. Return ``None`` in
        # that scenario so callers can handle the absence explicitly.
        return None


class WorkflowTemplates(Base):
    __tablename__ = "workflow_templates"
    id = Column(Integer, primary_key=True, index=True)
    template_name = Column(String, nullable=False, index=True)
    template_description = Column(String, nullable=False, index=True)
    template_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    workflow = relationship("WorkflowModel", back_populates="runs")
    definition_id = Column(
        Integer, ForeignKey("workflow_definitions.id"), nullable=True
    )
    definition = relationship("WorkflowDefinitionModel", back_populates="workflow_runs")
    # Stored as VARCHAR (not a Postgres ENUM) so new telephony providers can
    # be added purely in application code without a database migration.
    # See WorkflowRunMode in api/enums.py for the canonical value set.
    mode = Column(String(64), nullable=False)
    call_type = Column(
        Enum(*[call_type.value for call_type in CallType], name="workflow_call_type"),
        nullable=False,
        default=CallType.OUTBOUND.value,
        server_default=text("'outbound'::workflow_call_type"),
    )
    state = Column(
        Enum(*[state.value for state in WorkflowRunState], name="workflow_run_state"),
        nullable=False,
        default=WorkflowRunState.INITIALIZED.value,
        server_default=text("'initialized'::workflow_run_state"),
    )
    is_completed = Column(Boolean, default=False)
    recording_url = Column(String, nullable=True)
    transcript_url = Column(String, nullable=True)
    extra = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    # Store storage backend as string enum (s3, minio)
    storage_backend = Column(
        Enum("s3", "minio", name="storage_backend"),
        nullable=False,
        default="s3",
        server_default=text("'s3'::storage_backend"),
    )
    usage_info = Column(JSON, nullable=False, default=dict)
    cost_info = Column(JSON, nullable=False, default=dict)
    initial_context = Column(JSON, nullable=False, default=dict)
    gathered_context = Column(JSON, nullable=False, default=dict)
    logs = Column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
    annotations = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    campaign = relationship("CampaignModel")
    queued_run_id = Column(Integer, ForeignKey("queued_runs.id"), nullable=True)
    queued_run = relationship("QueuedRunModel", foreign_keys=[queued_run_id])
    public_access_token = Column(String(36), nullable=True)
    text_session = relationship(
        "WorkflowRunTextSessionModel",
        back_populates="workflow_run",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_workflow_runs_public_access_token",
            "public_access_token",
            unique=True,
            postgresql_where=text("public_access_token IS NOT NULL"),
        ),
        Index(
            "idx_workflow_runs_call_id",
            text("(gathered_context->>'call_id')"),
            postgresql_where=text("gathered_context->>'call_id' IS NOT NULL"),
        ),
        Index("idx_workflow_runs_workflow_id", "workflow_id"),
        Index("idx_workflow_runs_campaign_id", "campaign_id"),
    )


class WorkflowRunTextSessionModel(Base):
    __tablename__ = "workflow_run_text_sessions"

    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workflow_run = relationship("WorkflowRunModel", back_populates="text_session")
    revision = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    session_data = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    checkpoint = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("ix_workflow_run_text_sessions_updated_at", "updated_at"),)


class OrganizationUsageCycleModel(Base):
    """
    This model is used to track reporting aggregates for an organization for a given
    usage cycle. Quota fields on this model are deprecated; MPS owns quota and
    credit ledger state.
    """

    __tablename__ = "organization_usage_cycles"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    quota_dograh_tokens = Column(
        Integer,
        nullable=False,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    used_dograh_tokens = Column(Float, nullable=False, default=0)
    total_duration_seconds = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # New USD tracking fields
    used_amount_usd = Column(Float, nullable=True, default=0)
    quota_amount_usd = Column(
        Float,
        nullable=True,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel", back_populates="usage_cycles")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "period_start", "period_end", name="unique_org_period"
        ),
        Index("idx_usage_cycles_org_period", "organization_id", "period_end"),
    )


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Nullable during the legacy → multi-config migration window. Backfilled to the
    # org's default config by the migration; will become NOT NULL in a follow-up.
    telephony_configuration_id = Column(
        Integer, ForeignKey("telephony_configurations.id"), nullable=True
    )

    # Source configuration
    source_type = Column(String, nullable=False, default="csv")
    source_id = Column(String, nullable=False)  # CSV file key

    # State management
    state = Column(
        Enum(
            "created",
            "syncing",
            "running",
            "paused",
            "completed",
            "failed",
            name="campaign_state",
        ),
        nullable=False,
        default="created",
    )

    # Progress tracking
    total_rows = Column(Integer, nullable=True)
    processed_rows = Column(Integer, nullable=False, default=0)
    failed_rows = Column(Integer, nullable=False, default=0)

    # Rate limiting and sync configuration
    rate_limit_per_second = Column(Integer, nullable=False, default=1)
    max_retries = Column(Integer, nullable=False, default=0)
    source_sync_status = Column(String, nullable=False, default="pending")
    source_last_synced_at = Column(DateTime(timezone=True), nullable=True)
    source_sync_error = Column(String, nullable=True)

    # Retry configuration for call failures
    retry_config = Column(
        JSON,
        nullable=False,
        default=DEFAULT_CAMPAIGN_RETRY_CONFIG,
        server_default=text(
            '\'{"enabled": true, "max_retries": 2, "retry_on_busy": true, "retry_on_no_answer": true, "retry_on_voicemail": true, "retry_delay_seconds": 120}\'::jsonb'
        ),
    )

    # Orchestrator tracking fields
    last_batch_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    orchestrator_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Append-only timestamped log entries for state transitions, failures,
    # and circuit-breaker events. Surfaced in the UI so operators can see
    # why a campaign moved to paused/failed without digging through logs.
    logs = Column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::json"),
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    workflow = relationship("WorkflowModel")
    created_by_user = relationship("UserModel")

    # Indexes
    __table_args__ = (
        Index("ix_campaigns_org_id", "organization_id"),
        Index("ix_campaigns_state", "state"),
        Index("ix_campaigns_workflow_id", "workflow_id"),
        Index(
            "ix_campaigns_telephony_config",
            "telephony_configuration_id",
            postgresql_where=text("telephony_configuration_id IS NOT NULL"),
        ),
        # Index for efficient querying of active campaigns
        Index(
            "idx_campaigns_active_status",
            "state",
            postgresql_where=text("state IN ('syncing', 'running', 'paused')"),
        ),
    )


class QueuedRunModel(Base):
    __tablename__ = "queued_runs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    source_uuid = Column(String, nullable=False)
    context_variables = Column(JSON, nullable=False, default=dict)
    state = Column(
        Enum("queued", "processed", "processing", "failed", name="queued_run_state"),
        nullable=False,
        default="queued",
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # New retry-related fields
    retry_count = Column(Integer, default=0, nullable=False, server_default=text("0"))
    parent_queued_run_id = Column(Integer, ForeignKey("queued_runs.id"), nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    retry_reason = Column(String, nullable=True)  # 'busy', 'no_answer', 'voicemail'

    # Relationships
    campaign = relationship("CampaignModel")
    parent_queued_run = relationship("QueuedRunModel", remote_side=[id])

    # Indexes
    __table_args__ = (
        Index("idx_queued_runs_campaign_state", "campaign_id", "state"),
        Index("idx_queued_runs_created", "created_at"),
        Index("idx_queued_runs_source_uuid", "source_uuid"),
        Index(
            "idx_queued_runs_scheduled", "scheduled_for"
        ),  # New index for scheduled retries
        # Optimized index for checking queued runs efficiently
        Index(
            "idx_queued_runs_campaign_state_optimized",
            "campaign_id",
            "state",
            postgresql_where=text("state = 'queued'"),
        ),
        # Optimized index for scheduled retries
        Index(
            "idx_queued_runs_scheduled_optimized",
            "campaign_id",
            "scheduled_for",
            postgresql_where=text("scheduled_for IS NOT NULL"),
        ),
        UniqueConstraint(
            "campaign_id",
            "source_uuid",
            "retry_count",
            name="unique_campaign_source_retry",
        ),
    )


class EmbedTokenModel(Base):
    """Model for storing workflow embed tokens"""

    __tablename__ = "embed_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    workflow_id = Column(
        Integer,
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allowed_domains = Column(JSON, nullable=True)  # Array of whitelisted domains
    settings = Column(JSON, nullable=True)  # Widget customization settings
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    usage_limit = Column(Integer, nullable=True)  # Optional usage limit
    usage_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")
    creator = relationship("UserModel")
    sessions = relationship(
        "EmbedSessionModel", back_populates="embed_token", cascade="all, delete-orphan"
    )


class EmbedSessionModel(Base):
    """Model for storing temporary embed sessions"""

    __tablename__ = "embed_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(String(255), unique=True, nullable=False, index=True)
    embed_token_id = Column(
        Integer, ForeignKey("embed_tokens.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True
    )
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    origin = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # Relationships
    embed_token = relationship("EmbedTokenModel", back_populates="sessions")
    workflow_run = relationship("WorkflowRunModel")


class AgentTriggerModel(Base):
    """Model for storing agent trigger mappings (UUID -> workflow_id).

    This is a minimal lookup table that maps trigger UUIDs to workflows.
    The trigger node in the workflow definition is the source of truth.
    """

    __tablename__ = "agent_triggers"

    id = Column(Integer, primary_key=True, index=True)

    # Globally unique trigger path (UUID format)
    trigger_path = Column(String(36), unique=True, nullable=False, index=True)

    # Link to workflow
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # State management (active/archived)
    state = Column(
        Enum(*[state.value for state in TriggerState], name="trigger_state"),
        nullable=False,
        default=TriggerState.ACTIVE.value,
        server_default=text("'active'::trigger_state"),
    )

    # Audit
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")

    # Indexes for performance
    __table_args__ = (
        Index("ix_agent_triggers_workflow_id", "workflow_id"),
        Index("ix_agent_triggers_state", "state"),
    )


class ExternalCredentialModel(Base):
    """Model for storing external authentication credentials.

    Credentials are stored separately from webhook configurations to allow
    reuse across multiple workflows and secure storage of sensitive data.
    """

    __tablename__ = "external_credentials"

    id = Column(Integer, primary_key=True, index=True)

    # Public UUID reference (used in APIs and workflow definitions)
    # This prevents enumeration attacks and hides internal IDs
    credential_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Credential metadata
    name = Column(String, nullable=False)  # Display name, e.g., "Salesforce API"
    description = Column(String, nullable=True)  # Optional description

    # Credential type - uses enum from api/enums.py
    credential_type = Column(
        Enum(
            *[t.value for t in WebhookCredentialType],
            name="webhook_credential_type",
        ),
        nullable=False,
        default=WebhookCredentialType.NONE.value,
    )

    # Encrypted credential data (JSON)
    # Structure depends on credential_type:
    # - api_key: {"header_name": "X-API-Key", "api_key": "value"}
    # - bearer_token: {"token": "value"}
    # - basic_auth: {"username": "user", "password": "value"}
    # - custom_header: {"header_name": "X-Custom", "header_value": "value"}
    credential_data = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Soft delete for safety
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_webhook_credentials_organization_id", "organization_id"),
        Index("ix_webhook_credentials_uuid", "credential_uuid"),
        UniqueConstraint("organization_id", "name", name="unique_org_credential_name"),
    )


class WebhookDeliveryModel(Base):
    """Durable record of an outbound webhook delivery attempt.

    Final webhooks (e.g. a workflow's "Final Webhook" node) must not be lost to a
    single transient network error. Instead of firing the HTTP request inline and
    forgetting it, we persist one row per webhook node per workflow run and let an
    ARQ task drive delivery with bounded, backed-off retries. The row is the source
    of truth: it survives worker restarts and a periodic sweeper re-enqueues any
    ``pending`` delivery whose ``scheduled_for`` is overdue. After ``max_attempts``
    transient failures (or on a permanent 4xx) the row is parked as ``dead_letter``
    for inspection rather than retried forever.

    Mirrors the campaign retry pattern (``QueuedRunModel``): persisted state,
    ``scheduled_for`` gating, a hard attempt ceiling, and a terminal failure state.
    """

    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)

    # Stable idempotency key sent to the receiver so it can dedupe retries.
    delivery_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Frozen request definition. The payload is rendered once at enqueue time so
    # retries are deterministic. Secrets are NOT stored here: the auth header is
    # re-resolved from ``credential_uuid`` at send time (honours rotation/revocation).
    webhook_name = Column(String, nullable=True)
    endpoint_url = Column(String, nullable=False)
    http_method = Column(String, nullable=False, default="POST")
    payload = Column(JSON, nullable=False, default=dict)
    custom_headers = Column(JSON, nullable=True)
    credential_uuid = Column(String(36), nullable=True)

    # Workflow node that produced this delivery. Combined with workflow_run_id it
    # is the per-run/per-node idempotency key, so a retried run_integrations does
    # not create (and send) a duplicate delivery for the same node. Non-nullable:
    # a NULL would be distinct under the unique constraint and defeat the dedupe.
    webhook_node_id = Column(String, nullable=False)

    status = Column(
        Enum(
            "pending",
            "succeeded",
            "dead_letter",
            name="webhook_delivery_status",
        ),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts = Column(Integer, nullable=False, default=5, server_default=text("5"))
    # When the next attempt becomes due. NULL once terminal (succeeded/dead_letter).
    scheduled_for = Column(DateTime(timezone=True), nullable=True)

    last_status_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        # Sweeper lookup: due pending deliveries.
        Index(
            "idx_webhook_deliveries_pending_scheduled",
            "scheduled_for",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_webhook_deliveries_run", "workflow_run_id"),
        # Per-run/per-node idempotency: one delivery per webhook node per run.
        UniqueConstraint(
            "workflow_run_id",
            "webhook_node_id",
            name="uq_webhook_deliveries_run_node",
        ),
    )


class ToolModel(Base):
    """Model for storing reusable tools that can be invoked during workflows.

    Tools provide a standardized way to integrate external functionality - from
    HTTP API calls to native integrations.
    """

    __tablename__ = "tools"

    id = Column(Integer, primary_key=True, index=True)

    # Public identifier (used in APIs and workflow references)
    tool_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Tool metadata
    name = Column(String(255), nullable=False)
    description = Column(String, nullable=True)

    # Tool category - uses enum from api/enums.py
    category = Column(
        Enum(
            *[c.value for c in ToolCategory],
            name="tool_category",
        ),
        nullable=False,
        default=ToolCategory.HTTP_API.value,
    )

    # Icon configuration (for UI display)
    icon = Column(String(50), nullable=True)  # Icon identifier
    icon_color = Column(String(7), nullable=True)  # Hex color code

    # Status management
    status = Column(
        Enum(
            *[s.value for s in ToolStatus],
            name="tool_status",
        ),
        nullable=False,
        default=ToolStatus.ACTIVE.value,
        server_default=text("'active'::tool_status"),
    )

    # The tool definition (JSONB) - contains schema_version for compatibility
    # Structure depends on category:
    # - http_api: {"schema_version": 1, "type": "http_api", "config": {...}}
    definition = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_tools_organization_id", "organization_id"),
        Index("ix_tools_uuid", "tool_uuid"),
        Index("ix_tools_status", "status"),
        Index("ix_tools_category", "category"),
    )


class KnowledgeBaseDocumentModel(Base):
    """Model for storing document-level metadata in the knowledge base.

    Each document represents a source file (PDF, DOCX, etc.) that has been
    processed and chunked for retrieval.
    """

    __tablename__ = "knowledge_base_documents"

    id = Column(Integer, primary_key=True, index=True)

    # Public identifier for API references
    document_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Document metadata
    filename = Column(String(500), nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    file_hash = Column(String(64), nullable=True)  # SHA-256 hash for deduplication
    mime_type = Column(String(100), nullable=True)

    # Retrieval mode: "chunked" (vector search), "full_document" (return full text),
    # or "table" (CSV rows stored in csv_table_rows, queried via SQL tool)
    retrieval_mode = Column(
        String(20), nullable=False, default="chunked", server_default="chunked"
    )
    full_text = Column(
        Text, nullable=True
    )  # Stored when retrieval_mode is "full_document"

    # Processing metadata
    source_url = Column(String, nullable=True)  # If document was fetched from URL
    total_chunks = Column(Integer, nullable=False, default=0)
    processing_status = Column(
        Enum(
            "pending",
            "processing",
            "completed",
            "failed",
            name="document_processing_status",
        ),
        nullable=False,
        default="pending",
        server_default=text("'pending'::document_processing_status"),
    )
    processing_error = Column(Text, nullable=True)

    # Docling conversion metadata
    docling_metadata = Column(
        JSON, nullable=False, default=dict
    )  # Store docling document metadata

    # Custom metadata (user-defined tags, categories, etc.)
    custom_metadata = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Soft delete
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")
    chunks = relationship(
        "KnowledgeBaseChunkModel",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    # Indexes and constraints
    __table_args__ = (
        Index("ix_kb_documents_organization_id", "organization_id"),
        Index("ix_kb_documents_uuid", "document_uuid"),
        Index("ix_kb_documents_status", "processing_status"),
        Index("ix_kb_documents_created_at", "created_at"),
    )


class WorkflowRecordingModel(Base):
    """Model for storing audio recordings scoped to an organization.

    Recordings are used in hybrid prompts where parts of the output are pre-recorded
    audio rather than dynamically generated TTS.
    """

    __tablename__ = "workflow_recordings"

    id = Column(Integer, primary_key=True, index=True)

    # Descriptive ID used in prompts (unique per organization)
    recording_id = Column(String(64), nullable=False, index=True)

    # Scoping
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # TTS configuration metadata (optional, legacy)
    tts_provider = Column(String, nullable=True)
    tts_model = Column(String, nullable=True)
    tts_voice_id = Column(String, nullable=True)

    # Content
    transcript = Column(Text, nullable=False)

    # Storage
    storage_key = Column(String, nullable=False)
    storage_backend = Column(
        Enum("s3", "minio", name="recording_storage_backend"),
        nullable=False,
        default="s3",
        server_default=text("'s3'::recording_storage_backend"),
    )

    # Extra metadata (file_size_bytes, duration_seconds, original_filename, mime_type, etc.)
    recording_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Soft delete
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "organization_id",
            name="uq_workflow_recordings_recording_id_org",
        ),
        Index("ix_workflow_recordings_workflow_id", "workflow_id"),
        Index("ix_workflow_recordings_org_id", "organization_id"),
        Index("ix_workflow_recordings_recording_id", "recording_id"),
    )


class KnowledgeBaseChunkModel(Base):
    """Model for storing document chunks with vector embeddings.

    Each chunk represents a portion of a document that has been:
    1. Extracted and chunked by docling's HybridChunker
    2. Optionally contextualized with surrounding information
    3. Embedded into a vector representation for semantic search
    """

    __tablename__ = "knowledge_base_chunks"

    id = Column(Integer, primary_key=True, index=True)

    # Link to parent document
    document_id = Column(
        Integer,
        ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Organization scoping (denormalized for efficient querying)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Chunk content
    chunk_text = Column(Text, nullable=False)  # The actual chunk text
    contextualized_text = Column(
        Text, nullable=True
    )  # Enriched text from chunker.contextualize()

    # Chunk positioning and metadata
    chunk_index = Column(Integer, nullable=False)  # Position in document (0-based)

    # Docling chunk metadata
    chunk_metadata = Column(
        JSON, nullable=False, default=dict
    )  # Store chunk.meta if available

    # Embedding configuration
    embedding_model = Column(
        String(200), nullable=False
    )  # e.g., "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension = Column(
        Integer, nullable=False
    )  # e.g., 384 for all-MiniLM-L6-v2

    # Vector embedding (pgvector column)
    # The dimension should match the embedding_dimension field
    # Default: 1536 dimensions for OpenAI text-embedding-3-small
    # SentenceTransformer (384-dim) also supported but stored as 384-dim vectors
    embedding = Column(Vector(1536), nullable=True)

    # Token count (useful for chunking strategy analysis)
    token_count = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    document = relationship("KnowledgeBaseDocumentModel", back_populates="chunks")
    organization = relationship("OrganizationModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_kb_chunks_document_id", "document_id"),
        Index("ix_kb_chunks_organization_id", "organization_id"),
        Index("ix_kb_chunks_chunk_index", "chunk_index"),
        Index(
            "ix_kb_chunks_embedding_model", "embedding_model"
        ),  # For filtering by model
        # Vector similarity search index (using IVFFlat or HNSW)
        # IVFFlat is good for datasets with 10k-1M vectors
        # HNSW is better for larger datasets but uses more memory
        Index(
            "ix_kb_chunks_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},  # Adjust based on dataset size
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class RAGRetrievalLogModel(Base):
    """Feedback loop: one row per retrieve_from_knowledge_base() call.

    Logged fire-and-forget after every retrieval so we can analyse:
    - Which queries hit which route (semantic / metadata_filter / aggregation)
    - Which chunks are returned and their rerank scores
    - Queries that consistently return low-scoring chunks (need re-ingestion)
    - Latency trends per route
    """

    __tablename__ = "rag_retrieval_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id = Column(Integer, nullable=True)  # null for manual /search calls

    # Query details
    query = Column(Text, nullable=False)
    expanded_queries = Column(JSON, nullable=False, default=list)  # list[str]

    # Routing outcome
    route = Column(String(50), nullable=False, default="semantic")

    # Retrieved chunks summary — stored as JSON list of dicts:
    # [{"chunk_id": int, "filename": str, "rerank_score": float, "rrf_score": float}]
    retrieved_chunks = Column(JSON, nullable=False, default=list)

    # Aggregate quality signal (avg rerank score across returned chunks)
    avg_rerank_score = Column(Float, nullable=True)

    # How many candidates were fetched before reranking
    candidates_fetched = Column(Integer, nullable=True)

    # Timestamp
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index("ix_rag_logs_organization_id", "organization_id"),
        Index("ix_rag_logs_workflow_run_id", "workflow_run_id"),
        Index("ix_rag_logs_route", "route"),
        Index("ix_rag_logs_created_at", "created_at"),
    )

# ---------------------------------------------------------------------------
# CSV Table models (text-to-SQL tool — counterpart to RAG knowledge base)
# ---------------------------------------------------------------------------


class CsvTableModel(Base):
    """Metadata for an uploaded CSV file.

    Each record represents one CSV that has been parsed and whose rows are
    stored in CsvTableRowModel. The table_uuid is the public identifier used
    in workflow node csv_table_uuids lists — exactly like document_uuid is
    used for knowledge base documents.
    """

    __tablename__ = "csv_tables"

    id = Column(Integer, primary_key=True, index=True)

    # Public identifier for API / workflow references
    table_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    name = Column(String(500), nullable=False)  # original filename or user label
    row_count = Column(Integer, nullable=False, default=0)

    # JSON array of {"name": "Brand", "type": "text"} — one entry per column
    column_schema = Column(JSON, nullable=False, default=list)

    # JSON array of per-column value profiles, computed at ingest and injected
    # into the node system prompt. Nullable: rows created before this column
    # existed keep working via the lazy compute-on-first-use fallback.
    value_profile = Column(JSON, nullable=True)

    # Processing lifecycle — same status values as KB documents
    processing_status = Column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    processing_error = Column(Text, nullable=True)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")
    rows = relationship(
        "CsvTableRowModel",
        back_populates="table",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_csv_tables_organization_id", "organization_id"),
        Index("ix_csv_tables_table_uuid", "table_uuid"),
        Index("ix_csv_tables_status", "processing_status"),
    )


class CsvTableRowModel(Base):
    """One data row from an uploaded CSV file.

    row_data is a JSONB object keyed by column name. All values are
    stored as their natural Python type (str/float/int/None) after CSV
    parsing. Queries use PostgreSQL JSONB operators on this column.
    JSONB is pre-parsed binary — supports GIN indexes for fast key/value
    lookups unlike plain JSON which requires full sequential scans.
    """

    __tablename__ = "csv_table_rows"

    id = Column(Integer, primary_key=True, index=True)
    table_id = Column(
        Integer, ForeignKey("csv_tables.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    row_index = Column(Integer, nullable=False)  # 0-based position in original CSV
    row_data = Column(JSONB, nullable=False)  # {"Brand": "JLG", "working_height_m": 20.3, ...}

    # Relationships
    table = relationship("CsvTableModel", back_populates="rows")

    __table_args__ = (
        Index("ix_csv_table_rows_table_id", "table_id"),
        Index("ix_csv_table_rows_org_id", "organization_id"),
    )


# ---------------------------------------------------------------------------
# Credit Wallet models (prepaid billing system)
# ---------------------------------------------------------------------------


class OrganizationCreditAccountModel(Base):
    """Per-organization prepaid credit wallet.
    
    One account per organization. All balances stored in USD. Display conversion
    happens at API edges only. Uses Decimal (never float) for money precision.
    """

    __tablename__ = "organization_credit_accounts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Balance tracking (always in USD, signed decimal for precision)
    balance_usd = Column(
        Numeric(18, 6),
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Current balance in USD. Precision: 6 decimals ($0.000001)",
    )

    # Display currency configuration
    display_currency = Column(
        String(3),
        nullable=False,
        default="USD",
        server_default=text("'USD'"),
        comment="ISO 4217 currency code for UI display",
    )
    display_fx_rate_to_usd = Column(
        Numeric(18, 6),
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Exchange rate: 1 display_currency = X USD",
    )

    # Threshold and blocking
    low_balance_threshold_usd = Column(
        Numeric(18, 6),
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Alert threshold in USD. Zero = no alerts",
    )
    is_blocked = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="If true, prevent new workflow runs",
    )

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    ledger_entries = relationship(
        "CreditLedgerEntryModel",
        back_populates="account",
        order_by="CreditLedgerEntryModel.created_at.desc()",
        cascade="all, delete-orphan",
    )

    # Indexes and constraints
    __table_args__ = (
        Index("ix_credit_accounts_organization_id", "organization_id", unique=True),
        Index("ix_credit_accounts_balance", "balance_usd"),
        Index("ix_credit_accounts_blocked", "is_blocked"),
    )


class CreditLedgerEntryModel(Base):
    """Immutable append-only ledger for all credit account mutations.
    
    Every balance change writes one entry with: amount (signed), new balance after,
    idempotency key, and full provenance (workflow_run_id, tokens, payment details).
    """

    __tablename__ = "credit_ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("organization_credit_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Entry classification
    entry_type = Column(
        Enum(
            *[t.value for t in CreditLedgerEntryType],
            name="credit_ledger_entry_type",
        ),
        nullable=False,
        index=True,
    )

    # Amount and balance (always USD, signed Numeric)
    amount_usd = Column(
        Numeric(18, 6),
        nullable=False,
        comment="Signed amount: positive for credits (topup), negative for debits (call)",
    )
    balance_after_usd = Column(
        Numeric(18, 6),
        nullable=False,
        comment="Snapshot of balance_usd after applying this entry",
    )

    # Idempotency — unique key prevents duplicate charges/topups
    idempotency_key = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Client-provided or server-generated unique key (workflow_run_id for debits)",
    )

    # Provenance fields for call debits
    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Workflow run that triggered this debit (null for topups/adjustments)",
    )
    tokens_charged = Column(
        Integer,
        nullable=True,
        comment="Dograh tokens consumed (call debits only)",
    )

    # Provenance fields for topups (payment receipts)
    paid_amount_minor = Column(
        Integer,
        nullable=True,
        comment="Payment amount in minor units (cents/paise) — topups only",
    )
    paid_currency = Column(
        String(3),
        nullable=True,
        comment="ISO 4217 currency code of payment — topups only",
    )
    fx_rate_to_usd = Column(
        Numeric(18, 6),
        nullable=True,
        comment="Exchange rate used for this topup: 1 paid_currency = X USD",
    )
    payment_provider_reference = Column(
        String(255),
        nullable=True,
        comment="Payment gateway transaction ID (Stripe charge_id, etc.)",
    )

    # Optional memo for adjustments/refunds
    memo = Column(
        Text,
        nullable=True,
        comment="Human-readable note for manual adjustments, refunds",
    )

    # Per-component billing breakdown (speech-billing spec §9.2): the quantities,
    # rates, and subtotals for llm/tts/stt/embedding plus markup and total, so a
    # disputed charge can be reconstructed. STT block carries "source":
    # "metrics" | "call_duration_fallback".
    usage_breakdown = Column(
        JSON,
        nullable=True,
        comment="Per-component billing breakdown (llm/tts/stt/embedding, rates, subtotals)",
    )

    # Timestamps (immutable — no updated_at)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    # Relationships
    organization = relationship("OrganizationModel")
    account = relationship("OrganizationCreditAccountModel", back_populates="ledger_entries")
    workflow_run = relationship("WorkflowRunModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_ledger_entries_account_id", "account_id"),
        Index("ix_ledger_entries_organization_id", "organization_id"),
        Index("ix_ledger_entries_idempotency_key", "idempotency_key", unique=True),
        Index("ix_ledger_entries_workflow_run_id", "workflow_run_id"),
        Index(
            "ix_ledger_entries_org_created",
            "organization_id",
            "created_at",
            postgresql_using="btree",
        ),
        Index("ix_ledger_entries_entry_type", "entry_type"),
    )


# ─────────────────────────── Dynamic Model Pricing ───────────────────────────


class ModelPricingModel(Base):
    """Versioned per-model pricing.

    One row per model *version*. Chat/embedding rows are synced from LiteLLM;
    TTS/STT rows are entered manually by an admin. A price change never
    overwrites a row — the current row is closed (effective_to set) and a new
    row inserted (effective_from set), so history is preserved and billing can
    reference the exact rate version active at execution time.

    Rate columns by kind:
      - chat:       input_per_1m, output_per_1m, cached_input_per_1m (stored, not billed)
      - embedding:  embedding_per_1m
      - tts:        rate (USD per 1,000,000 characters)
      - stt:        rate (USD per hour of audio)
    """

    __tablename__ = "model_pricing"

    id = Column(Integer, primary_key=True, index=True)

    provider = Column(
        String(64),
        nullable=False,
        index=True,
        comment="Provider key, e.g. openai/anthropic/google/groq/cartesia",
    )
    model_identifier = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Model id, e.g. gpt-4.1, text-embedding-3-small, sonic-3.5",
    )

    kind = Column(
        Enum(*[k.value for k in ModelPricingKind], name="model_pricing_kind"),
        nullable=False,
        index=True,
        comment="chat / embedding / tts / stt",
    )
    source = Column(
        Enum(*[s.value for s in ModelPricingSource], name="model_pricing_source"),
        nullable=False,
        comment="litellm (chat/embedding) or manual (tts/stt)",
    )
    unit = Column(
        Enum(*[u.value for u in ModelPricingUnit], name="model_pricing_unit"),
        nullable=False,
        comment="per_1m_token (chat/embedding) / per_1m_char (tts) / per_hour (stt)",
    )

    # Chat rates (per 1M tokens). Null for embedding/tts/stt.
    input_per_1m = Column(Numeric(18, 6), nullable=True, comment="Chat input rate per 1M tokens")
    output_per_1m = Column(Numeric(18, 6), nullable=True, comment="Chat output rate per 1M tokens")
    cached_input_per_1m = Column(
        Numeric(18, 6),
        nullable=True,
        comment="Chat cached-input rate per 1M tokens (stored, NOT billed)",
    )
    # Embedding rate (per 1M tokens). Null for chat/tts/stt.
    embedding_per_1m = Column(Numeric(18, 6), nullable=True, comment="Embedding rate per 1M tokens")
    # Speech-to-speech realtime rates (per 1M tokens). Null for every other kind.
    # Realtime models bill text and audio tokens at different rates, in and out.
    rt_text_input_per_1m = Column(
        Numeric(18, 6), nullable=True, comment="Realtime text input rate per 1M tokens"
    )
    rt_text_output_per_1m = Column(
        Numeric(18, 6), nullable=True, comment="Realtime text output rate per 1M tokens"
    )
    rt_audio_input_per_1m = Column(
        Numeric(18, 6), nullable=True, comment="Realtime audio input rate per 1M tokens"
    )
    rt_audio_output_per_1m = Column(
        Numeric(18, 6), nullable=True, comment="Realtime audio output rate per 1M tokens"
    )
    # Single rate for tts / stt. UNITS (per speech-billing spec):
    #   tts -> USD per 1,000,000 characters
    #   stt -> USD per hour of audio
    rate = Column(
        Numeric(18, 6),
        nullable=True,
        comment="TTS: USD per 1M characters. STT: USD per hour of audio.",
    )

    # Rate provenance (speech-billing spec §8.3). When a human last verified the
    # rate against the vendor's page, a free-text note, and who last changed it.
    rate_last_checked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When a human last verified this rate against the vendor page",
    )
    notes = Column(Text, nullable=True, comment="Operator note, e.g. pricing-tier reasoning")
    updated_by = Column(String(255), nullable=True, comment="Audit: who last changed this rate")

    status = Column(
        Enum(*[s.value for s in ModelPricingStatus], name="model_pricing_status"),
        nullable=False,
        server_default=text("'active'"),
        index=True,
        comment="active / deprecated / missing (catalog reality)",
    )
    is_enabled = Column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        index=True,
        comment="Admin toggle: whether this model is offered in AgentX",
    )

    # Versioning window
    effective_from = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="When this price version became active",
    )
    effective_to = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this version was superseded (null = current version)",
    )

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_model_pricing_provider_model", "provider", "model_identifier"),
        Index("ix_model_pricing_kind", "kind"),
        Index("ix_model_pricing_status", "status"),
        Index("ix_model_pricing_is_enabled", "is_enabled"),
        # Only one CURRENT (effective_to IS NULL) row per provider+model.
        Index(
            "uq_model_pricing_current",
            "provider",
            "model_identifier",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class ModelProviderSettingsModel(Base):
    """Per-provider admin enable/disable.

    Disabling a provider hides all of its models from AgentX regardless of each
    model's own is_enabled flag.
    """

    __tablename__ = "model_provider_settings"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="Provider key (openai/anthropic/google/groq/cartesia/...)",
    )
    is_enabled = Column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="If false, hide all of this provider's models from AgentX",
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ModelPricingPendingModel(Base):
    """Approval queue for LiteLLM sync differentials.

    The weekly (or manual) sync writes new/changed/deprecated chat & embedding
    models here. Nothing affects live pricing until an admin approves a row,
    which then writes to model_pricing. STT/TTS are manual and never land here.
    """

    __tablename__ = "model_pricing_pending"

    id = Column(Integer, primary_key=True, index=True)

    provider = Column(String(64), nullable=False, index=True)
    model_identifier = Column(String(255), nullable=False, index=True)
    kind = Column(
        Enum(*[k.value for k in ModelPricingKind], name="model_pricing_kind"),
        nullable=False,
    )
    change_type = Column(
        Enum(*[c.value for c in ModelPricingChangeType], name="model_pricing_change_type"),
        nullable=False,
        comment="new / price_change / deprecated",
    )

    # Proposed rates from the catalog (per 1M tokens).
    proposed_input_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_output_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_cached_input_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_embedding_per_1m = Column(Numeric(18, 6), nullable=True)
    # Proposed speech-to-speech realtime rates (per 1M tokens).
    proposed_rt_text_input_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_rt_text_output_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_rt_audio_input_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_rt_audio_output_per_1m = Column(Numeric(18, 6), nullable=True)
    proposed_status = Column(
        Enum(*[s.value for s in ModelPricingStatus], name="model_pricing_status"),
        nullable=True,
    )

    # Current live values (for admin comparison in the UI).
    current_input_per_1m = Column(Numeric(18, 6), nullable=True)
    current_output_per_1m = Column(Numeric(18, 6), nullable=True)
    current_cached_input_per_1m = Column(Numeric(18, 6), nullable=True)
    current_embedding_per_1m = Column(Numeric(18, 6), nullable=True)
    # Current speech-to-speech realtime rates (per 1M tokens).
    current_rt_text_input_per_1m = Column(Numeric(18, 6), nullable=True)
    current_rt_text_output_per_1m = Column(Numeric(18, 6), nullable=True)
    current_rt_audio_input_per_1m = Column(Numeric(18, 6), nullable=True)
    current_rt_audio_output_per_1m = Column(Numeric(18, 6), nullable=True)

    sync_run_id = Column(
        String(64),
        nullable=True,
        index=True,
        comment="Groups all pending rows produced by a single sync run",
    )
    detected_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_model_pricing_pending_provider_model", "provider", "model_identifier"),
        Index("ix_model_pricing_pending_sync_run", "sync_run_id"),
    )


class ModelUsageAuditModel(Base):
    """Immutable per-run billing audit.

    Append-only. One row per billed workflow run records the usage counts, the
    exact pricing version(s) applied, and the final computed charge — so any
    charge can be reconstructed from the rate active at execution time.
    """

    __tablename__ = "model_usage_audit"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Which models were used (nullable — a run may not use every service).
    chat_model = Column(String(255), nullable=True)
    embedding_model = Column(String(255), nullable=True)
    tts_model = Column(String(255), nullable=True)
    stt_model = Column(String(255), nullable=True)

    # Usage counts, each in its own unit.
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    embedding_tokens = Column(Integer, nullable=True)
    tts_characters = Column(Integer, nullable=True)
    stt_seconds = Column(Numeric(18, 3), nullable=True)

    # The chat pricing row used, for traceability (the primary driver of cost).
    chat_pricing_version_id = Column(
        Integer,
        ForeignKey("model_pricing.id", ondelete="SET NULL"),
        nullable=True,
    )

    computed_charge_usd = Column(
        Numeric(18, 6),
        nullable=False,
        comment="Final USD charge written to the wallet for this run",
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_model_usage_audit_workflow_run_id", "workflow_run_id"),
        Index("ix_model_usage_audit_created_at", "created_at"),
    )
