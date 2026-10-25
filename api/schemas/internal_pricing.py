"""Request/response models for /internal/pricing routes.

Money/rate fields are Decimal to preserve precision. These back the admin
"Model Pricing" dashboard (sync, approve, enable/disable, manual STT/TTS CRUD).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


# ─────────────────────────── sync ───────────────────────────


class SyncResponse(BaseModel):
    sync_run_id: str
    new: int
    price_change: int
    missing: int = 0
    auto_approved: int = 0
    total: int


# ─────────────────────────── models list ───────────────────────────


class PricingModelItem(BaseModel):
    id: int
    provider: str
    model_identifier: str
    kind: str
    source: str
    unit: str
    input_per_1m: Optional[Decimal] = None
    output_per_1m: Optional[Decimal] = None
    cached_input_per_1m: Optional[Decimal] = None
    embedding_per_1m: Optional[Decimal] = None
    rt_text_input_per_1m: Optional[Decimal] = None
    rt_text_output_per_1m: Optional[Decimal] = None
    rt_audio_input_per_1m: Optional[Decimal] = None
    rt_audio_output_per_1m: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    status: str
    is_enabled: bool
    effective_from: Optional[datetime] = None


class ProviderSettingItem(BaseModel):
    provider: str
    is_enabled: bool


class ModelsListResponse(BaseModel):
    models: list[PricingModelItem]
    providers: list[ProviderSettingItem]


# ─────────────────────────── pending list ───────────────────────────


class PendingItem(BaseModel):
    id: int
    provider: str
    model_identifier: str
    kind: str
    change_type: str
    proposed_input_per_1m: Optional[Decimal] = None
    proposed_output_per_1m: Optional[Decimal] = None
    proposed_cached_input_per_1m: Optional[Decimal] = None
    proposed_embedding_per_1m: Optional[Decimal] = None
    proposed_rt_text_input_per_1m: Optional[Decimal] = None
    proposed_rt_text_output_per_1m: Optional[Decimal] = None
    proposed_rt_audio_input_per_1m: Optional[Decimal] = None
    proposed_rt_audio_output_per_1m: Optional[Decimal] = None
    proposed_status: Optional[str] = None
    current_input_per_1m: Optional[Decimal] = None
    current_output_per_1m: Optional[Decimal] = None
    current_cached_input_per_1m: Optional[Decimal] = None
    current_embedding_per_1m: Optional[Decimal] = None
    current_rt_text_input_per_1m: Optional[Decimal] = None
    current_rt_text_output_per_1m: Optional[Decimal] = None
    current_rt_audio_input_per_1m: Optional[Decimal] = None
    current_rt_audio_output_per_1m: Optional[Decimal] = None
    detected_at: Optional[datetime] = None


class PendingListResponse(BaseModel):
    pending: list[PendingItem]


# ─────────────────────────── approve / reject ───────────────────────────


class ApproveResponse(BaseModel):
    approved_id: int
    provider: str
    model_identifier: str
    change_type: str


class RejectResponse(BaseModel):
    rejected_id: int


# ─────────────────────────── enable/disable ───────────────────────────


class SetEnabledRequest(BaseModel):
    is_enabled: bool


class SetProviderEnabledResponse(BaseModel):
    provider: str
    is_enabled: bool


# ─────────────────────────── manual STT/TTS ───────────────────────────


class ManualModelRequest(BaseModel):
    """Add a manual STT or TTS model with its per-unit rate.

    kind must be 'tts' (rate is per character) or 'stt' (rate is per second).
    """

    provider: str
    model_identifier: str
    kind: str  # "tts" | "stt"
    rate: Decimal
    # None → preserve an existing model's enabled state (and default a brand-new
    # model to enabled). Only set explicitly to force enable/disable on add.
    is_enabled: Optional[bool] = None


class DeleteModelResponse(BaseModel):
    deleted_id: int
