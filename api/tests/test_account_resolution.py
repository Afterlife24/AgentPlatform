"""Tests for credit account resolution service.

Phase 3 Task 3.3 & 3.4: Account resolution and FX computation.
"""

import pytest
from decimal import Decimal
from fastapi import HTTPException

from api.services.credit_account_service import (
    resolve_account,
    resolve_account_by_api_key,
    resolve_account_by_email,
    resolve_account_by_organization_id,
    compute_credited_usd,
    to_display,
)


@pytest.mark.asyncio
async def test_resolve_by_api_key_success(db_session, test_organization, test_api_key):
    """Test successful resolution by API key."""
    result = await resolve_account_by_api_key(test_api_key["raw_key"])

    assert result.organization_id == test_organization.id
    # This fixture's org has no users, so there is no owner email to resolve.
    assert result.email is None


@pytest.mark.asyncio
async def test_resolve_by_api_key_includes_owner_email(
    db_session, test_user_with_org, test_api_key
):
    """API-key resolution returns the org owner's email for operator confirmation.

    test_api_key and test_user_with_org are both built on test_organization, so
    the key and the owning user share the same org.
    """
    user, org = test_user_with_org

    result = await resolve_account_by_api_key(test_api_key["raw_key"])

    assert result.organization_id == org.id
    assert result.email == user.email


@pytest.mark.asyncio
async def test_resolve_by_organization_id_includes_owner_email(
    db_session, test_user_with_org
):
    """Org-id resolution also returns the owner's email."""
    user, org = test_user_with_org

    result = await resolve_account_by_organization_id(org.id)

    assert result.organization_id == org.id
    assert result.email == user.email


@pytest.mark.asyncio
async def test_resolve_by_api_key_not_found(db_session):
    """Test resolution fails with invalid API key."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account_by_api_key("dgr_invalid_key_12345")

    assert exc_info.value.status_code == 404
    assert "not found or inactive" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_by_email_success(db_session, test_user_with_org):
    """Test successful resolution by email (case-insensitive)."""
    user, org = test_user_with_org

    # Test with exact case
    result = await resolve_account_by_email(user.email)
    assert result.organization_id == org.id
    assert result.email == user.email

    # Test with different case
    result_upper = await resolve_account_by_email(user.email.upper())
    assert result_upper.organization_id == org.id


@pytest.mark.asyncio
async def test_resolve_by_email_no_selected_org(db_session, test_user_no_org):
    """Test resolution fails when user has no selected organization."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account_by_email(test_user_no_org.email)

    assert exc_info.value.status_code == 409
    assert "no selected organization" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_by_email_not_found(db_session):
    """Test resolution fails with non-existent email."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account_by_email("nonexistent@example.com")

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_by_organization_id_success(db_session, test_organization):
    """Test successful resolution by organization ID."""
    result = await resolve_account_by_organization_id(test_organization.id)

    assert result.organization_id == test_organization.id


@pytest.mark.asyncio
async def test_resolve_by_organization_id_not_found(db_session):
    """Test resolution fails with non-existent organization ID."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account_by_organization_id(99999)

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_account_unified(db_session, test_organization, test_api_key):
    """Test the unified resolve_account function."""
    # Resolve by API key
    result = await resolve_account(api_key=test_api_key["raw_key"])
    assert result.organization_id == test_organization.id

    # Resolve by organization ID
    result = await resolve_account(organization_id=test_organization.id)
    assert result.organization_id == test_organization.id


@pytest.mark.asyncio
async def test_resolve_account_no_identifier(db_session):
    """Test resolve_account fails when no identifier provided."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account()

    assert exc_info.value.status_code == 400
    assert "Must provide one of" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_account_multiple_identifiers(
    db_session, test_organization, test_api_key
):
    """Test resolve_account fails when multiple identifiers provided."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_account(
            api_key=test_api_key["raw_key"], organization_id=test_organization.id
        )

    assert exc_info.value.status_code == 400
    assert "exactly one" in exc_info.value.detail


# FX Computation Tests


def test_compute_credited_usd_from_usd():
    """Test computing USD from USD payment (no conversion)."""
    # $50.00 in cents
    result = compute_credited_usd(5000, "USD", None)
    assert result == Decimal("50.000000")

    # Test with explicit rate of 1.0
    result = compute_credited_usd(5000, "USD", Decimal("1.0"))
    assert result == Decimal("50.000000")


def test_compute_credited_usd_from_inr():
    """Test computing USD from INR payment with FX rate."""
    # ₹500.00 at rate 0.01124 = $5.62
    result = compute_credited_usd(50000, "INR", Decimal("0.01124"))
    assert result == Decimal("5.620000")


def test_compute_credited_usd_zero_amount():
    """Test rejection of zero amount."""
    with pytest.raises(ValueError, match="must be positive"):
        compute_credited_usd(0, "USD", None)


def test_compute_credited_usd_negative_amount():
    """Test rejection of negative amount."""
    with pytest.raises(ValueError, match="must be positive"):
        compute_credited_usd(-1000, "USD", None)


def test_compute_credited_usd_usd_with_wrong_rate():
    """Test rejection of USD with rate != 1."""
    with pytest.raises(ValueError, match="absent or 1.0"):
        compute_credited_usd(5000, "USD", Decimal("0.5"))


def test_compute_credited_usd_non_usd_without_rate():
    """Test rejection of non-USD without rate."""
    with pytest.raises(ValueError, match="requires fx_rate_to_usd"):
        compute_credited_usd(5000, "INR", None)


def test_compute_credited_usd_non_usd_with_zero_rate():
    """Test rejection of non-USD with zero or negative rate."""
    with pytest.raises(ValueError, match="must be positive"):
        compute_credited_usd(5000, "INR", Decimal("0"))

    with pytest.raises(ValueError, match="must be positive"):
        compute_credited_usd(5000, "INR", Decimal("-0.01"))


def test_compute_credited_usd_quantization():
    """Test proper 6-decimal quantization with rounding."""
    # Test ROUND_HALF_UP behavior
    # 123456 / 100 = 1234.56, then * 0.0112345 = 13.86966420
    # Quantized to 6dp with ROUND_HALF_UP = 13.869664
    result = compute_credited_usd(123456, "INR", Decimal("0.0112345"))
    assert result == Decimal("13.869664")


# Display Conversion Tests


def test_to_display_usd():
    """Test display conversion for USD (no conversion)."""
    result = to_display(Decimal("5.620000"), "USD", None)
    assert result == Decimal("5.62")

    # Test with explicit rate of 1.0
    result = to_display(Decimal("5.620000"), "USD", Decimal("1.0"))
    assert result == Decimal("5.62")


def test_to_display_inr():
    """Test display conversion from USD to INR."""
    # $5.62 at rate 89.0 = ₹500.18
    result = to_display(Decimal("5.620000"), "INR", Decimal("89.0"))
    assert result == Decimal("500.18")


def test_to_display_usd_with_wrong_rate():
    """Test rejection of USD with rate != 1."""
    with pytest.raises(ValueError, match="absent or 1.0"):
        to_display(Decimal("5.62"), "USD", Decimal("2.0"))


def test_to_display_non_usd_without_rate():
    """Test rejection of non-USD without rate."""
    with pytest.raises(ValueError, match="requires fx_rate_from_usd"):
        to_display(Decimal("5.62"), "INR", None)


def test_to_display_non_usd_with_invalid_rate():
    """Test rejection of non-USD with zero or negative rate."""
    with pytest.raises(ValueError, match="must be positive"):
        to_display(Decimal("5.62"), "INR", Decimal("0"))

    with pytest.raises(ValueError, match="must be positive"):
        to_display(Decimal("5.62"), "INR", Decimal("-1.0"))


def test_to_display_quantization():
    """Test proper 2-decimal quantization with rounding."""
    # Test ROUND_HALF_UP behavior
    # 5.62 * 89.12345 = 500.873399 -> quantized to 2dp with ROUND_HALF_UP = 500.87
    result = to_display(Decimal("5.620000"), "INR", Decimal("89.12345"))
    assert result == Decimal("500.87")


def test_round_trip_conversion():
    """Test that converting to USD and back to display gives consistent results."""
    # Pay ₹500.00 at rate 0.01124 -> $5.62 USD
    usd_amount = compute_credited_usd(50000, "INR", Decimal("0.01124"))
    assert usd_amount == Decimal("5.620000")

    # Display $5.62 as INR at inverse rate 89.0 -> ₹500.18
    # (Note: slight difference due to FX rate precision, this is expected)
    display_amount = to_display(usd_amount, "INR", Decimal("89.0"))
    assert display_amount == Decimal("500.18")
