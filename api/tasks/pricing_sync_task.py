"""Weekly pricing catalog sync (arq cron task).

Runs the LiteLLM fetch+diff on a schedule, populating the pending-approval
queue. It never changes live pricing — an admin still approves each diff in the
dashboard. Mirrors the sync the "Refresh Pricing" button triggers.
"""

from loguru import logger

from api.services.pricing import pricing_service


async def sync_model_pricing(ctx) -> dict:
    """ARQ cron entrypoint: fetch catalog → diff → pending queue."""
    try:
        result = await pricing_service.run_sync()
        logger.info("Weekly pricing sync complete: {}", result)
        return result
    except Exception as e:
        # Never let a sync failure crash the worker; it retries next week and
        # the manual "Refresh Pricing" button is always available.
        logger.error("Weekly pricing sync failed: {}", e)
        return {"error": str(e)}
