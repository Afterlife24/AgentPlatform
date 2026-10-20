"""Phase 5 enforcement tests — prepaid wallet balance gate.

Covers authorize_workflow_run_start's OSS wallet enforcement:
- the balance truth table (allowed/refused at the <= threshold)
- the explicit block flag
- the fail-CLOSED behaviour when the balance cannot be read (the single most
  important test in the phase — a lookup failure must refuse, never allow)
- the flag-off path never reads the balance

These are unit tests: the DB client and model-config resolution are mocked, so
no database is required.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import quota_service


def _workflow():
    return SimpleNamespace(
        id=7,
        user_id=123,
        organization_id=42,
        workflow_configurations={},
    )


def _owner():
    return SimpleNamespace(id=123, provider_id="provider-123")


def _byok_config():
    """A config with no Dograh keys, so the only gate exercised is the wallet."""
    return SimpleNamespace(
        managed_service_version=2,
        llm=SimpleNamespace(provider="openai", api_key="sk-openai"),
        stt=None,
        tts=None,
        embeddings=None,
    )


def _account(balance: str, *, is_blocked: bool = False):
    return SimpleNamespace(
        organization_id=42,
        balance_usd=Decimal(balance),
        is_blocked=is_blocked,
    )


def _patch_common(monkeypatch, *, enforce: bool, get_account):
    """Patch OSS mode, the enforcement flag, workflow context, and get_account."""
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(quota_service, "ENFORCE_CREDIT_BALANCE", enforce)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_by_id",
        AsyncMock(return_value=_workflow()),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(return_value=_owner()),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    monkeypatch.setattr(quota_service.db_client, "get_account", get_account)


# ───────────────────────────── balance truth table ─────────────────────────────


@pytest.mark.asyncio
async def test_balance_above_threshold_allowed(monkeypatch):
    get_account = AsyncMock(return_value=_account("1.000000"))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_balance_one_cent_allowed(monkeypatch):
    get_account = AsyncMock(return_value=_account("0.010000"))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_balance_exactly_zero_refused(monkeypatch):
    # The comparison is <=, so exactly 0.00 (the default threshold) is refused.
    get_account = AsyncMock(return_value=_account("0.000000"))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "insufficient_balance"


@pytest.mark.asyncio
async def test_balance_negative_refused(monkeypatch):
    get_account = AsyncMock(return_value=_account("-0.140000"))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "insufficient_balance"


# ───────────────────────────────── block flag ─────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_account_refused_even_with_balance(monkeypatch):
    # Positive balance but blocked → refused with account_blocked.
    get_account = AsyncMock(return_value=_account("100.000000", is_blocked=True))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "account_blocked"


# ──────────────────────────────── fail CLOSED ────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_raises_refuses_not_allows(monkeypatch):
    """The single most important test: a lookup failure must FAIL CLOSED.

    If this returned has_quota=True, a transient DB error would become a free
    call (R6.8). The WalletUnavailableError handler must sit before the
    fail-open catch-all.
    """
    get_account = AsyncMock(side_effect=RuntimeError("db connection lost"))
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "balance_check_failed"


@pytest.mark.asyncio
async def test_missing_account_refuses(monkeypatch):
    # No account row at all → cannot verify → fail closed.
    get_account = AsyncMock(return_value=None)
    _patch_common(monkeypatch, enforce=True, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "balance_check_failed"


# ───────────────────────────────── flag off ─────────────────────────────────


@pytest.mark.asyncio
async def test_flag_off_never_reads_balance(monkeypatch):
    """With ENFORCE_CREDIT_BALANCE off, get_account must never be called."""
    get_account = AsyncMock(return_value=_account("0.000000"))
    _patch_common(monkeypatch, enforce=False, get_account=get_account)

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    # No Dograh keys in the BYOK config, so with enforcement off the run is
    # allowed and the wallet is never consulted.
    assert result.has_quota is True
    get_account.assert_not_awaited()
