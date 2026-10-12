"""
Microsoft Teams Cloud Recording integration API routes (integration_service).
Handles OAuth connect/disconnect, status, admin consent, and settings.
"""

import logging
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.recording import RecordingConnection
from app.services.teams_cloud_oauth import teams_cloud_oauth_service
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/teams-cloud", tags=["teams-cloud"])

OAUTH_REDIRECT_PATH = "/integrations"


# ─── Schemas ─────────────────────────────────────────────────────────────────

class TeamsAuthUrlResponse(BaseModel):
    auth_url: str


class TeamsCallbackRequest(BaseModel):
    code: str
    state: str


class TeamsConnectionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    provider_email: str
    auto_import: bool = True
    recording_preference: str = "auto"
    tenant_id: Optional[str] = None
    subscription_id: Optional[str] = None
    subscription_expiry: Optional[datetime] = None
    admin_consent_granted: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamsSettingsUpdate(BaseModel):
    auto_import: Optional[bool] = None
    recording_preference: Optional[str] = None


class AdminConsentCompleteRequest(BaseModel):
    tenant_id: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_user_primary_team(db: AsyncSession, user_id: str) -> uuid.UUID:
    result = await db.execute(
        text("SELECT team_id FROM team_members WHERE user_id = :uid LIMIT 1"),
        {"uid": user_id},
    )
    row = result.first()
    if not row:
        raise HTTPException(400, "User has no team")
    return row[0]


async def _get_teams_connection(db: AsyncSession, user_id: str) -> RecordingConnection:
    result = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user_id),
            RecordingConnection.provider == "teams",
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "No Teams connection found")
    return conn


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/connect", response_model=TeamsAuthUrlResponse)
async def connect_teams(
    user: UserContext = Depends(get_current_user),
):
    """Start Microsoft Teams OAuth flow. Returns the MS consent URL."""
    if not settings.OUTLOOK_CLIENT_ID or not settings.OUTLOOK_CLIENT_SECRET:
        raise HTTPException(503, "Teams integration not configured (missing OUTLOOK_CLIENT_ID)")

    state = f"{user.user_id}:teams:{secrets.token_urlsafe(32)}"
    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    auth_url = teams_cloud_oauth_service.get_auth_url(redirect_uri, state)
    return TeamsAuthUrlResponse(auth_url=auth_url)


@router.post("/callback", response_model=TeamsConnectionResponse)
async def teams_callback(
    body: TeamsCallbackRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle MS OAuth callback — exchange code, store connection, attempt subscription creation."""
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
        encrypted_tokens, ms_email, ms_user_id, tenant_id = (
            await teams_cloud_oauth_service.exchange_code_for_tokens(body.code, redirect_uri)
        )
    except Exception as e:
        logger.error(f"Teams OAuth token exchange failed: {e}")
        raise HTTPException(400, "Failed to complete Teams authentication")

    team_id = await _get_user_primary_team(db, user.user_id)

    # Upsert: remove existing Teams connection for this user
    existing = await db.execute(
        select(RecordingConnection).where(
            RecordingConnection.user_id == uuid.UUID(user.user_id),
            RecordingConnection.provider == "teams",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        # Delete old Graph subscription if any
        if old.subscription_id and old.tenant_id:
            await teams_cloud_oauth_service.delete_subscription(
                old.tenant_id, old.subscription_id
            )
        await db.delete(old)
        await db.flush()

    connection = RecordingConnection(
        user_id=uuid.UUID(user.user_id),
        team_id=team_id,
        provider="teams",
        encrypted_tokens=encrypted_tokens,
        provider_user_id=ms_user_id,
        provider_email=ms_email,
        auto_import=True,
        recording_preference="auto",
        tenant_id=tenant_id,
        admin_consent_granted=False,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    # Attempt to create Graph subscription (succeeds only if admin consent already granted)
    if tenant_id and settings.TEAMS_CLOUD_WEBHOOK_URL:
        try:
            sub_id, expiry = await teams_cloud_oauth_service.create_subscription(
                tenant_id,
                settings.TEAMS_CLOUD_WEBHOOK_URL,
                settings.TEAMS_CLOUD_CLIENT_STATE_SECRET,
            )
            connection.subscription_id = sub_id
            connection.subscription_expiry = expiry
            connection.admin_consent_granted = True
            await db.commit()
            await db.refresh(connection)
            logger.info(f"Teams subscription created: {sub_id} for tenant {tenant_id}")
        except Exception as e:
            # Admin consent likely not yet granted — connection is saved, subscription pending
            logger.info(f"Teams subscription not yet created (admin consent needed): {e}")

    return TeamsConnectionResponse.model_validate(connection)


@router.post("/admin-consent-complete")
async def admin_consent_complete(
    body: AdminConsentCompleteRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark admin consent as granted and create Graph subscription.

    Frontend calls this after detecting the admin consent redirect params.
    """
    connection = await _get_teams_connection(db, user.user_id)

    tenant_id = body.tenant_id or connection.tenant_id
    if not tenant_id:
        raise HTTPException(400, "tenant_id required")

    if not settings.TEAMS_CLOUD_WEBHOOK_URL:
        raise HTTPException(503, "Teams webhook URL not configured")

    try:
        sub_id, expiry = await teams_cloud_oauth_service.create_subscription(
            tenant_id,
            settings.TEAMS_CLOUD_WEBHOOK_URL,
            settings.TEAMS_CLOUD_CLIENT_STATE_SECRET,
        )
    except Exception as e:
        logger.error(f"Failed to create Teams subscription: {e}")
        raise HTTPException(400, f"Failed to create Graph subscription: {e}")

    connection.subscription_id = sub_id
    connection.subscription_expiry = expiry
    connection.admin_consent_granted = True
    connection.tenant_id = tenant_id
    await db.commit()
    await db.refresh(connection)

    return {
        "message": "Admin consent confirmed, subscription created",
        "subscription_id": sub_id,
    }


@router.get("/admin-consent-url")
async def get_admin_consent_url(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the MS admin consent URL for this user's tenant."""
    connection = await _get_teams_connection(db, user.user_id)
    if not connection.tenant_id:
        raise HTTPException(400, "No tenant ID found; reconnect Teams first")

    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"
    state = f"{user.user_id}:teams-admin:{secrets.token_urlsafe(16)}"
    url = teams_cloud_oauth_service.get_admin_consent_url(
        connection.tenant_id, redirect_uri, state
    )
    return {"admin_consent_url": url, "tenant_id": connection.tenant_id}


@router.delete("/disconnect")
async def disconnect_teams(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect Teams integration. Deletes subscription and connection."""
    connection = await _get_teams_connection(db, user.user_id)

    # Best-effort subscription deletion
    if connection.subscription_id and connection.tenant_id:
        await teams_cloud_oauth_service.delete_subscription(
            connection.tenant_id, connection.subscription_id
        )

    await db.delete(connection)
    await db.commit()
    return {"message": "Teams disconnected"}


@router.get("/status", response_model=TeamsConnectionResponse)
async def teams_status(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if Microsoft Teams Cloud Recording is connected for this user."""
    connection = await _get_teams_connection(db, user.user_id)
    return TeamsConnectionResponse.model_validate(connection)


@router.patch("/settings")
async def update_teams_settings(
    body: TeamsSettingsUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Teams recording preferences."""
    connection = await _get_teams_connection(db, user.user_id)

    if body.auto_import is not None:
        connection.auto_import = body.auto_import

    if body.recording_preference is not None:
        if body.recording_preference not in ("bot", "teams_cloud", "auto"):
            raise HTTPException(
                400, "Invalid recording_preference. Must be: bot, teams_cloud, auto"
            )
        connection.recording_preference = body.recording_preference

    await db.commit()
    await db.refresh(connection)
    return TeamsConnectionResponse.model_validate(connection)
