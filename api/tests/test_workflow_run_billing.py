from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.enums import CreditLedgerEntryType
from api.services import workflow_run_billing as workflow_run_billing_mod
from api.services.workflow_run_billing import (
    _calculate_cost_usd,
    _is_usage_not_ready_error,
    _report_oss_platform_usage,
    report_completed_workflow_run_platform_usage,
    report_workflow_run_platform_usage,
)


def _make_workflow_run():
    return SimpleNamespace(
        id=123,
        workflow_id=456,
        is_completed=True,
        initial_context={"mps_correlation_id": "mps-corr-123"},
        usage_info={"call_duration_seconds": 87},
        workflow=SimpleNamespace(
            organization_id=42,
            user=SimpleNamespace(selected_organization_id=42),
        ),
    )


def test_is_usage_not_ready_error_detects_mps_409():
    exc = Exception("Failed to report platform usage")
    exc.response = SimpleNamespace(
        status_code=409,
        text='{"detail":"usage_not_ready"}',
    )

    assert _is_usage_not_ready_error(exc) is True


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_hosted_completion(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id="mps-corr-123",
        duration_seconds=None,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "mps_correlation",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_reports_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_awaited_once_with(
        organization_id=42,
        correlation_id=None,
        duration_seconds=87.0,
        workflow_run_id=workflow_run.id,
        metadata={
            "source": "workflow_run_completion",
            "workflow_id": workflow_run.workflow_id,
            "duration_source": "dograh_usage_info",
        },
    )


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_non_v2_account(monkeypatch):
    workflow_run = _make_workflow_run()
    get_status = AsyncMock(return_value={"billing_mode": "v1"})
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    get_status.assert_awaited_once_with(organization_id=42)
    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_missing_duration_without_correlation(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    workflow_run.initial_context = {}
    workflow_run.usage_info = {}
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    get_status.assert_not_awaited()
    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_oss(monkeypatch):
    workflow_run = _make_workflow_run()
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_workflow_run_platform_usage_skips_incomplete(monkeypatch):
    workflow_run = _make_workflow_run()
    workflow_run.is_completed = False
    report_usage = AsyncMock()

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_workflow_run_platform_usage(workflow_run)

    report_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_completed_workflow_run_platform_usage_loads_run(monkeypatch):
    workflow_run = _make_workflow_run()
    get_run = AsyncMock(return_value=workflow_run)
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    report_usage = AsyncMock(return_value={"metered": True})

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client,
        "get_workflow_run_by_id",
        get_run,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )
    monkeypatch.setattr(
        workflow_run_billing_mod.mps_service_key_client,
        "report_platform_usage",
        report_usage,
    )

    await report_completed_workflow_run_platform_usage(workflow_run.id)

    get_run.assert_awaited_once_with(workflow_run.id)
    report_usage.assert_awaited_once()


# ───────────────────────── Phase 4: USD cost formula ─────────────────────────


@pytest.mark.parametrize(
    "prompt,completion,embedding,expected",
    [
        # The five reference rows from design.md §6 — must match exactly.
        (1_000_000, 0, 0, Decimal("0.180000")),
        (0, 1_000_000, 0, Decimal("0.720000")),
        (0, 0, 1_000_000, Decimal("0.024000")),
        (100_779, 1_472, 54, Decimal("0.019201")),
        (0, 0, 0, Decimal("0.000000")),
    ],
)
def test_calculate_cost_usd_reference_rows(prompt, completion, embedding, expected):
    """The USD cost formula must reproduce the five reference rows exactly."""
    assert _calculate_cost_usd(prompt, completion, embedding) == expected


def test_calculate_cost_usd_returns_decimal():
    """Cost must be a Decimal (never float) for exact ledger storage."""
    result = _calculate_cost_usd(1_000_000, 0, 0)
    assert isinstance(result, Decimal)


def test_calculate_cost_usd_applies_markup():
    """The 1.2x platform markup must be applied.

    Guards against anyone silently deleting the markup. Raw input cost for
    1M prompt tokens is $0.15; with the 1.2x markup it is $0.18. If the markup
    were dropped the result would be 0.150000, so this assertion fails loudly.
    """
    with_markup = _calculate_cost_usd(1_000_000, 0, 0)
    assert with_markup == Decimal("0.180000")
    # Explicitly: result == raw * 1.2
    raw = Decimal("0.15")
    assert with_markup == (raw * Decimal("1.2")).quantize(Decimal("0.000001"))


# ──────────────────── Phase 4: OSS debit on run completion ────────────────────


def _make_oss_run(run_id=777, usage_info=None):
    return SimpleNamespace(
        id=run_id,
        workflow_id=456,
        is_completed=True,
        initial_context={},
        usage_info=usage_info
        if usage_info is not None
        else {
            "llm": {
                "Svc#0__gemini-3.1": {
                    "prompt_tokens": 100_779,
                    "completion_tokens": 1_472,
                }
            },
            "embedding_tokens": 54,
        },
        workflow=SimpleNamespace(organization_id=42),
    )


@pytest.mark.asyncio
async def test_oss_completion_writes_one_negative_debit(monkeypatch):
    """A completed OSS run debits the ledger once with the correct USD amount."""
    run = _make_oss_run()
    apply = AsyncMock()
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", apply)
    # Silence PostHog
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)

    apply.assert_awaited_once()
    kwargs = apply.await_args.kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs["entry_type"] == CreditLedgerEntryType.DEBIT_CALL
    # Negative amount (money leaving the wallet), matching reference row 4
    assert kwargs["amount_usd"] == Decimal("-0.019201")
    assert kwargs["idempotency_key"] == "run:777"
    assert kwargs["workflow_run_id"] == 777
    # Single tokens_charged = prompt + completion + embedding
    assert kwargs["tokens_charged"] == 100_779 + 1_472 + 54


@pytest.mark.asyncio
async def test_oss_completion_zero_tokens_writes_nothing(monkeypatch):
    """A run with no billable tokens must write no ledger entry at all."""
    run = _make_oss_run(usage_info={"llm": {}, "embedding_tokens": 0})
    apply = AsyncMock()
    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(workflow_run_billing_mod.db_client, "apply_ledger_entry", apply)

    await _report_oss_platform_usage(run)

    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_oss_completion_idempotent_on_retry(monkeypatch):
    """Re-running completion for the same run must not debit twice.

    The idempotency key run:{id} is what makes this safe. apply_ledger_entry
    itself dedups by key; here we assert the key is stable across retries so
    the DB-level dedup can do its job.
    """
    run = _make_oss_run(run_id=999)
    seen_keys = []

    async def fake_apply(**kwargs):
        seen_keys.append(kwargs["idempotency_key"])
        return SimpleNamespace(id=1)

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "apply_ledger_entry", fake_apply
    )
    monkeypatch.setattr(workflow_run_billing_mod, "capture_event", lambda **k: None)

    await _report_oss_platform_usage(run)
    await _report_oss_platform_usage(run)

    # Both attempts use the identical idempotency key, so the real
    # apply_ledger_entry would dedup to a single ledger row.
    assert seen_keys == ["run:999", "run:999"]


@pytest.mark.asyncio
async def test_oss_completion_billing_failure_does_not_raise(monkeypatch):
    """A ledger failure must be swallowed (logged, not re-raised) so it never
    aborts artifact upload or integrations."""
    run = _make_oss_run()

    async def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(workflow_run_billing_mod, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        workflow_run_billing_mod.db_client, "apply_ledger_entry", boom
    )

    # Must not raise
    await _report_oss_platform_usage(run)
