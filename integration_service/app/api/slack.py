"""
Slack integration API routes (integration_service).
Handles OAuth connect/disconnect, notification settings, and channel listing.
User-based: each user connects their own Slack workspace.
"""

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.models.slack import SlackConnection, SlackNotificationSettings
from app.schemas.slack import (
    SlackAuthUrlResponse,
    SlackCallbackRequest,
    SlackChannelsResponse,
    SlackConnectionResponse,
    SlackNotificationSettingsResponse,
    SlackNotificationSettingsUpdate,
)
from app.services.slack_service import slack_service
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/slack", tags=["slack"])

OAUTH_REDIRECT_PATH = "/integrations"


def _slack_redirect_uri() -> str:
    """Get the Slack OAuth redirect URI.

    Uses SLACK_OAUTH_REDIRECT_URI if set (ngrok HTTPS URL for dev),
    otherwise falls back to FRONTEND_URL + path.
    """
    if settings.SLACK_OAUTH_REDIRECT_URI:
        return settings.SLACK_OAUTH_REDIRECT_URI
    return f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"


@router.post("/connect", response_model=SlackAuthUrlResponse)
async def connect_slack(
    user: UserContext = Depends(get_current_user),
):
    """Start Slack OAuth flow. Returns the Slack consent URL."""
    if not settings.SLACK_CLIENT_ID or not settings.SLACK_CLIENT_SECRET:
        raise HTTPException(503, "Slack integration not configured")

    state = f"{user.user_id}:slack:{secrets.token_urlsafe(32)}"
    redirect_uri = _slack_redirect_uri()

    auth_url = slack_service.get_auth_url(redirect_uri, state)
    return SlackAuthUrlResponse(auth_url=auth_url)


@router.post("/callback", response_model=SlackConnectionResponse)
async def slack_callback(
    body: SlackCallbackRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Slack OAuth callback — exchange code for tokens and save connection."""
    try:
        user_id_str = body.state.split(":")[0]
        state_user_id = uuid.UUID(user_id_str)
    except (ValueError, IndexError):
        raise HTTPException(400, "Invalid state parameter")

    if str(state_user_id) != user.user_id:
        raise HTTPException(403, "State user_id mismatch")

    redirect_uri = _slack_redirect_uri()

    try:
        encrypted_tokens, workspace_id, workspace_name, scopes = (
            slack_service.exchange_code_for_tokens(body.code, redirect_uri)
        )
    except Exception as e:
        logger.error(f"Slack OAuth token exchange failed: {e}")
        raise HTTPException(400, "Failed to complete Slack authentication")

    # Upsert: delete existing connection for this user+workspace, then create new
    existing = await db.execute(
        select(SlackConnection).where(
            SlackConnection.user_id == uuid.UUID(user.user_id),
            SlackConnection.workspace_id == workspace_id,
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    connection = SlackConnection(
        user_id=uuid.UUID(user.user_id),
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        encrypted_tokens=encrypted_tokens,
        scopes=scopes,
    )
    db.add(connection)
    await db.flush()

    # Create default notification settings
    notif_settings = SlackNotificationSettings(
        connection_id=connection.id,
        user_id=uuid.UUID(user.user_id),
    )
    db.add(notif_settings)
    await db.commit()
    await db.refresh(connection, attribute_names=["notification_settings"])

    return SlackConnectionResponse.model_validate(connection)


@router.delete("/disconnect")
async def disconnect_slack(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Slack integration. Revokes token and deletes connection."""
    result = await db.execute(
        select(SlackConnection).where(
            SlackConnection.user_id == uuid.UUID(user.user_id)
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Slack connection found")

    # Best-effort token revocation
    slack_service.revoke_token(connection.encrypted_tokens)

    await db.delete(connection)
    await db.commit()
    return {"message": "Slack disconnected"}


@router.get("/status", response_model=SlackConnectionResponse)
async def slack_status(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if Slack is connected for this user."""
    result = await db.execute(
        select(SlackConnection)
        .options(selectinload(SlackConnection.notification_settings))
        .where(SlackConnection.user_id == uuid.UUID(user.user_id))
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Slack connection found")

    return SlackConnectionResponse.model_validate(connection)


@router.patch("/settings", response_model=SlackNotificationSettingsResponse)
async def update_slack_settings(
    body: SlackNotificationSettingsUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Slack notification settings.

    When a channel is selected, the bot auto-joins public channels.
    For private channels, the user must invite the bot manually.
    """
    result = await db.execute(
        select(SlackNotificationSettings).where(
            SlackNotificationSettings.user_id == uuid.UUID(user.user_id)
        )
    )
    notif_settings = result.scalar_one_or_none()
    if not notif_settings:
        raise HTTPException(404, "No Slack notification settings found")

    # If channel is changing, auto-join the new channel (public only)
    new_channel_id = body.channel_id
    if new_channel_id and new_channel_id != notif_settings.channel_id:
        conn_result = await db.execute(
            select(SlackConnection).where(
                SlackConnection.user_id == uuid.UUID(user.user_id)
            )
        )
        connection = conn_result.scalar_one_or_none()
        if connection:
            slack_service.join_channel(connection.encrypted_tokens, new_channel_id)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(notif_settings, field, value)

    await db.commit()
    await db.refresh(notif_settings)

    return SlackNotificationSettingsResponse.model_validate(notif_settings)


@router.get("/channels", response_model=SlackChannelsResponse)
async def list_slack_channels(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List channels available in the connected Slack workspace."""
    result = await db.execute(
        select(SlackConnection).where(
            SlackConnection.user_id == uuid.UUID(user.user_id)
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Slack connection found")

    try:
        channels = slack_service.list_channels(connection.encrypted_tokens)
    except Exception as e:
        logger.error(f"Failed to list Slack channels: {e}")
        raise HTTPException(502, "Failed to list Slack channels")

    return SlackChannelsResponse(channels=channels)


# ---------------------------------------------------------------------------
# Internal endpoint for cross-service Slack messaging (called by core_api)
# ---------------------------------------------------------------------------

internal_router = APIRouter(prefix="/integrations/internal/slack", tags=["slack-internal"])


class InternalSlackMessage(BaseModel):
    user_id: str
    text: str
    blocks: list[dict] | None = None


@internal_router.post("/send-message")
async def internal_send_message(
    body: InternalSlackMessage,
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Send a Slack message on behalf of a user. Protected by internal service key."""
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    user_uuid = uuid.UUID(body.user_id)

    # Look up connection
    conn_result = await db.execute(
        select(SlackConnection).where(SlackConnection.user_id == user_uuid)
    )
    connection = conn_result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Slack connection for this user")

    # Look up notification settings for channel
    settings_result = await db.execute(
        select(SlackNotificationSettings).where(
            SlackNotificationSettings.user_id == user_uuid
        )
    )
    notif_settings = settings_result.scalar_one_or_none()
    if not notif_settings or not notif_settings.channel_id:
        raise HTTPException(404, "No Slack channel configured for this user")

    # Send the message
    try:
        success = slack_service.post_message(
            connection.encrypted_tokens,
            notif_settings.channel_id,
            body.text,
            body.blocks,
        )
        if not success:
            raise HTTPException(502, "Slack message delivery failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Slack post_message error: {e}")
        raise HTTPException(502, f"Slack send failed: {e}")

    return {"status": "sent", "channel_id": notif_settings.channel_id}
