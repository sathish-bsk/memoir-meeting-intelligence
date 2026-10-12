"""
Zoom Cloud Recording integration API routes (integration_service).
Handles OAuth connect/disconnect, status, and recording preference settings.
"""

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.recording import RecordingConnection
from app.schemas.zoom import (
    ZoomAuthUrlResponse,
    ZoomCallbackRequest,
    ZoomConnectionResponse,
    ZoomSettingsUpdate,
)
from app.services.zoom_oauth import zoom_oauth_service
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/zoom", tags=["zoom"])

OAUTH_REDIRECT_PATH = "/integrations"


async def _get_user_primary_team(db: AsyncSession, user_id: str) -> uuid.UUID:
    """Get the user's primary team_id from team_members table (raw SQL)."""
    result = await db.execute(
        text("SELECT team_id FROM team_members WHERE user_id = :uid LIMIT 1"),
        {"uid": user_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(400, "User has no team")
    return row[0]


@router.post("/connect", response_model=ZoomAuthUrlResponse)
async def connect_zoom(
    user: UserContext = Depends(get_current_user),
):
    """Start Zoom OAuth flow. Returns the Zoom consent URL."""
    if not settings.ZOOM_CLIENT_ID or not settings.ZOOM_CLIENT_SECRET:
        raise HTTPException(503, "Zoom integration not configured")

    state = f"{user.user_id}:zoom:{secrets.token_urlsafe(32)}"
    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    auth_url = zoom_oauth_service.get_auth_url(redirect_uri, state)
    return ZoomAuthUrlResponse(auth_url=auth_url)


@router.post("/callback", response_model=ZoomConnectionResponse)
async def zoom_callback(
    body: ZoomCallbackRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Zoom OAuth callback — exchange code for tokens and save connection."""
    # Validate state
    try:
        user_id_str = body.state.split(":")[0]
        state_user_id = uuid.UUID(user_id_str)
    except (ValueError, IndexError):
        raise HTTPException(400, "Invalid state parameter")

    if str(state_user_id) != user.user_id:
        raise HTTPException(403, "State user_id mismatch")

    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    try:
        encrypted_tokens, zoom_email, zoom_user_id = (
            await zoom_oauth_service.exchange_code_for_tokens(body.code, redirect_uri)
        )
    except Exception as e:
        logger.error(f"Zoom OAuth token exchange failed: {e}")
        raise HTTPException(400, "Failed to complete Zoom authentication")

    # Check if cloud recording is enabled
    cloud_recording_enabled = False
    try:
        tokens = zoom_oauth_service.decrypt_tokens(encrypted_tokens)
        rec_settings = await zoom_oauth_service.check_recording_settings(
            tokens["access_token"]
        )
        recording_cfg = rec_settings.get("recording", {})
        cloud_recording_enabled = recording_cfg.get("cloud_recording", False)
    except Exception as e:
        logger.warning(f"Could not check Zoom recording settings: {e}")

    # Get user's team_id
    team_id = await _get_user_primary_team(db, user.user_id)

    # Upsert: delete existing Zoom connection for this user, then create new
    existing = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user.user_id),
            RecordingConnection.provider == "zoom",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    connection = RecordingConnection(
        user_id=uuid.UUID(user.user_id),
        team_id=team_id,
        provider="zoom",
        encrypted_tokens=encrypted_tokens,
        provider_user_id=zoom_user_id,
        provider_email=zoom_email,
        auto_import=True,
        recording_preference="auto",
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    resp = ZoomConnectionResponse.model_validate(connection)
    resp.cloud_recording_enabled = cloud_recording_enabled
    return resp


@router.delete("/disconnect")
async def disconnect_zoom(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Zoom integration. Revokes token and deletes connection."""
    result = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user.user_id),
            RecordingConnection.provider == "zoom",
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Zoom connection found")

    # Best-effort token revocation
    try:
        tokens = zoom_oauth_service.decrypt_tokens(connection.encrypted_tokens)
        await zoom_oauth_service.revoke_token(tokens["access_token"])
    except Exception as e:
        logger.warning(f"Failed to revoke Zoom token: {e}")

    await db.delete(connection)
    await db.commit()
    return {"message": "Zoom disconnected"}


@router.get("/status", response_model=ZoomConnectionResponse)
async def zoom_status(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if Zoom is connected for this user."""
    result = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user.user_id),
            RecordingConnection.provider == "zoom",
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Zoom connection found")

    # Check if cloud recording is still enabled
    cloud_recording_enabled = False
    try:
        access_token, new_encrypted = await zoom_oauth_service.get_valid_access_token(
            connection.encrypted_tokens
        )
        if new_encrypted:
            connection.encrypted_tokens = new_encrypted
            await db.commit()

        rec_settings = await zoom_oauth_service.check_recording_settings(access_token)
        recording_cfg = rec_settings.get("recording", {})
        cloud_recording_enabled = recording_cfg.get("cloud_recording", False)
    except Exception as e:
        logger.warning(f"Could not check Zoom recording settings: {e}")

    resp = ZoomConnectionResponse.model_validate(connection)
    resp.cloud_recording_enabled = cloud_recording_enabled
    return resp


@router.patch("/settings")
async def update_zoom_settings(
    body: ZoomSettingsUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Zoom recording preferences (auto_import, recording_preference)."""
    result = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user.user_id),
            RecordingConnection.provider == "zoom",
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No Zoom connection found")

    if body.auto_import is not None:
        connection.auto_import = body.auto_import

    if body.recording_preference is not None:
        if body.recording_preference not in ("bot", "zoom_cloud", "auto"):
            raise HTTPException(400, "Invalid recording_preference. Must be: bot, zoom_cloud, auto")
        connection.recording_preference = body.recording_preference

    await db.commit()
    await db.refresh(connection)

    return ZoomConnectionResponse.model_validate(connection)
