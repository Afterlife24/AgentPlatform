from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import workflow_run_billing as workflow_run_billing_mod
from api.services.workflow_run_billing import (
    _PLATFORM_MARKUP,
    _calculate_credits,
    _extract_token_counts,
    _is_usage_not_ready_error,
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


# ---------------------------------------------------------------------------
# OSS credit pricing (pure functions)
#
# credits = (
#     (prompt_tokens     * 0.15 / 1M) +
#     (completion_tokens * 0.60 / 1M) +
#     (embedding_tokens  * 0.02 / 1M)
# ) * 1.2 * 100
#
# 1 credit = 1 cent, so 1M input tokens = $0.15 * 1.2 = $0.18 = 18 credits.
# ---------------------------------------------------------------------------


def test_calculate_credits_is_zero_without_tokens():
    assert _calculate_credits(0, 0, 0) == 0.0


def test_calculate_credits_prices_input_tokens():
    # 1M prompt tokens: $0.15 * 1.2 markup = $0.18 = 18 credits
    assert _calculate_credits(1_000_000, 0, 0) == pytest.approx(18.0)


def test_calculate_credits_prices_output_tokens():
    # 1M completion tokens: $0.60 * 1.2 markup = $0.72 = 72 credits
    assert _calculate_credits(0, 1_000_000, 0) == pytest.approx(72.0)


def test_calculate_credits_prices_embedding_tokens():
    # 1M embedding tokens: $0.02 * 1.2 markup = $0.024 = 2.4 credits
    assert _calculate_credits(0, 0, 1_000_000) == pytest.approx(2.4)


def test_calculate_credits_sums_all_three_token_types():
    # Realistic single call: 100779 prompt / 1472 completion / 54 embedding
    expected = (
        (100_779 * 0.15 / 1_000_000)
        + (1_472 * 0.60 / 1_000_000)
        + (54 * 0.02 / 1_000_000)
    ) * 1.2 * 100
    assert _calculate_credits(100_779, 1_472, 54) == pytest.approx(expected)
    assert _calculate_credits(100_779, 1_472, 54) == pytest.approx(1.9201356)


def test_calculate_credits_applies_platform_markup():
    """The 20% markup must be applied — guards against it being dropped."""
    unmarked = ((1_000_000 * 0.15 / 1_000_000)) * 100
    assert _calculate_credits(1_000_000, 0, 0) == pytest.approx(
        unmarked * _PLATFORM_MARKUP
    )


def test_calculate_credits_scales_linearly():
    single = _calculate_credits(10_000, 500, 10)
    double = _calculate_credits(20_000, 1_000, 20)
    assert double == pytest.approx(single * 2)


# ---------------------------------------------------------------------------
# Token extraction from workflow_run.usage_info
# ---------------------------------------------------------------------------


def test_extract_token_counts_sums_across_llm_entries():
    usage_info = {
        "llm": {
            "ServiceA#0__gemini-3.1-flash": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
            },
            "ServiceB#1__gpt-4.1-mini": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
            },
        },
        "embedding_tokens": 7,
    }
    assert _extract_token_counts(usage_info) == (150, 15, 7)


def test_extract_token_counts_defaults_to_zero_when_empty():
    assert _extract_token_counts({}) == (0, 0, 0)


def test_extract_token_counts_tolerates_missing_and_null_fields():
    usage_info = {
        "llm": {
            "ServiceA#0__model": {"prompt_tokens": None},
            "ServiceB#1__model": {"completion_tokens": 4},
        },
        "embedding_tokens": None,
    }
    assert _extract_token_counts(usage_info) == (0, 4, 0)


def test_extract_token_counts_skips_non_dict_llm_entries():
    usage_info = {
        "llm": {
            "ServiceA#0__model": {"prompt_tokens": 20, "completion_tokens": 2},
            "malformed": "not-a-dict",
        }
    }
    assert _extract_token_counts(usage_info) == (20, 2, 0)
