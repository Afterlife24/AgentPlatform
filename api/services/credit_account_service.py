"""Credit account resolution and FX computation service.

Implements Phase 3 account resolution and FX conversion logic.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException, status
from loguru import logger

from api.db import db_client
from api.enums import CreditLedgerEntryType
from api.utils.api_key import hash_api_key


class AccountResolutionResult:
    """Result of resolving an account identifier to an organization."""

    def __init__(
        self,
        organization_id: int,
        email: Optional[str] = None,
        balance_usd: Optional[Decimal] = None,
    ):
        self.organization_id = organization_id
        self.email = email
        self.balance_usd = balance_usd


async def resolve_account_by_api_key(raw_api_key: str) -> AccountResolutionResult:
    """Resolve organization by raw API key.

    Args:
        raw_api_key: The unhashed API key (e.g., "dgr_...")

    Returns:
        AccountResolutionResult with organization_id

    Raises:
        HTTPException: 404 if the API key is not found or inactive
    """
    key_hash = hash_api_key(raw_api_key)
    api_key = await db_client.get_api_key_by_hash(key_hash)

    if not api_key:
        logger.warning(f"API key not found or inactive: {raw_api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or inactive",
        )

    # Resolve the org's owner email so the operator console can confirm the
    # target before granting money (the key path otherwise carries no identity).
    owner = await db_client.get_primary_user_for_organization(
        api_key.organization_id
    )

    return AccountResolutionResult(
        organization_id=api_key.organization_id,
        email=owner.email if owner else None,
    )


async def resolve_account_by_email(email: str) -> AccountResolutionResult:
    """Resolve organization by user email (case-insensitive).

    Args:
        email: User's email address

    Returns:
        AccountResolutionResult with organization_id and email

    Raises:
        HTTPException: 404 if user not found, 409 if user has no selected organization
    """
    user = await db_client.get_user_by_email(email)

    if not user:
        logger.warning(f"User not found by email: {email}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.selected_organization_id is None:
        logger.warning(
            f"User {user.id} ({email}) has no selected_organization_id - refusing implicit creation"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User has no selected organization",
        )

    return AccountResolutionResult(
        organization_id=user.selected_organization_id,
        email=user.email,
    )


async def resolve_account_by_organization_id(
    organization_id: int,
) -> AccountResolutionResult:
    """Resolve organization by explicit organization ID.

    Validates that the organization exists.

    Args:
        organization_id: The organization ID

    Returns:
        AccountResolutionResult with organization_id

    Raises:
        HTTPException: 404 if organization not found
    """
    organization = await db_client.get_organization_by_id(organization_id)

    if not organization:
        logger.warning(f"Organization not found: {organization_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Resolve the org's owner email for operator confirmation (same reason as
    # the API-key path).
    owner = await db_client.get_primary_user_for_organization(organization_id)

    return AccountResolutionResult(
        organization_id=organization_id,
        email=owner.email if owner else None,
    )


async def resolve_account(
    api_key: Optional[str] = None,
    email: Optional[str] = None,
    organization_id: Optional[int] = None,
) -> AccountResolutionResult:
    """Resolve an account by API key, email, or organization ID.

    Exactly one parameter must be provided. Priority order: api_key > email > organization_id.

    Args:
        api_key: Raw API key (e.g., "dgr_...")
        email: User email address
        organization_id: Organization ID

    Returns:
        AccountResolutionResult with organization_id and optional metadata

    Raises:
        HTTPException: 400 if multiple or no identifiers provided, 404/409 per resolution method
    """
    # Count how many identifiers were provided
    provided = sum(x is not None for x in [api_key, email, organization_id])

    if provided == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide one of: api_key, email, or organization_id",
        )

    if provided > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of: api_key, email, or organization_id",
        )

    # Resolve by priority order
    if api_key is not None:
        return await resolve_account_by_api_key(api_key)
    elif email is not None:
        return await resolve_account_by_email(email)
    else:
        return await resolve_account_by_organization_id(organization_id)


def compute_credited_usd(
    paid_amount_minor: int,
    paid_currency: str,
    fx_rate_to_usd: Optional[Decimal] = None,
) -> Decimal:
    """Compute the USD amount to credit from a payment in any currency.

    This is the ONLY place where money enters the wallet. The caller must never be able
    to supply the USD figure directly, as that would allow bugs or tampering to credit
    arbitrary sums.

    Args:
        paid_amount_minor: Amount paid in minor units (e.g., 5000 = $50.00 or ₹50.00)
        paid_currency: ISO 4217 currency code (e.g., "USD", "INR")
        fx_rate_to_usd: Exchange rate to USD (e.g., 0.01124 for INR). Required for non-USD.
                        Must be absent or exactly 1.0 for USD.

    Returns:
        Decimal: Amount in USD, quantized to 6 decimal places

    Raises:
        ValueError: If amount <= 0, USD with rate != 1, or non-USD without valid rate

    Examples:
        >>> compute_credited_usd(5000, "USD", None)
        Decimal('50.000000')
        >>> compute_credited_usd(50000, "INR", Decimal("0.01124"))
        Decimal('5.620000')
    """
    if paid_amount_minor <= 0:
        raise ValueError(f"Amount must be positive, got {paid_amount_minor}")

    # Convert minor units to major units (e.g., 5000 cents -> 50.00 dollars)
    paid_amount_major = Decimal(paid_amount_minor) / Decimal(100)

    currency_upper = paid_currency.upper()

    if currency_upper == "USD":
        # USD requires no rate or exactly 1.0
        if fx_rate_to_usd is not None and fx_rate_to_usd != Decimal("1.0"):
            raise ValueError(
                f"USD requires fx_rate_to_usd to be absent or 1.0, got {fx_rate_to_usd}"
            )
        usd_amount = paid_amount_major
    else:
        # Non-USD requires a positive rate
        if fx_rate_to_usd is None:
            raise ValueError(
                f"Non-USD currency {paid_currency} requires fx_rate_to_usd"
            )
        if fx_rate_to_usd <= 0:
            raise ValueError(
                f"fx_rate_to_usd must be positive, got {fx_rate_to_usd}"
            )

        usd_amount = paid_amount_major * fx_rate_to_usd

    # Quantize to 6 decimal places with ROUND_HALF_UP
    return usd_amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def to_display(
    balance_usd: Decimal,
    display_currency: str,
    fx_rate_from_usd: Optional[Decimal] = None,
) -> Decimal:
    """Convert a USD balance to a display currency for presentation.

    This is the ONLY place FX conversion happens for presentation. Money is always
    stored in USD internally.

    Args:
        balance_usd: Balance in USD (from database)
        display_currency: ISO 4217 currency code to display in (e.g., "INR")
        fx_rate_from_usd: Exchange rate FROM USD (e.g., 89.0 for USD->INR).
                          Required for non-USD. Must be absent or 1.0 for USD.

    Returns:
        Decimal: Amount in display currency, quantized to 2 decimal places

    Raises:
        ValueError: If USD with rate != 1, or non-USD without valid rate

    Examples:
        >>> to_display(Decimal("5.620000"), "USD", None)
        Decimal('5.62')
        >>> to_display(Decimal("5.620000"), "INR", Decimal("89.0"))
        Decimal('500.18')
    """
    currency_upper = display_currency.upper()

    if currency_upper == "USD":
        # USD requires no rate or exactly 1.0
        if fx_rate_from_usd is not None and fx_rate_from_usd != Decimal("1.0"):
            raise ValueError(
                f"USD requires fx_rate_from_usd to be absent or 1.0, got {fx_rate_from_usd}"
            )
        display_amount = balance_usd
    else:
        # Non-USD requires a positive rate
        if fx_rate_from_usd is None:
            raise ValueError(
                f"Non-USD currency {display_currency} requires fx_rate_from_usd"
            )
        if fx_rate_from_usd <= 0:
            raise ValueError(
                f"fx_rate_from_usd must be positive, got {fx_rate_from_usd}"
            )

        display_amount = balance_usd * fx_rate_from_usd

    # Quantize to 2 decimal places (standard for currency display) with ROUND_HALF_UP
    return display_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ─────────────────────── Account summary & grant/adjust ───────────────────────


async def get_account_summary(organization_id: int) -> dict:
    """Fetch an account and return a display-ready summary.

    Ensures the account exists (creates a zero-balance USD account if missing),
    then returns balance in both USD and the account's display currency.

    Returns:
        dict with organization_id, balance_usd, display_currency,
        display_fx_rate_to_usd, balance_display, is_blocked
    """
    account = await db_client.get_or_create_account(organization_id)

    balance_display = to_display(
        account.balance_usd,
        account.display_currency,
        # The account stores "1 display_currency = X USD". to_display expects
        # "USD -> display" (how many display units per USD), which is the inverse.
        _display_rate_from_account(account.display_currency, account.display_fx_rate_to_usd),
    )

    return {
        "organization_id": organization_id,
        "balance_usd": account.balance_usd,
        "display_currency": account.display_currency,
        "display_fx_rate_to_usd": account.display_fx_rate_to_usd,
        "balance_display": balance_display,
        "is_blocked": account.is_blocked,
        "low_balance_threshold_usd": account.low_balance_threshold_usd,
    }


def _display_rate_from_account(
    display_currency: str, display_fx_rate_to_usd: Decimal
) -> Optional[Decimal]:
    """Convert the account's stored 'X USD per 1 display unit' into the
    'display units per 1 USD' rate that to_display expects.

    For USD, returns None (no conversion). For non-USD, returns the inverse of
    display_fx_rate_to_usd (guarding against a zero/None rate).
    """
    if display_currency.upper() == "USD":
        return None
    if not display_fx_rate_to_usd or display_fx_rate_to_usd <= 0:
        # Fall back to no conversion rather than dividing by zero; the balance
        # is still correct in USD, display just mirrors it.
        return Decimal("1")
    return (Decimal("1") / display_fx_rate_to_usd).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


async def grant_credits(
    organization_id: int,
    idempotency_key: str,
    paid_amount_minor: int,
    paid_currency: str,
    fx_rate_to_usd: Optional[Decimal] = None,
    note: Optional[str] = None,
    payment_provider_reference: Optional[str] = None,
) -> dict:
    """Grant (top up) credits to an organization's wallet.

    The USD figure is computed server-side via compute_credited_usd and is never
    accepted from the caller. Sets the account's display currency in the same
    transaction as the ledger entry.

    Returns:
        dict with organization_id, credited_usd, balance_usd, entry_id, duplicate

    Raises:
        HTTPException 404: Account/organization does not exist
        HTTPException 400: Invalid amount or FX rate
    """
    # Ensure the account exists before attempting the grant
    await db_client.get_or_create_account(organization_id)

    try:
        credited_usd = compute_credited_usd(
            paid_amount_minor, paid_currency, fx_rate_to_usd
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    currency = paid_currency.upper()
    rate = Decimal("1") if currency == "USD" else fx_rate_to_usd

    try:
        entry, duplicate = await db_client.grant_topup(
            organization_id=organization_id,
            amount_usd=credited_usd,
            idempotency_key=idempotency_key,
            display_currency=currency,
            display_fx_rate_to_usd=rate,
            paid_amount_minor=paid_amount_minor,
            paid_currency=currency,
            fx_rate_to_usd=rate,
            payment_provider_reference=payment_provider_reference,
            memo=note,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    return {
        "organization_id": organization_id,
        "credited_usd": entry.amount_usd,
        "balance_usd": entry.balance_after_usd,
        "entry_id": entry.id,
        "duplicate": duplicate,
    }


async def adjust_credits(
    organization_id: int,
    idempotency_key: str,
    amount_usd: Decimal,
    note: str,
) -> dict:
    """Apply a manual signed adjustment to a wallet. Note is mandatory.

    Positive amount credits, negative debits. The only sanctioned way to reduce a
    balance outside of usage (refunds, corrections, goodwill).

    Returns:
        dict with organization_id, amount_usd, balance_usd, entry_id, duplicate

    Raises:
        HTTPException 400: Zero amount or empty note
        HTTPException 404: Account does not exist
    """
    if amount_usd == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="amount_usd cannot be zero",
        )
    if not note or not note.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="note is mandatory for adjustments",
        )

    await db_client.get_or_create_account(organization_id)

    # Detect duplicate before mutating so we can report it
    existing = await db_client.get_entry_by_idempotency_key(idempotency_key)
    duplicate = existing is not None

    try:
        entry = await db_client.apply_ledger_entry(
            organization_id=organization_id,
            entry_type=CreditLedgerEntryType.ADJUSTMENT,
            amount_usd=amount_usd,
            idempotency_key=idempotency_key,
            memo=note,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

    return {
        "organization_id": organization_id,
        "amount_usd": entry.amount_usd,
        "balance_usd": entry.balance_after_usd,
        "entry_id": entry.id,
        "duplicate": duplicate,
    }


async def get_ledger_page(
    organization_id: int, page: int = 1, limit: int = 50
) -> dict:
    """Fetch a page of ledger entries plus pagination metadata.

    Returns:
        dict with organization_id, balance_usd, entries, total_count, page,
        limit, total_pages
    """
    if page < 1:
        page = 1
    if limit < 1:
        limit = 50

    offset = (page - 1) * limit

    account = await db_client.get_or_create_account(organization_id)
    total_count = await db_client.get_ledger_count(organization_id)
    entries = await db_client.get_ledger(organization_id, limit=limit, offset=offset)

    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0

    return {
        "organization_id": organization_id,
        "balance_usd": account.balance_usd,
        "entries": entries,
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


# ─────────────────── Customer-facing billing view (Phase 7) ───────────────────


def _entry_display_type(entry_type: str) -> str:
    """Map a stored ledger entry_type to the customer-facing bucket.

    The customer page groups everything into grant / debit / adjustment. Call
    and KB charges are both "debit"; top-ups and the opening seed read as
    "grant"; refunds and manual corrections are "adjustment".
    """
    if entry_type in ("topup", "initial_balance"):
        return "grant"
    if entry_type == "debit_call":
        return "debit"
    if entry_type == "refund":
        return "adjustment"
    return "adjustment"  # adjustment, and any future type, shown as adjustment


async def build_oss_billing_view(
    organization_id: int, page: int = 1, limit: int = 50
) -> dict:
    """Assemble the customer-facing billing payload from the prepaid wallet.

    This is the OSS replacement for the legacy MPS credits response. Every money
    figure is converted to the account's DISPLAY CURRENCY here, in the backend,
    using the exact Decimal balance — the frontend never does FX arithmetic.

    Two display rules (design.md §9):
      - a grant row shows the tendered amount verbatim (paid_amount_minor /
        paid_currency), with no conversion;
      - a debit/adjustment row shows the USD amount converted at the account's
        display rate.

    Returns a dict shaped for MPSBillingCreditsResponse. The legacy "credits"
    field names carry display-currency amounts in this v2/OSS path — the names
    are kept only so the existing frontend ledger table lights up unchanged;
    nothing shown to the customer is labelled "credits".
    """
    if page < 1:
        page = 1
    if not 1 <= limit <= 100:
        limit = 50
    offset = (page - 1) * limit

    account = await db_client.get_or_create_account(organization_id)

    # The single place FX happens: "display units per 1 USD" (None for USD).
    display_rate = _display_rate_from_account(
        account.display_currency, account.display_fx_rate_to_usd
    )

    def to_disp(amount_usd: Decimal) -> Decimal:
        return to_display(amount_usd, account.display_currency, display_rate)

    balance_display = to_disp(account.balance_usd)

    total_count = await db_client.get_ledger_count(organization_id)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0
    entries = await db_client.get_ledger(organization_id, limit=limit, offset=offset)

    # Resolve workflow_id for debit rows so the UI can build a run link, scoped
    # to this org so a stray run id can't leak another tenant's workflow.
    run_ids = {
        e.workflow_run_id for e in entries if e.workflow_run_id is not None
    }
    workflow_id_by_run: dict[int, int] = {}
    for run_id in run_ids:
        workflow_run = await db_client.get_workflow_run_by_id(run_id)
        if (
            workflow_run
            and workflow_run.workflow
            and workflow_run.workflow.organization_id == organization_id
        ):
            workflow_id_by_run[run_id] = workflow_run.workflow_id

    ledger_entries: list[dict] = []
    for entry in entries:
        display_type = _entry_display_type(entry.entry_type)
        is_kb = bool(entry.idempotency_key and entry.idempotency_key.startswith("kb:"))

        ledger_entries.append(
            {
                "id": entry.id,
                "entry_type": display_type,
                # Origin is a short free-text tag the UI shows as a badge.
                "origin": "knowledge_base"
                if is_kb
                else ("call" if entry.entry_type == "debit_call" else None),
                # Amounts are already in the display currency.
                "credits_delta": to_disp(entry.amount_usd),
                "balance_after": to_disp(entry.balance_after_usd),
                # Grants show the tendered amount verbatim; others omit it.
                "amount_minor": entry.paid_amount_minor
                if display_type == "grant"
                else None,
                "amount_currency": entry.paid_currency
                if display_type == "grant"
                else None,
                "workflow_run_id": entry.workflow_run_id,
                "workflow_id": workflow_id_by_run.get(entry.workflow_run_id)
                if entry.workflow_run_id is not None
                else None,
                # Single total we actually store (prompt+completion+embedding).
                "tokens_charged": entry.tokens_charged,
                "memo": entry.memo,
                "fx_rate_to_usd": entry.fx_rate_to_usd,
                "created_at": entry.created_at,
            }
        )

    # Total spent = sum of debit magnitudes, in display currency.
    total_debits_usd = sum(
        (-e.amount_usd for e in entries if e.amount_usd < 0), Decimal("0")
    )

    low_balance = (
        account.low_balance_threshold_usd > 0
        and account.balance_usd <= account.low_balance_threshold_usd
    )

    return {
        "organization_id": organization_id,
        "balance_usd": account.balance_usd,
        "balance_display": balance_display,
        "display_currency": account.display_currency,
        "total_debits_display": to_disp(total_debits_usd),
        "is_blocked": account.is_blocked,
        "low_balance": low_balance,
        "ledger_entries": ledger_entries,
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }
