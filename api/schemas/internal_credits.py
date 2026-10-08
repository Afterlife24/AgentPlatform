"""Pydantic schemas for the internal credits API.

Request/response models for /internal/credits routes. Money fields are serialized
as strings to preserve Decimal precision across JSON (never float).
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── resolve-account ───────────────────────────


class ResolveAccountRequest(BaseModel):
    """Supply exactly one of api_key, email, or organization_id."""

    api_key: Optional[str] = None
    email: Optional[str] = None
    organization_id: Optional[int] = None


class ResolveAccountResponse(BaseModel):
    organization_id: int
    email: Optional[str] = None
    balance_usd: Decimal
    display_currency: str
    display_fx_rate_to_usd: Decimal
    balance_display: Decimal
    is_blocked: bool
    # Operator-facing low-balance warning level in USD. Zero means no warning.
    low_balance_threshold_usd: Decimal


# ─────────────────────────────── grant ─────────────────────────────────


class GrantRequest(BaseModel):
    organization_id: int
    idempotency_key: str = Field(..., min_length=1)
    paid_amount_minor: int = Field(..., description="Amount in minor units (paise/cents)")
    paid_currency: str = Field(..., min_length=3, max_length=3)
    fx_rate_to_usd: Optional[Decimal] = None
    note: Optional[str] = None
    created_by: Optional[str] = None
    payment_provider_reference: Optional[str] = None


class GrantResponse(BaseModel):
    organization_id: int
    credited_usd: Decimal
    balance_usd: Decimal
    entry_id: int
    duplicate: bool


# ─────────────────────────────── adjust ────────────────────────────────


class AdjustRequest(BaseModel):
    organization_id: int
    idempotency_key: str = Field(..., min_length=1)
    amount_usd: Decimal = Field(..., description="Signed USD amount: positive credit, negative debit")
    note: str = Field(..., min_length=1, description="Mandatory human-readable reason")
    created_by: Optional[str] = None


class AdjustResponse(BaseModel):
    organization_id: int
    amount_usd: Decimal
    balance_usd: Decimal
    entry_id: int
    duplicate: bool


# ─────────────────────────────── ledger ────────────────────────────────


class LedgerEntryResponse(BaseModel):
    id: int
    entry_type: str
    amount_usd: Decimal
    balance_after_usd: Decimal
    idempotency_key: str
    workflow_run_id: Optional[int] = None
    tokens_charged: Optional[int] = None
    paid_amount_minor: Optional[int] = None
    paid_currency: Optional[str] = None
    fx_rate_to_usd: Optional[Decimal] = None
    payment_provider_reference: Optional[str] = None
    memo: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LedgerResponse(BaseModel):
    organization_id: int
    balance_usd: Decimal
    entries: list[LedgerEntryResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int
