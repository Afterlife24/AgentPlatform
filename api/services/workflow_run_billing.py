"""Workflow-run billing hooks.

Dograh does not rate or deduct credits locally. MPS owns credit accounting.
For hosted deployments, Dograh reports completed platform usage to MPS.
When a server-minted MPS correlation id exists, MPS uses model-service usage
as the canonical duration. Otherwise Dograh reports the completed run duration.

For OSS deployments, usage is debited from the prepaid credit wallet in USD
using the following formula:
    cost_usd = (
        (prompt_tokens   * 0.15 / 1_000_000) +
        (completion_tokens * 0.60 / 1_000_000) +
        (embedding_tokens  * 0.02 / 1_000_000)
    ) * 1.2

    Prices (per 1M tokens):
        Input  (prompt)     : $0.15
        Output (completion) : $0.60
        Embedding           : $0.02
    1.2x = 20% platform markup

Token counts are read from workflow_run.usage_info. The cost is written as a
negative ledger entry via apply_ledger_entry, keyed run:{workflow_run_id} for
idempotency against task retries.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.enums import CreditLedgerEntryType, PostHogEvent
from api.services.managed_model_services import get_mps_correlation_id
from api.services.mps_service_key_client import mps_service_key_client
from api.services.posthog_client import capture_event

# Pricing per 1M tokens (USD). Decimal throughout — never float — so the cost
# that lands in the wallet ledger is exact.
_INPUT_PRICE_PER_1M = Decimal("0.15")
_OUTPUT_PRICE_PER_1M = Decimal("0.60")
_EMBED_PRICE_PER_1M = Decimal("0.02")
_PLATFORM_MARKUP = Decimal("1.2")
_MILLION = Decimal("1000000")


def _workflow_run_organization_id(workflow_run) -> int | None:
    workflow = getattr(workflow_run, "workflow", None)
    return getattr(workflow, "organization_id", None)


def _duration_seconds_from_usage_info(workflow_run) -> float | None:
    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    duration = usage_info.get("call_duration_seconds")
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        return None

    return duration_seconds if duration_seconds > 0 else None


def _extract_token_counts(usage_info: dict[str, Any]) -> tuple[int, int, int]:
    """Extract prompt, completion, and embedding token counts from usage_info.

    Returns:
        (prompt_tokens, completion_tokens, embedding_tokens)

    usage_info stores per-model LLM token counts under the "llm" key, e.g.:
        {
            "llm": {
                "DograhGeminiLiveLLMService#0__gemini-3.1-flash-live-preview": {
                    "prompt_tokens": 100779,
                    "completion_tokens": 1472,
                    "total_tokens": 102251,
                    ...
                }
            },
            "embedding_tokens": 54
        }
    Sums across all LLM service entries for prompt and completion tokens.
    """
    prompt_tokens = 0
    completion_tokens = 0

    llm_entries = usage_info.get("llm") or {}
    for entry in llm_entries.values():
        if not isinstance(entry, dict):
            continue
        prompt_tokens += int(entry.get("prompt_tokens") or 0)
        completion_tokens += int(entry.get("completion_tokens") or 0)

    embedding_tokens = int(usage_info.get("embedding_tokens") or 0)

    return prompt_tokens, completion_tokens, embedding_tokens


def _calculate_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    embedding_tokens: int,
) -> Decimal:
    """Apply the platform billing formula and return the USD cost to debit.

    Formula (USD, not credits):
        cost_usd = (
            (prompt_tokens     * INPUT_PRICE  / 1_000_000) +
            (completion_tokens * OUTPUT_PRICE / 1_000_000) +
            (embedding_tokens  * EMBED_PRICE  / 1_000_000)
        ) * 1.2

    The 1.2x platform markup is unchanged. Only the unit changed: the old
    `* 100` credits conversion is gone — money is stored in USD now.
    Quantized to 6 dp with ROUND_HALF_UP to match the ledger's Numeric(18,6).
    """
    raw = (
        (Decimal(prompt_tokens) * _INPUT_PRICE_PER_1M / _MILLION)
        + (Decimal(completion_tokens) * _OUTPUT_PRICE_PER_1M / _MILLION)
        + (Decimal(embedding_tokens) * _EMBED_PRICE_PER_1M / _MILLION)
    )
    return (raw * _PLATFORM_MARKUP).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


async def _report_oss_platform_usage(workflow_run) -> None:
    """Debit the prepaid wallet ledger for a completed run in OSS mode.

    Uses the platform formula (USD):
        cost_usd = (
            (prompt_tokens   * 0.15 / 1M) +
            (completion_tokens * 0.60 / 1M) +
            (embedding_tokens  * 0.02 / 1M)
        ) * 1.2
    """
    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping OSS usage record for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    prompt_tokens, completion_tokens, embedding_tokens = _extract_token_counts(usage_info)

    if prompt_tokens <= 0 and completion_tokens <= 0 and embedding_tokens <= 0:
        logger.debug(
            "Skipping OSS usage record for workflow run {}: no billable tokens found",
            workflow_run.id,
        )
        return

    cost_usd = _calculate_cost_usd(prompt_tokens, completion_tokens, embedding_tokens)
    tokens_charged = prompt_tokens + completion_tokens + embedding_tokens

    # Extract model name for logging/PostHog (first LLM entry key)
    llm_entries = usage_info.get("llm") or {}
    raw_model_key = next(iter(llm_entries), "unknown")
    # Key format: "ServiceName#0__model-name" — extract just the model part
    model_name = raw_model_key.split("__")[-1] if "__" in raw_model_key else raw_model_key

    try:
        # Debit the wallet ledger. amount_usd is NEGATIVE (money leaving the
        # wallet). The idempotency key run:{id} makes this safe against ARQ
        # retries of the completion task — a retry finds the existing entry and
        # does not debit again.
        await db_client.apply_ledger_entry(
            organization_id=organization_id,
            entry_type=CreditLedgerEntryType.DEBIT_CALL,
            amount_usd=-cost_usd,
            idempotency_key=f"run:{workflow_run.id}",
            workflow_run_id=workflow_run.id,
            tokens_charged=tokens_charged,
        )
        logger.info(
            "Recorded OSS usage for workflow run {}: "
            "prompt={} output={} embed={} → ${} USD debited",
            workflow_run.id,
            prompt_tokens,
            completion_tokens,
            embedding_tokens,
            cost_usd,
        )
    except Exception as e:
        logger.error(
            "Failed to record OSS usage for workflow run {}: {}",
            workflow_run.id,
            e,
        )
        return

    # Fire PostHog event after every completed call billing
    try:
        capture_event(
            distinct_id=str(organization_id),
            event=PostHogEvent.CALL_COMPLETED_BILLING,
            properties={
                "workflow_run_id": workflow_run.id,
                "organization_id": organization_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "embedding_tokens": embedding_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": float(cost_usd),
                "model": model_name,
            },
        )
    except Exception as e:
        logger.warning(
            "Failed to send PostHog billing event for workflow run {}: {}",
            workflow_run.id,
            e,
        )


async def _organization_uses_mps_billing_v2(organization_id: int) -> bool:
    account = await mps_service_key_client.get_billing_account_status(
        organization_id=organization_id
    )
    return bool(account and account.get("billing_mode") == "v2")


def _is_usage_not_ready_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 409:
        return False
    return "usage_not_ready" in (getattr(response, "text", "") or "")


async def report_workflow_run_platform_usage(workflow_run) -> None:
    """Report hosted platform usage for a completed workflow run to MPS.

    In OSS mode, usage is recorded locally instead (see _report_oss_platform_usage).
    """
    if DEPLOYMENT_MODE == "oss":
        await _report_oss_platform_usage(workflow_run)
        return

    if not getattr(workflow_run, "is_completed", False):
        logger.warning(
            "Workflow run is not completed in report_workflow_run_platform_usage"
        )
        return

    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    duration_seconds = (
        None if correlation_id else _duration_seconds_from_usage_info(workflow_run)
    )
    if not correlation_id and duration_seconds is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no billable duration",
            workflow_run.id,
        )
        return

    try:
        if not await _organization_uses_mps_billing_v2(organization_id):
            logger.debug(
                "Not reporting platform usage since org not using mps billing v2"
            )
            return

        result = await mps_service_key_client.report_platform_usage(
            organization_id=organization_id,
            correlation_id=correlation_id,
            duration_seconds=duration_seconds,
            workflow_run_id=workflow_run.id,
            metadata={
                "source": "workflow_run_completion",
                "workflow_id": getattr(workflow_run, "workflow_id", None),
                "duration_source": (
                    "mps_correlation" if correlation_id else "dograh_usage_info"
                ),
            },
        )
        logger.info(
            "Reported platform usage for workflow run {} to MPS: {}",
            workflow_run.id,
            result,
        )
    except Exception as e:
        if _is_usage_not_ready_error(e):
            # A run can start and receive an MPS correlation id, then fail or end
            # before billable STT usage is recorded. MPS returns usage_not_ready
            # for that no-platform-fee path, so keep it out of error alerts.
            logger.warning(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )
        else:
            logger.error(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )


async def report_completed_workflow_run_platform_usage(workflow_run_id: int) -> None:
    """Load a completed workflow run and report platform usage to MPS."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Skipping platform usage report: workflow run {} not found",
            workflow_run_id,
        )
        return

    await report_workflow_run_platform_usage(workflow_run)
