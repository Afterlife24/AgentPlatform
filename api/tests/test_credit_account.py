"""Tests for credit account database client operations.

This module tests the prepaid credit wallet functionality including:
- Account creation and retrieval
- Ledger entry creation with idempotency
- Balance calculations and atomic updates
- Account settings updates
- Concurrency and locking behavior
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from api.db.models import OrganizationModel
from api.enums import CreditLedgerEntryType


@pytest.fixture
async def test_organization(db_session):
    """Create a test organization for wallet tests."""
    org = OrganizationModel(
        provider_id="test_org_wallet_001",
    )
    async with db_session.async_session() as session:
        session.add(org)
        await session.commit()
        await session.refresh(org)
    return org


@pytest.mark.asyncio
async def test_get_account_returns_none_when_not_exists(db_session, test_organization):
    """Test that get_account returns None for non-existent account."""
    # Use a different org_id that doesn't have an account
    account = await db_session.get_account(organization_id=99999)
    assert account is None


@pytest.mark.asyncio
async def test_get_or_create_account_creates_new_account(
    db_session, test_organization
):
    """Test that get_or_create_account creates a new account with defaults."""
    account = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )

    assert account is not None
    assert account.organization_id == test_organization.id
    assert account.balance_usd == Decimal("0")
    assert account.display_currency == "USD"
    assert account.display_fx_rate_to_usd == Decimal("1")
    assert account.low_balance_threshold_usd == Decimal("0")
    assert account.is_blocked is False
    assert account.created_at is not None
    assert account.updated_at is not None


@pytest.mark.asyncio
async def test_get_or_create_account_returns_existing_account(
    db_session, test_organization
):
    """Test that get_or_create_account returns existing account on second call."""
    # Create account first time
    account1 = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )
    account1_id = account1.id

    # Try to create again - should return existing
    account2 = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )

    assert account2.id == account1_id
    assert account2.organization_id == test_organization.id


@pytest.mark.asyncio
async def test_apply_ledger_entry_topup(db_session, test_organization):
    """Test applying a topup ledger entry increases balance."""
    # Create account
    account = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )
    assert account.balance_usd == Decimal("0")

    # Apply topup
    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("100.50"),
        idempotency_key="topup_001",
        paid_amount_minor=10050,
        paid_currency="USD",
        fx_rate_to_usd=Decimal("1"),
        payment_provider_reference="stripe_ch_123",
    )

    assert entry.entry_type == CreditLedgerEntryType.TOPUP.value
    assert entry.amount_usd == Decimal("100.50")
    assert entry.balance_after_usd == Decimal("100.50")
    assert entry.idempotency_key == "topup_001"
    assert entry.paid_amount_minor == 10050
    assert entry.paid_currency == "USD"
    assert entry.payment_provider_reference == "stripe_ch_123"

    # Verify account balance updated
    account_refreshed = await db_session.get_account(
        organization_id=test_organization.id
    )
    assert account_refreshed.balance_usd == Decimal("100.50")


@pytest.mark.asyncio
async def test_apply_ledger_entry_debit(db_session, test_organization):
    """Test applying a debit ledger entry decreases balance."""
    # Create account with initial balance
    account = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )

    # Add some funds first
    await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("100"),
        idempotency_key="initial_topup",
    )

    # Apply debit (negative amount)
    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.DEBIT_CALL,
        amount_usd=Decimal("-25.75"),
        idempotency_key="call_001",
        workflow_run_id=None,  # Would be actual workflow_run_id in production
        tokens_charged=515,
    )

    assert entry.entry_type == CreditLedgerEntryType.DEBIT_CALL.value
    assert entry.amount_usd == Decimal("-25.75")
    assert entry.balance_after_usd == Decimal("74.25")
    assert entry.tokens_charged == 515

    # Verify balance
    account_refreshed = await db_session.get_account(
        organization_id=test_organization.id
    )
    assert account_refreshed.balance_usd == Decimal("74.25")


@pytest.mark.asyncio
async def test_apply_ledger_entry_idempotency(db_session, test_organization):
    """Test that duplicate idempotency_key returns existing entry without creating duplicate."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # First application
    entry1 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("50"),
        idempotency_key="idempotent_topup_001",
    )

    # Second application with same idempotency key
    entry2 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("50"),
        idempotency_key="idempotent_topup_001",
    )

    # Should return same entry
    assert entry1.id == entry2.id
    assert entry1.amount_usd == entry2.amount_usd

    # Balance should only be increased once
    account = await db_session.get_account(organization_id=test_organization.id)
    assert account.balance_usd == Decimal("50")


@pytest.mark.asyncio
async def test_apply_ledger_entry_zero_amount_raises_error(
    db_session, test_organization
):
    """Test that zero amount raises ValueError."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    with pytest.raises(ValueError, match="amount_usd cannot be zero"):
        await db_session.apply_ledger_entry(
            organization_id=test_organization.id,
            entry_type=CreditLedgerEntryType.ADJUSTMENT,
            amount_usd=Decimal("0"),
            idempotency_key="zero_amount",
        )


@pytest.mark.asyncio
async def test_apply_ledger_entry_no_account_raises_error(
    db_session, test_organization
):
    """Test that applying entry without account raises ValueError."""
    with pytest.raises(ValueError, match="No credit account found"):
        await db_session.apply_ledger_entry(
            organization_id=test_organization.id,
            entry_type=CreditLedgerEntryType.TOPUP,
            amount_usd=Decimal("100"),
            idempotency_key="no_account_test",
        )


@pytest.mark.asyncio
async def test_get_ledger_returns_entries_newest_first(db_session, test_organization):
    """Test that get_ledger returns entries in descending order by created_at."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Create multiple entries
    await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("100"),
        idempotency_key="entry_1",
    )

    await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.DEBIT_CALL,
        amount_usd=Decimal("-10"),
        idempotency_key="entry_2",
    )

    await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("50"),
        idempotency_key="entry_3",
    )

    # Get ledger
    entries = await db_session.get_ledger(organization_id=test_organization.id)

    assert len(entries) == 3
    # Newest first (entry_3, entry_2, entry_1)
    assert entries[0].idempotency_key == "entry_3"
    assert entries[1].idempotency_key == "entry_2"
    assert entries[2].idempotency_key == "entry_1"


@pytest.mark.asyncio
async def test_get_ledger_pagination(db_session, test_organization):
    """Test get_ledger pagination with limit and offset."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Create 5 entries
    for i in range(5):
        await db_session.apply_ledger_entry(
            organization_id=test_organization.id,
            entry_type=CreditLedgerEntryType.TOPUP,
            amount_usd=Decimal("10"),
            idempotency_key=f"entry_{i}",
        )

    # Get page 1 (first 2 entries)
    page1 = await db_session.get_ledger(
        organization_id=test_organization.id, limit=2, offset=0
    )
    assert len(page1) == 2

    # Get page 2 (next 2 entries)
    page2 = await db_session.get_ledger(
        organization_id=test_organization.id, limit=2, offset=2
    )
    assert len(page2) == 2

    # Ensure no overlap
    assert page1[0].id != page2[0].id


@pytest.mark.asyncio
async def test_get_entry_by_idempotency_key(db_session, test_organization):
    """Test retrieving a ledger entry by idempotency key."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Create entry
    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("75.25"),
        idempotency_key="unique_key_123",
        memo="Test topup",
    )

    # Retrieve by idempotency key
    found_entry = await db_session.get_entry_by_idempotency_key("unique_key_123")

    assert found_entry is not None
    assert found_entry.id == entry.id
    assert found_entry.amount_usd == Decimal("75.25")
    assert found_entry.memo == "Test topup"


@pytest.mark.asyncio
async def test_get_entry_by_idempotency_key_not_found(db_session):
    """Test that non-existent idempotency key returns None."""
    found_entry = await db_session.get_entry_by_idempotency_key("nonexistent_key")
    assert found_entry is None


@pytest.mark.asyncio
async def test_update_account_settings(db_session, test_organization):
    """Test updating account settings."""
    account = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )

    # Update settings
    updated_account = await db_session.update_account_settings(
        organization_id=test_organization.id,
        display_currency="EUR",
        display_fx_rate_to_usd=Decimal("0.92"),
        low_balance_threshold_usd=Decimal("10"),
        is_blocked=True,
    )

    assert updated_account.display_currency == "EUR"
    assert updated_account.display_fx_rate_to_usd == Decimal("0.92")
    assert updated_account.low_balance_threshold_usd == Decimal("10")
    assert updated_account.is_blocked is True


@pytest.mark.asyncio
async def test_update_account_settings_partial_update(db_session, test_organization):
    """Test that update_account_settings only updates provided fields."""
    account = await db_session.get_or_create_account(
        organization_id=test_organization.id
    )

    # Update only is_blocked
    updated_account = await db_session.update_account_settings(
        organization_id=test_organization.id,
        is_blocked=True,
    )

    # Other fields unchanged
    assert updated_account.display_currency == "USD"
    assert updated_account.display_fx_rate_to_usd == Decimal("1")
    assert updated_account.low_balance_threshold_usd == Decimal("0")
    # Only is_blocked changed
    assert updated_account.is_blocked is True


@pytest.mark.asyncio
async def test_update_account_settings_no_account_raises_error(
    db_session, test_organization
):
    """Test that updating non-existent account raises ValueError."""
    with pytest.raises(ValueError, match="No credit account found"):
        await db_session.update_account_settings(
            organization_id=test_organization.id,
            is_blocked=True,
        )


@pytest.mark.asyncio
async def test_multiple_entries_balance_calculation(db_session, test_organization):
    """Test that multiple ledger entries correctly calculate running balance."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Initial topup
    entry1 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("1000"),
        idempotency_key="initial",
    )
    assert entry1.balance_after_usd == Decimal("1000")

    # First debit
    entry2 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.DEBIT_CALL,
        amount_usd=Decimal("-123.45"),
        idempotency_key="call_1",
    )
    assert entry2.balance_after_usd == Decimal("876.55")

    # Second debit
    entry3 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.DEBIT_CALL,
        amount_usd=Decimal("-76.55"),
        idempotency_key="call_2",
    )
    assert entry3.balance_after_usd == Decimal("800")

    # Refund
    entry4 = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.REFUND,
        amount_usd=Decimal("50"),
        idempotency_key="refund_1",
        memo="Refund for overage",
    )
    assert entry4.balance_after_usd == Decimal("850")

    # Final balance check
    account = await db_session.get_account(organization_id=test_organization.id)
    assert account.balance_usd == Decimal("850")


@pytest.mark.asyncio
async def test_negative_balance_allowed(db_session, test_organization):
    """Test that balance can go negative (overdraft scenario)."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Apply debit that creates negative balance
    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.DEBIT_CALL,
        amount_usd=Decimal("-50"),
        idempotency_key="overdraft_test",
    )

    assert entry.balance_after_usd == Decimal("-50")

    # Verify account has negative balance
    account = await db_session.get_account(organization_id=test_organization.id)
    assert account.balance_usd == Decimal("-50")


@pytest.mark.asyncio
async def test_decimal_precision(db_session, test_organization):
    """Test that Decimal values maintain 6 decimal places precision."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    # Apply entry with 6 decimal places
    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("123.456789"),  # Will be stored as 123.456789
        idempotency_key="precision_test",
    )

    # Database should store with precision (up to 6 decimals)
    assert entry.amount_usd == Decimal("123.456789")
    assert entry.balance_after_usd == Decimal("123.456789")


@pytest.mark.asyncio
async def test_ledger_entry_with_all_provenance_fields(db_session, test_organization):
    """Test creating ledger entry with all optional provenance fields populated."""
    await db_session.get_or_create_account(organization_id=test_organization.id)

    entry = await db_session.apply_ledger_entry(
        organization_id=test_organization.id,
        entry_type=CreditLedgerEntryType.TOPUP,
        amount_usd=Decimal("250"),
        idempotency_key="full_topup_001",
        workflow_run_id=None,  # Not applicable for topups
        tokens_charged=None,  # Not applicable for topups
        paid_amount_minor=25000,  # $250 in cents
        paid_currency="USD",
        fx_rate_to_usd=Decimal("1"),
        payment_provider_reference="stripe_pi_abc123xyz",
        memo="Annual subscription payment",
    )

    assert entry.paid_amount_minor == 25000
    assert entry.paid_currency == "USD"
    assert entry.fx_rate_to_usd == Decimal("1")
    assert entry.payment_provider_reference == "stripe_pi_abc123xyz"
    assert entry.memo == "Annual subscription payment"
