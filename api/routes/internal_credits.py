"""Internal credits API.

Service-to-service endpoints for the prepaid credit wallet, guarded by
verify_internal_secret (X-Dograh-Internal-Secret header). These are called by
internal Dograh services (e.g. the WhatsApp Adapter), never by end users.

Handlers stay thin per api/AGENTS.md: validate, delegate to
api/services/credit_account_service.py, shape the response.
"""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.schemas.internal_credits import (
    AdjustRequest,
    AdjustResponse,
    GrantRequest,
    GrantResponse,
    LedgerResponse,
    OrganizationListResponse,
    OrganizationSummary,
    OrganizationWorkflowsResponse,
    ResolveAccountRequest,
    ResolveAccountResponse,
    SetCurrencyRequest,
    SetCurrencyResponse,
    WorkflowSummary,
)
from api.services import credit_account_service as credit_service
from api.services.auth.internal_auth import verify_internal_secret

router = APIRouter(
    prefix="/internal/credits",
    tags=["internal-credits"],
    dependencies=[Depends(verify_internal_secret)],
)


@router.post("/resolve-account", response_model=ResolveAccountResponse)
async def resolve_account(request: ResolveAccountRequest):
    """Resolve an organization by API key, email, or org ID; return its balance.

    Supply exactly one identifier. The service raises 400 for zero/multiple
    identifiers, 404 when nothing matches, 409 when a user has no selected org.
    """
    result = await credit_service.resolve_account(
        api_key=request.api_key,
        email=request.email,
        organization_id=request.organization_id,
    )

    summary = await credit_service.get_account_summary(result.organization_id)

    return ResolveAccountResponse(
        organization_id=summary["organization_id"],
        email=result.email,
        balance_usd=summary["balance_usd"],
        display_currency=summary["display_currency"],
        display_fx_rate_to_usd=summary["display_fx_rate_to_usd"],
        balance_display=summary["balance_display"],
        is_blocked=summary["is_blocked"],
        low_balance_threshold_usd=summary["low_balance_threshold_usd"],
    )


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations():
    """List every organization with owner email and workflow count.

    Platform-admin view for the operator console's org picker. Guarded by the
    internal secret (router-level dependency).
    """
    orgs = await credit_service.list_organizations()
    return OrganizationListResponse(
        organizations=[OrganizationSummary(**o) for o in orgs]
    )


@router.get(
    "/{organization_id}/workflows", response_model=OrganizationWorkflowsResponse
)
async def get_organization_workflows(organization_id: int):
    """List an org's workflows (name + status) for display only.

    Billing is per-org; this is informational so the operator can see what runs
    under the account. Not a per-workflow charge.
    """
    workflows = await credit_service.get_organization_workflows(organization_id)
    return OrganizationWorkflowsResponse(
        organization_id=organization_id,
        workflows=[WorkflowSummary(**w) for w in workflows],
    )


@router.post("/grant", response_model=GrantResponse)
async def grant(request: GrantRequest):
    """Top up a wallet. USD is computed server-side; never accepted from caller.

    A duplicate idempotency key returns 200 with duplicate=true and the original
    figures, leaving the balance untouched.
    """
    result = await credit_service.grant_credits(
        organization_id=request.organization_id,
        idempotency_key=request.idempotency_key,
        paid_amount_minor=request.paid_amount_minor,
        paid_currency=request.paid_currency,
        fx_rate_to_usd=request.fx_rate_to_usd,
        note=request.note,
        payment_provider_reference=request.payment_provider_reference,
    )
    return GrantResponse(**result)


@router.post("/adjust", response_model=AdjustResponse)
async def adjust(request: AdjustRequest):
    """Apply a signed manual adjustment. Note is mandatory.

    Positive credits, negative debits. The only sanctioned way to reduce a
    balance outside of usage.
    """
    result = await credit_service.adjust_credits(
        organization_id=request.organization_id,
        idempotency_key=request.idempotency_key,
        amount_usd=request.amount_usd,
        note=request.note,
    )
    return AdjustResponse(**result)


@router.post("/{organization_id}/currency", response_model=SetCurrencyResponse)
async def set_currency(organization_id: int, request: SetCurrencyRequest):
    """Set the account's display currency (operator decision, not a payment).

    Changes only how balances are shown; never touches the USD balance or the
    ledger. USD needs no rate; a non-USD currency needs a positive fx rate.
    """
    result = await credit_service.set_display_currency(
        organization_id=organization_id,
        display_currency=request.display_currency,
        fx_rate_to_usd=request.fx_rate_to_usd,
    )
    return SetCurrencyResponse(**result)


@router.get("/{organization_id}/ledger", response_model=LedgerResponse)
async def get_ledger(organization_id: int, page: int = 1, limit: int = 50):
    """Return a page of ledger entries (newest first) plus pagination metadata."""
    if limit > 500:
        raise HTTPException(status_code=400, detail="limit cannot exceed 500")

    result = await credit_service.get_ledger_page(
        organization_id=organization_id, page=page, limit=limit
    )
    return LedgerResponse(**result)
