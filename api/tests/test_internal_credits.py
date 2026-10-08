"""HTTP-layer tests for the internal credits API (Phase 3 Task 3.6).

Exercises the /internal/credits router end to end through the real FastAPI app
and the transactional test DB. Covers:
- Secret header matrix: unset -> 503, absent -> 401, wrong -> 401, correct -> passes
- compute_credited_usd rule table
- Duplicate grant returns original figures with duplicate=true (HTTP 200)
- Resolution by key / email / org id, plus 404 and 409
- Adjust requires a note; signed amounts move the balance
- Ledger pagination
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app

TEST_SECRET = "internal-secret-test-123"
HEADER = "X-Dograh-Internal-Secret"


def _patch_secret(value):
    """Patch DOGRAH_INTERNAL_SECRET everywhere it is referenced.

    internal_auth imports the constant by value at import time, so patching the
    name on that module is what the dependency actually reads.
    """
    return patch(
        "api.services.auth.internal_auth.DOGRAH_INTERNAL_SECRET", value
    )


async def _client():
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


# ──────────────────────────── auth matrix ────────────────────────────


@pytest.mark.asyncio
async def test_secret_unset_returns_503(db_session):
    with _patch_secret(None):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": 1},
            )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_secret_absent_returns_401(db_session):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": 1},
            )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_secret_wrong_returns_401(db_session):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": 1},
                headers={HEADER: "wrong-secret"},
            )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_secret_correct_passes(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": test_organization.id},
                headers={HEADER: TEST_SECRET},
            )
    # Passes auth (not 401/503); resolves a real org
    assert resp.status_code == 200
    assert resp.json()["organization_id"] == test_organization.id


# ──────────────────────────── resolve-account ────────────────────────────


@pytest.mark.asyncio
async def test_resolve_by_api_key(db_session, test_organization, test_api_key):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"api_key": test_api_key["raw_key"]},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == test_organization.id
    assert body["balance_usd"] == "0.000000"
    assert body["display_currency"] == "USD"
    assert body["is_blocked"] is False


@pytest.mark.asyncio
async def test_resolve_by_email(db_session, test_user_with_org):
    user, org = test_user_with_org
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"email": user.email},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == org.id
    assert body["email"] == user.email


@pytest.mark.asyncio
async def test_resolve_by_organization_id(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": test_organization.id},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    assert resp.json()["organization_id"] == test_organization.id


@pytest.mark.asyncio
async def test_resolve_bad_api_key_404(db_session):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"api_key": "dgr_does_not_exist"},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_email_no_org_409(db_session, test_user_no_org):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"email": test_user_no_org.email},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_resolve_no_identifier_400(db_session):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={},
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 400


# ──────────────────────────────── grant ────────────────────────────────


@pytest.mark.asyncio
async def test_grant_usd(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "grant-usd-1",
                    "paid_amount_minor": 5000,
                    "paid_currency": "USD",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["credited_usd"] == "50.000000"
    assert body["balance_usd"] == "50.000000"
    assert body["duplicate"] is False


@pytest.mark.asyncio
async def test_grant_inr_sets_display_currency(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            grant_resp = await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "grant-inr-1",
                    "paid_amount_minor": 50000,
                    "paid_currency": "INR",
                    "fx_rate_to_usd": "0.01124",
                    "note": "UPI ref 4432",
                },
                headers={HEADER: TEST_SECRET},
            )
            assert grant_resp.status_code == 200
            assert grant_resp.json()["credited_usd"] == "5.620000"

            # The account's display currency must now be INR
            resolve_resp = await client.post(
                "/api/v1/internal/credits/resolve-account",
                json={"organization_id": test_organization.id},
                headers={HEADER: TEST_SECRET},
            )
    body = resolve_resp.json()
    assert body["display_currency"] == "INR"
    assert body["balance_display"] != body["balance_usd"]


@pytest.mark.asyncio
async def test_grant_duplicate_returns_200_duplicate_true(
    db_session, test_organization
):
    payload = {
        "organization_id": test_organization.id,
        "idempotency_key": "grant-dup-1",
        "paid_amount_minor": 10000,
        "paid_currency": "USD",
    }
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            first = await client.post(
                "/api/v1/internal/credits/grant",
                json=payload,
                headers={HEADER: TEST_SECRET},
            )
            second = await client.post(
                "/api/v1/internal/credits/grant",
                json=payload,
                headers={HEADER: TEST_SECRET},
            )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    # Balance moved only once
    assert first.json()["balance_usd"] == "100.000000"
    assert second.json()["balance_usd"] == "100.000000"
    assert second.json()["entry_id"] == first.json()["entry_id"]


@pytest.mark.asyncio
async def test_grant_zero_amount_rejected(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "grant-zero",
                    "paid_amount_minor": 0,
                    "paid_currency": "USD",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_grant_usd_with_rate_2_rejected(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "grant-usd-rate2",
                    "paid_amount_minor": 5000,
                    "paid_currency": "USD",
                    "fx_rate_to_usd": "2.0",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_grant_non_usd_without_rate_rejected(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "grant-inr-norate",
                    "paid_amount_minor": 50000,
                    "paid_currency": "INR",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 400


# ──────────────────────────────── adjust ────────────────────────────────


@pytest.mark.asyncio
async def test_adjust_positive(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/adjust",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "adj-pos-1",
                    "amount_usd": "10.000000",
                    "note": "goodwill credit",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount_usd"] == "10.000000"
    assert body["balance_usd"] == "10.000000"


@pytest.mark.asyncio
async def test_adjust_negative_reduces_balance(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            # First credit 20
            await client.post(
                "/api/v1/internal/credits/grant",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "adj-setup",
                    "paid_amount_minor": 2000,
                    "paid_currency": "USD",
                },
                headers={HEADER: TEST_SECRET},
            )
            # Then adjust -5
            resp = await client.post(
                "/api/v1/internal/credits/adjust",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "adj-neg-1",
                    "amount_usd": "-5.000000",
                    "note": "correction",
                },
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    assert resp.json()["balance_usd"] == "15.000000"


@pytest.mark.asyncio
async def test_adjust_missing_note_rejected(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            resp = await client.post(
                "/api/v1/internal/credits/adjust",
                json={
                    "organization_id": test_organization.id,
                    "idempotency_key": "adj-nonote",
                    "amount_usd": "5.000000",
                    "note": "",
                },
                headers={HEADER: TEST_SECRET},
            )
    # Empty note rejected by Pydantic (422) or service (400)
    assert resp.status_code in (400, 422)


# ──────────────────────────────── ledger ────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_pagination(db_session, test_organization):
    with _patch_secret(TEST_SECRET):
        async with await _client() as client:
            # Create 3 ledger entries
            for i in range(3):
                await client.post(
                    "/api/v1/internal/credits/grant",
                    json={
                        "organization_id": test_organization.id,
                        "idempotency_key": f"ledger-{i}",
                        "paid_amount_minor": 1000,
                        "paid_currency": "USD",
                    },
                    headers={HEADER: TEST_SECRET},
                )
            resp = await client.get(
                f"/api/v1/internal/credits/{test_organization.id}/ledger?page=1&limit=2",
                headers={HEADER: TEST_SECRET},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 3
    assert body["page"] == 1
    assert body["limit"] == 2
    assert body["total_pages"] == 2
    assert len(body["entries"]) == 2
    # Newest first
    assert body["entries"][0]["idempotency_key"] == "ledger-2"
