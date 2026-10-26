"""Embed token endpoints for workflows."""

from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.constants import BACKEND_API_ENDPOINT, ENVIRONMENT, UI_APP_URL
from api.db import db_client
from api.db.models import EmbedTokenModel, UserModel
from api.enums import PostHogEvent
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event

router = APIRouter(prefix="/workflow")


def generate_embed_script(token: EmbedTokenModel) -> str:
    """Generate the voice embed script for a given token."""
    base_url = str(UI_APP_URL).rstrip("/")

    return f"""<!-- Dograh Voice Widget -->
<script>
  (function(d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '{base_url}/embed/dograh-widget.js?token={token.token}&environment={ENVIRONMENT}&apiEndpoint={BACKEND_API_ENDPOINT}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }}(document, 'script', 'dograh-widget'));
</script>"""


def generate_chat_embed_script(token: EmbedTokenModel) -> str:
    """Generate the chat embed script for a given token."""
    base_url = str(UI_APP_URL).rstrip("/")

    return f"""<!-- AgentX Chat Widget -->
<script>
  (function(d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '{base_url}/embed/agentx-chat-widget.js?token={token.token}&environment={ENVIRONMENT}&apiEndpoint={BACKEND_API_ENDPOINT}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }}(document, 'script', 'agentx-chat-widget'));
</script>"""


class EmbedTokenRequest(BaseModel):
    allowed_domains: Optional[list[str]] = None
    settings: Optional[dict] = None
    usage_limit: Optional[int] = None
    expires_in_days: Optional[int] = 30
    is_active: Optional[bool] = None


class ToggleEmbedTokenRequest(BaseModel):
    is_active: bool


class EmbedTokenResponse(BaseModel):
    id: int
    token: str
    allowed_domains: Optional[list[str]]
    settings: Optional[dict]
    is_active: bool
    usage_count: int
    usage_limit: Optional[int]
    expires_at: Optional[datetime]
    created_at: datetime
    embed_script: str
    chat_embed_script: Optional[str] = None


@router.post("/{workflow_id}/embed-token")
async def create_or_update_embed_token(
    workflow_id: int,
    request: Request,
    embed_request: EmbedTokenRequest,
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """
    Create or update an embed token for a workflow.
    Each workflow can have only one active embed token.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Check if an embed token already exists for this workflow
    existing_tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    expires_at = None
    if embed_request.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=embed_request.expires_in_days)

    is_active = embed_request.is_active if embed_request.is_active is not None else True

    if existing_tokens:
        # Update the existing token (reactivate/deactivate as specified)
        token = await db_client.update_embed_token(
            existing_tokens[0].id,
            user.selected_organization_id,
            allowed_domains=embed_request.allowed_domains,
            settings=embed_request.settings,
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
            is_active=is_active,
        )
    else:
        # Create new token
        token = await db_client.create_embed_token(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            allowed_domains=embed_request.allowed_domains,
            settings=embed_request.settings,
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
        )
        if not is_active:
            token = await db_client.update_embed_token(
                token.id,
                user.selected_organization_id,
                is_active=False,
            )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.AGENT_EMBEDDED,
        properties={
            "workflow_id": workflow_id,
            "is_new_token": len(existing_tokens) == 0,
            "has_domain_restriction": bool(embed_request.allowed_domains),
            "organization_id": user.selected_organization_id,
        },
    )

    # Generate embed scripts
    embed_script = generate_embed_script(token)
    chat_embed_script = generate_chat_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
        chat_embed_script=chat_embed_script,
    )


@router.get("/{workflow_id}/embed-token")
async def get_embed_token(
    workflow_id: int,
    request: Request,
    user: UserModel = Depends(get_user),
) -> Optional[EmbedTokenResponse]:
    """
    Get the embed token for a workflow if it exists.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get embed tokens for this workflow (active or inactive)
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    if not tokens:
        return None

    token = tokens[0]  # Return the workflow's embed token

    # Generate embed scripts
    embed_script = generate_embed_script(token)
    chat_embed_script = generate_chat_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
        chat_embed_script=chat_embed_script,
    )


@router.patch("/{workflow_id}/embed-token/toggle")
async def toggle_embed_token(
    workflow_id: int,
    toggle_request: ToggleEmbedTokenRequest,
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """
    Instantly toggle embed token between active and inactive states.
    When inactive, external website widgets will not render.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    if not tokens:
        # Create token if one doesn't exist yet
        token = await db_client.create_embed_token(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            created_by=user.id,
        )
        if not toggle_request.is_active:
            token = await db_client.update_embed_token(
                token.id,
                user.selected_organization_id,
                is_active=False,
            )
    else:
        token = await db_client.update_embed_token(
            tokens[0].id,
            user.selected_organization_id,
            is_active=toggle_request.is_active,
        )

    embed_script = generate_embed_script(token)
    chat_embed_script = generate_chat_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
        chat_embed_script=chat_embed_script,
    )


@router.delete("/{workflow_id}/embed-token")
async def deactivate_embed_token(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """
    Deactivate the embed token for a workflow.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get embed tokens for this workflow (active or inactive)
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    if not tokens:
        raise HTTPException(
            status_code=404, detail="No embed token found for this workflow"
        )

    # Deactivate the token
    success = await db_client.deactivate_embed_token(
        tokens[0].id, user.selected_organization_id
    )

    if success:
        return {"message": "Embed token deactivated successfully", "is_active": False}
    else:
        raise HTTPException(status_code=500, detail="Failed to deactivate embed token")
