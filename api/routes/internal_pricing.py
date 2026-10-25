"""Internal model-pricing API.

Service-to-service endpoints for the dynamic pricing system, guarded by
verify_internal_secret. Called by the WhatsApp Adapter admin dashboard, never by
end users. Handlers stay thin; logic lives in services/pricing/pricing_service.py.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from api.enums import ModelPricingKind
from api.schemas.internal_pricing import (
    ApproveResponse,
    DeleteModelResponse,
    ManualModelRequest,
    ModelsListResponse,
    PendingListResponse,
    RejectResponse,
    SetEnabledRequest,
    SetProviderEnabledResponse,
    SyncResponse,
)
from api.services.auth.internal_auth import verify_internal_secret
from api.services.pricing import pricing_service

router = APIRouter(
    prefix="/internal/pricing",
    tags=["internal-pricing"],
    dependencies=[Depends(verify_internal_secret)],
)


@router.post("/sync", response_model=SyncResponse)
async def sync_pricing():
    """Fetch the LiteLLM catalog and compute a diff into the pending queue.

    Does NOT change live pricing — only populates pending for admin review.
    """
    result = await pricing_service.run_sync()
    return SyncResponse(**result)


@router.get("/models", response_model=ModelsListResponse)
async def list_models():
    """All current pricing rows + provider settings for the admin table."""
    result = await pricing_service.list_models()
    return ModelsListResponse(**result)


@router.get("/pending", response_model=PendingListResponse)
async def list_pending():
    """The current sync diff awaiting admin approval."""
    result = await pricing_service.list_pending()
    return PendingListResponse(**result)


@router.post("/pending/{pending_id}/approve", response_model=ApproveResponse)
async def approve_pending(pending_id: int):
    """Apply one pending diff to live pricing (versioned write)."""
    result = await pricing_service.approve_pending(pending_id)
    return ApproveResponse(**result)


@router.post("/pending/{pending_id}/reject", response_model=RejectResponse)
async def reject_pending(pending_id: int):
    """Discard a pending diff without applying it."""
    result = await pricing_service.reject_pending(pending_id)
    return RejectResponse(**result)


@router.post("/models/{pricing_id}/enable")
async def enable_model(pricing_id: int):
    """Enable a model → it becomes selectable in AgentX."""
    return await pricing_service.set_model_enabled(pricing_id, True)


@router.post("/models/{pricing_id}/disable")
async def disable_model(pricing_id: int):
    """Disable a model → hidden from AgentX."""
    return await pricing_service.set_model_enabled(pricing_id, False)


@router.post("/providers/{provider}/enable", response_model=SetProviderEnabledResponse)
async def enable_provider(provider: str):
    """Enable a whole provider."""
    result = await pricing_service.set_provider_enabled(provider, True)
    return SetProviderEnabledResponse(**result)


@router.post("/providers/{provider}/disable", response_model=SetProviderEnabledResponse)
async def disable_provider(provider: str):
    """Disable a whole provider → hides all its models from AgentX."""
    result = await pricing_service.set_provider_enabled(provider, False)
    return SetProviderEnabledResponse(**result)


@router.post("/manual")
async def add_manual_model(request: ManualModelRequest):
    """Add a manual STT/TTS model with its per-unit rate."""
    try:
        kind = ModelPricingKind(request.kind)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="kind must be 'tts' or 'stt'"
        ) from e
    return await pricing_service.add_manual_model(
        provider=request.provider,
        model_identifier=request.model_identifier,
        kind=kind,
        rate=Decimal(request.rate),
        is_enabled=request.is_enabled,
    )


@router.delete("/models/{pricing_id}", response_model=DeleteModelResponse)
async def delete_model(pricing_id: int):
    """Delete a pricing row (used for removing manual STT/TTS models)."""
    result = await pricing_service.delete_model(pricing_id)
    return DeleteModelResponse(**result)
