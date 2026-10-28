from typing import Any, Optional

from loguru import logger
from posthog import Posthog

from api.constants import ENABLE_TELEMETRY, POSTHOG_API_KEY, POSTHOG_HOST

_posthog_client: Posthog | None = None
POSTHOG_SERVER_GROUP_IDENTIFY_DISTINCT_ID = "server-group-identify"


def get_posthog() -> Posthog | None:
    """Return the lazily-initialised PostHog client, or None if disabled.

    Returns None — making every capture/identify call in this module a silent
    no-op — when either:
      * ``ENABLE_TELEMETRY`` is false, or
      * ``POSTHOG_API_KEY`` is unset/empty.

    ``ENABLE_TELEMETRY`` previously gated only Sentry (see api/app.py), so
    setting it false left PostHog reporting. It is honoured here too, giving
    one switch that turns off all outbound telemetry.
    """
    global _posthog_client
    if not ENABLE_TELEMETRY:
        return None
    if _posthog_client is None and POSTHOG_API_KEY:
        _posthog_client = Posthog(POSTHOG_API_KEY, host=POSTHOG_HOST)
    return _posthog_client


def shutdown_posthog() -> None:
    """Flush queued PostHog messages before a short-lived process exits."""
    client = get_posthog()
    if not client:
        return
    try:
        client.shutdown()
    except Exception:
        logger.exception("Failed to shut down PostHog client")


def flush_posthog() -> None:
    """Flush queued PostHog messages without shutting down the client."""
    client = get_posthog()
    if not client:
        return
    try:
        client.flush()
    except Exception:
        logger.exception("Failed to flush PostHog client")


def capture_event(
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
    groups: Optional[dict[str, str]] = None,
) -> None:
    """Fire a PostHog event. Silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return
    try:
        kwargs: dict[str, Any] = {
            "distinct_id": distinct_id,
            "event": event,
            "properties": properties or {},
        }
        if groups:
            kwargs["groups"] = groups
        client.capture(**kwargs)
    except Exception:
        logger.exception(f"Failed to send PostHog event '{event}'")


def group_identify(
    group_type: str,
    group_key: str,
    properties: dict[str, Any],
    *,
    distinct_id: Optional[str] = None,
) -> None:
    """Set PostHog group properties. Silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return
    try:
        client.group_identify(
            group_type,
            group_key,
            properties,
            distinct_id=distinct_id or POSTHOG_SERVER_GROUP_IDENTIFY_DISTINCT_ID,
        )
    except Exception:
        logger.exception("Failed to identify PostHog group")


def set_person_properties(distinct_id: str, properties: dict[str, Any]) -> None:
    """Set PostHog person properties. Silently no-ops if PostHog is not configured."""
    client = get_posthog()
    if not client:
        return
    try:
        client.set(distinct_id=distinct_id, properties=properties)
    except Exception:
        logger.exception("Failed to set PostHog person properties")


def capture_text_chat_lifecycle_event(
    *,
    event: str,
    distinct_id: str | None,
    workflow_run_id: int,
    workflow_id: int | None,
    call_direction: str | None = None,
    extra_properties: dict[str, Any] | None = None,
) -> None:
    """Fire a call-lifecycle PostHog event for a text chat run.

    Text chat has no voice pipeline, so the voice path's CALL_STARTED /
    CALL_COMPLETED never fire for it. This mirrors that event shape (same event
    names, same property keys) so text chats appear in the same PostHog activity
    views as calls, distinguished by ``call_type == "textchat"``. Never raises.
    """
    if not distinct_id:
        # PostHog requires a distinct_id; skip rather than send an anonymous one.
        return
    try:
        properties: dict[str, Any] = {
            "workflow_run_id": workflow_run_id,
            "workflow_id": workflow_id,
            "call_type": "textchat",
            "call_direction": call_direction or "inbound",
        }
        if extra_properties:
            properties.update(extra_properties)
        capture_event(distinct_id=distinct_id, event=event, properties=properties)
    except Exception:
        logger.exception(f"Failed to capture text chat lifecycle event '{event}'")
