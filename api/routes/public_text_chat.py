"""Public API endpoints for text-chat agent execution (e.g. WhatsApp, SMS).

These endpoints are accessible with API key authentication and allow
external messaging systems to send text messages to a Dograh workflow
and receive assistant replies — no dashboard login required.

Session management is keyed by a caller-provided `session_key` (typically
the sender's phone number like "whatsapp:+919876543210"). First message
from a new session_key creates a fresh workflow run; subsequent messages
continue the existing conversation.
"""

import random
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from pydantic import BaseModel, Field

from api.db import db_client
from api.enums import TriggerState, WorkflowRunMode, WorkflowStatus
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
    append_text_chat_user_message,
    default_text_chat_checkpoint,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
)

router = APIRouter(prefix="/public/agent/text-chat", tags=["public-text-chat"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PublicTextChatMessageRequest(BaseModel):
    """Request body for sending a text message to an agent."""

    session_key: str = Field(
        min_length=1,
        description=(
            "Unique key identifying the conversation session. Typically the "
            "sender's phone number (e.g. 'whatsapp:+919876543210'). "
            "First message with a new session_key starts a new conversation; "
            "subsequent messages continue it."
        ),
    )
    text: str = Field(min_length=1, description="The user's message text.")
    initial_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional context to inject into the workflow run when a new "
            "session is created (ignored for existing sessions)."
        ),
    )


class PublicTextChatMessageResponse(BaseModel):
    """Response containing the assistant's reply."""

    session_key: str
    assistant_text: str | None = None
    is_completed: bool = False
    workflow_run_id: int


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _validate_api_key(x_api_key: str):
    """Validate the org API key."""
    api_key = await db_client.validate_api_key(x_api_key)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


async def _resolve_trigger(trigger_path: str, organization_id: int):
    """Resolve trigger UUID → workflow, ensuring org ownership."""
    trigger = await db_client.get_agent_trigger_by_path(trigger_path)
    if not trigger:
        raise HTTPException(status_code=404, detail="Agent trigger not found")
    if organization_id != trigger.organization_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if trigger.state != TriggerState.ACTIVE.value:
        raise HTTPException(
            status_code=404, detail="Agent trigger is not active")

    workflow = await db_client.get_workflow(
        trigger.workflow_id, organization_id=organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != WorkflowStatus.ACTIVE.value:
        raise HTTPException(status_code=404, detail="Workflow is not active")

    return workflow, trigger


async def _resolve_workflow_by_uuid(workflow_uuid: str, organization_id: int):
    """Resolve workflow UUID directly, scoped to the API key's org."""
    workflow = await db_client.get_workflow_by_uuid(workflow_uuid, organization_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != WorkflowStatus.ACTIVE.value:
        raise HTTPException(status_code=404, detail="Workflow is not active")
    return workflow


def _get_execution_user_id(workflow) -> int:
    if workflow.user_id is None:
        raise HTTPException(
            status_code=409, detail="Workflow has no execution owner"
        )
    return workflow.user_id


async def _get_or_create_session(
    *,
    workflow,
    organization_id: int,
    session_key: str,
    initial_context: Dict[str, Any] | None,
    use_draft: bool,
    api_key_id: int | None,
):
    """Look up an existing text-chat session by session_key, or create one.

    Sessions are tracked via annotations on the workflow run:
      annotations.public_text_chat.session_key = <session_key>

    A session is considered expired if the workflow run's last update was
    more than SESSION_TIMEOUT_MINUTES ago.

    Returns (text_session, is_new).
    """
    from datetime import datetime, timezone, timedelta

    SESSION_TIMEOUT_MINUTES = 10  # Session expires after 10 min of inactivity

    # Try to find an existing active workflow run for this session_key
    existing_run = await db_client.get_workflow_run_by_annotation(
        workflow_id=workflow.id,
        organization_id=organization_id,
        annotation_key="public_text_chat",
        annotation_value={"session_key": session_key},
        mode=WorkflowRunMode.TEXTCHAT.value,
        completed=False,
    )

    if existing_run:
        # Check if session has expired due to inactivity
        last_activity = existing_run.created_at
        # Use the text session's updated_at if available
        text_session = await db_client.get_workflow_run_text_session(
            existing_run.id, organization_id=organization_id
        )
        if text_session:
            last_activity = text_session.updated_at or text_session.created_at

        now = datetime.now(timezone.utc)
        # Ensure last_activity is timezone-aware
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)

        if now - last_activity < timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            # Session is still active
            set_current_run_id(existing_run.id)
            return text_session, False
        else:
            # Session expired — mark old run as completed so we don't pick it up again
            logger.info(
                f"Session expired for {session_key} "
                f"(inactive {(now - last_activity).seconds // 60}m). Starting new session."
            )
            await db_client.update_workflow_run(existing_run.id, is_completed=True)

    # No active session — create one
    execution_user_id = _get_execution_user_id(workflow)
    run_name = f"WR-WHATSAPP-{random.randint(1000, 9999)}"

    context: Dict[str, Any] = {
        "channel": "text_chat_public",
        "session_key": session_key,
    }
    if api_key_id is not None:
        context["api_key_id"] = api_key_id
    if initial_context:
        context.update(initial_context)

    workflow_run = await db_client.create_workflow_run(
        name=run_name,
        workflow_id=workflow.id,
        mode=WorkflowRunMode.TEXTCHAT.value,
        user_id=execution_user_id,
        initial_context=context,
        use_draft=use_draft,
        organization_id=organization_id,
    )

    set_current_run_id(workflow_run.id)

    # Store session_key in annotations for later lookup
    annotations = {
        "public_text_chat": {
            "session_key": session_key,
            "source": "public_api",
            "modality": "text",
        }
    }
    await db_client.update_workflow_run(workflow_run.id, annotations=annotations)

    # Check quota
    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        workflow_run_id=workflow_run.id,
    )
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Create and initialize text session
    text_session = await db_client.ensure_workflow_run_text_session(
        workflow_run.id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )

    try:
        text_session = await initialize_text_chat_session(
            run_id=workflow_run.id,
            text_session=text_session,
        )
    except TextChatSessionRevisionConflictError:
        raise HTTPException(
            status_code=409, detail="Session initialization conflict")

    # Execute the initial turn (greeting/welcome from the workflow)
    text_session = await _execute_turn(workflow.id, workflow_run.id, text_session)

    return text_session, True


async def _execute_turn(workflow_id: int, run_id: int, text_session):
    """Execute a pending assistant turn and return updated session."""
    try:
        return await execute_pending_text_chat_turn(
            workflow_id=workflow_id,
            run_id=run_id,
            text_session=text_session,
        )
    except TextChatSessionRevisionConflictError:
        raise HTTPException(
            status_code=409, detail="Session revision conflict")
    except TextChatPendingTurnLostError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except TextChatSessionExecutionError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _extract_latest_assistant_text(text_session) -> str | None:
    """Pull the most recent assistant message text from session data."""
    session_data = text_session.session_data or {}
    turns = session_data.get("turns") or []
    for turn in reversed(turns):
        if turn.get("status") == "completed":
            assistant_msg = turn.get("assistant_message")
            if assistant_msg:
                return assistant_msg.get("text")
    return None


async def _handle_message(
    *,
    workflow,
    organization_id: int,
    request: PublicTextChatMessageRequest,
    use_draft: bool,
    api_key_id: int | None,
) -> PublicTextChatMessageResponse:
    """Core message handling: find/create session, append message, execute."""
    text_session, is_new = await _get_or_create_session(
        workflow=workflow,
        organization_id=organization_id,
        session_key=request.session_key,
        initial_context=request.initial_context,
        use_draft=use_draft,
        api_key_id=api_key_id,
    )

    workflow_run = text_session.workflow_run
    run_id = workflow_run.id
    workflow_id = workflow_run.workflow_id

    if is_new:
        # Session was just created and initialized — the first user message
        # still needs to be appended and processed.
        pass

    # Append user message
    try:
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=request.text,
            expected_revision=None,  # No revision tracking for public API
        )
    except TextChatSessionRevisionConflictError:
        raise HTTPException(
            status_code=409, detail="Session revision conflict")

    # Execute the assistant turn
    text_session = await _execute_turn(workflow_id, run_id, text_session)

    # Extract assistant reply
    assistant_text = _extract_latest_assistant_text(text_session)

    return PublicTextChatMessageResponse(
        session_key=request.session_key,
        assistant_text=assistant_text,
        is_completed=workflow_run.is_completed if workflow_run else False,
        workflow_run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.post("/{trigger_path}/message", response_model=PublicTextChatMessageResponse)
async def send_message_by_trigger(
    trigger_path: str,
    request: PublicTextChatMessageRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Send a text message to an agent identified by its trigger UUID.

    Uses the published workflow definition. The session is automatically
    created on first contact and continued on subsequent messages from
    the same session_key.
    """
    api_key = await _validate_api_key(x_api_key)
    workflow, _trigger = await _resolve_trigger(
        trigger_path, api_key.organization_id
    )

    return await _handle_message(
        workflow=workflow,
        organization_id=api_key.organization_id,
        request=request,
        use_draft=False,
        api_key_id=api_key.id,
    )


@router.post(
    "/test/{trigger_path}/message", response_model=PublicTextChatMessageResponse
)
async def send_message_by_trigger_test(
    trigger_path: str,
    request: PublicTextChatMessageRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Send a text message using the latest draft (for testing before publish)."""
    api_key = await _validate_api_key(x_api_key)
    workflow, _trigger = await _resolve_trigger(
        trigger_path, api_key.organization_id
    )

    return await _handle_message(
        workflow=workflow,
        organization_id=api_key.organization_id,
        request=request,
        use_draft=True,
        api_key_id=api_key.id,
    )


@router.post(
    "/workflow/{workflow_uuid}/message",
    response_model=PublicTextChatMessageResponse,
)
async def send_message_by_workflow_uuid(
    workflow_uuid: str,
    request: PublicTextChatMessageRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Send a text message to an agent identified by workflow UUID."""
    api_key = await _validate_api_key(x_api_key)
    workflow = await _resolve_workflow_by_uuid(
        workflow_uuid, api_key.organization_id
    )

    return await _handle_message(
        workflow=workflow,
        organization_id=api_key.organization_id,
        request=request,
        use_draft=False,
        api_key_id=api_key.id,
    )


@router.post(
    "/test/workflow/{workflow_uuid}/message",
    response_model=PublicTextChatMessageResponse,
)
async def send_message_by_workflow_uuid_test(
    workflow_uuid: str,
    request: PublicTextChatMessageRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Send a text message using the latest draft, identified by workflow UUID."""
    api_key = await _validate_api_key(x_api_key)
    workflow = await _resolve_workflow_by_uuid(
        workflow_uuid, api_key.organization_id
    )

    return await _handle_message(
        workflow=workflow,
        organization_id=api_key.organization_id,
        request=request,
        use_draft=True,
        api_key_id=api_key.id,
    )
