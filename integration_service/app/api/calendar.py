"""
Calendar integration API routes (integration_service).
Handles OAuth connect/disconnect, sync, and connection status.
User-based: each user connects their own calendar account.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.calendar import CalendarConnection, CalendarEvent
from app.schemas.calendar import (
    AuthUrlResponse,
    CalendarCallbackRequest,
    CalendarConnectionResponse,
    CalendarEventResponse,
    CalendarSyncResponse,
)
from app.services.google_calendar import google_calendar_service
from app.services.outlook_calendar import outlook_calendar_service
from app.services.oauth_errors import TokenInvalidError
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/calendar", tags=["calendar"])

# OAuth redirect path on the frontend
OAUTH_REDIRECT_PATH = "/integrations"


# ─── Google Calendar ──────────────────────────────────────────────────


@router.post("/connect/google", response_model=AuthUrlResponse)
async def connect_google_calendar(
    user: UserContext = Depends(get_current_user),
):
    """Start Google Calendar OAuth flow. Returns the Google consent URL."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google Calendar integration not configured")

    state = f"{user.user_id}:google:{secrets.token_urlsafe(32)}"
    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    auth_url = google_calendar_service.get_auth_url(redirect_uri, state)
    return AuthUrlResponse(auth_url=auth_url)


@router.post("/callback/google", response_model=CalendarConnectionResponse)
async def google_callback(
    body: CalendarCallbackRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback — exchange code for tokens and save connection."""
    # Parse user_id from state
    try:
        user_id_str = body.state.split(":")[0]
        state_user_id = uuid.UUID(user_id_str)
    except (ValueError, IndexError):
        raise HTTPException(400, "Invalid state parameter")

    if str(state_user_id) != user.user_id:
        raise HTTPException(403, "State user_id mismatch")

    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    try:
        encrypted_tokens, provider_email = google_calendar_service.exchange_code_for_tokens(
            body.code, redirect_uri
        )
    except Exception as e:
        logger.error(f"Google OAuth token exchange failed: {e}")
        raise HTTPException(400, "Failed to complete Google authentication")

    # Upsert: delete existing connection for this user+provider, then create new
    existing = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.user_id == uuid.UUID(user.user_id),
            CalendarConnection.provider == "google",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    connection = CalendarConnection(
        user_id=uuid.UUID(user.user_id),
        provider="google",
        encrypted_tokens=encrypted_tokens,
        provider_email=provider_email,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    # Initial sync + register push notification channel (non-blocking)
    try:
        await _initial_sync_and_watch(db, connection)
    except Exception as e:
        logger.warning(f"Initial Google sync/watch registration failed (non-fatal): {e}")

    return CalendarConnectionResponse.model_validate(connection)


# ─── Outlook Calendar ─────────────────────────────────────────────────


@router.post("/connect/outlook", response_model=AuthUrlResponse)
async def connect_outlook_calendar(
    user: UserContext = Depends(get_current_user),
):
    """Start Outlook Calendar OAuth flow. Returns the Microsoft consent URL."""
    if not settings.OUTLOOK_CLIENT_ID or not settings.OUTLOOK_CLIENT_SECRET:
        raise HTTPException(503, "Outlook Calendar integration not configured")

    state = f"{user.user_id}:outlook:{secrets.token_urlsafe(32)}"
    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    auth_url = outlook_calendar_service.get_auth_url(redirect_uri, state)
    return AuthUrlResponse(auth_url=auth_url)


@router.post("/callback/outlook", response_model=CalendarConnectionResponse)
async def outlook_callback(
    body: CalendarCallbackRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Handle Outlook OAuth callback — exchange code for tokens and save connection."""
    # Parse user_id from state
    try:
        user_id_str = body.state.split(":")[0]
        state_user_id = uuid.UUID(user_id_str)
    except (ValueError, IndexError):
        raise HTTPException(400, "Invalid state parameter")

    if str(state_user_id) != user.user_id:
        raise HTTPException(403, "State user_id mismatch")

    redirect_uri = f"{settings.FRONTEND_URL}{OAUTH_REDIRECT_PATH}"

    try:
        encrypted_tokens, provider_email = outlook_calendar_service.exchange_code_for_tokens(
            body.code, redirect_uri
        )
    except Exception as e:
        logger.error(f"Outlook OAuth token exchange failed: {e}")
        raise HTTPException(400, "Failed to complete Outlook authentication")

    # Upsert: delete existing connection for this user+provider, then create new
    existing = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.user_id == uuid.UUID(user.user_id),
            CalendarConnection.provider == "outlook",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    connection = CalendarConnection(
        user_id=uuid.UUID(user.user_id),
        provider="outlook",
        encrypted_tokens=encrypted_tokens,
        provider_email=provider_email,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    # Initial sync + register push notification subscription (non-blocking)
    try:
        await _initial_sync_and_watch(db, connection)
    except Exception as e:
        logger.warning(f"Initial Outlook sync/subscription registration failed (non-fatal): {e}")

    return CalendarConnectionResponse.model_validate(connection)


# ─── Multi-provider: Status / Disconnect / Sync ───────────────────────


@router.delete("/disconnect")
async def disconnect_calendar(
    provider: str | None = Query(None),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect calendar integration. Deletes connection and cascades events.

    If provider is specified, only disconnect that provider.
    Otherwise disconnect all connections for the user.
    """
    query = select(CalendarConnection).where(
        CalendarConnection.user_id == uuid.UUID(user.user_id)
    )
    if provider:
        query = query.where(CalendarConnection.provider == provider)

    result = await db.execute(query)
    connections = result.scalars().all()
    if not connections:
        raise HTTPException(404, "No calendar connection found")

    for conn in connections:
        await db.delete(conn)
    await db.commit()
    return {"message": "Calendar disconnected"}


@router.get("/status", response_model=CalendarConnectionResponse)
async def calendar_status(
    provider: str | None = Query(None),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if calendar is connected for this user.

    If provider is specified, return that provider's connection.
    Otherwise return the first found connection.
    """
    query = select(CalendarConnection).where(
        CalendarConnection.user_id == uuid.UUID(user.user_id)
    )
    if provider:
        query = query.where(CalendarConnection.provider == provider)

    result = await db.execute(query)
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "No calendar connection found")

    return CalendarConnectionResponse.model_validate(connection)


@router.post("/sync", response_model=CalendarSyncResponse)
async def sync_calendar(
    provider: str | None = Query(None),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually sync calendar events.

    If provider is specified, sync only that provider.
    Otherwise sync all connected providers.
    """
    query = select(CalendarConnection).where(
        CalendarConnection.user_id == uuid.UUID(user.user_id)
    )
    if provider:
        query = query.where(CalendarConnection.provider == provider)

    result = await db.execute(query)
    connections = result.scalars().all()
    if not connections:
        raise HTTPException(404, "No calendar connection found")

    all_synced_events = []

    for connection in connections:
        try:
            events_data = _fetch_events_for_connection(connection)
        except TokenInvalidError as e:
            # OAuth refresh token rejected by provider. Mark the connection
            # so the frontend can stop showing "Connected" and prompt the
            # user to reconnect via the OAuth flow. We return 401 with a
            # structured body so the UI doesn't need to parse the message.
            connection.token_invalid = True
            connection.token_invalid_at = datetime.now(timezone.utc)
            await db.commit()
            logger.warning(
                f"Calendar token invalid for user {user.user_id} provider {connection.provider}: {e.reason}; marked token_invalid=true"
            )
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "token_invalid",
                    "provider": connection.provider,
                    "reason": e.reason,
                    "message": f"Your {connection.provider} calendar connection has expired. Please reconnect.",
                },
            )
        except Exception as e:
            logger.error(f"Calendar sync failed for user {user.user_id} provider {connection.provider}: {e}")
            raise HTTPException(502, f"Failed to sync with {connection.provider} calendar")

        # Upsert events
        for ev in events_data:
            start = _parse_dt(ev["start_time"])
            end = _parse_dt(ev["end_time"])

            existing_event = await db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.connection_id == connection.id,
                    CalendarEvent.provider_event_id == ev["provider_event_id"],
                )
            )
            cal_event = existing_event.scalar_one_or_none()

            if cal_event:
                cal_event.title = ev["title"]
                cal_event.description = ev.get("description")
                cal_event.start_time = start
                cal_event.end_time = end
                cal_event.meeting_url = ev.get("meeting_url")
                cal_event.attendees = ev.get("attendees")
                cal_event.updated_at = datetime.now(timezone.utc)
            else:
                cal_event = CalendarEvent(
                    connection_id=connection.id,
                    provider_event_id=ev["provider_event_id"],
                    title=ev["title"],
                    description=ev.get("description"),
                    start_time=start,
                    end_time=end,
                    meeting_url=ev.get("meeting_url"),
                    attendees=ev.get("attendees"),
                )
                db.add(cal_event)

            await db.flush()
            all_synced_events.append(cal_event)

        connection.last_synced_at = datetime.now(timezone.utc)

    await db.commit()

    # Refresh all events to get generated IDs
    event_responses = []
    for ev in all_synced_events:
        await db.refresh(ev)
        event_responses.append(CalendarEventResponse.model_validate(ev))

    return CalendarSyncResponse(synced_count=len(event_responses), events=event_responses)


def _fetch_events_for_connection(connection: CalendarConnection) -> list[dict]:
    """Fetch events from the correct provider service."""
    if connection.provider == "google":
        credentials, new_encrypted = google_calendar_service.refresh_if_needed(
            connection.encrypted_tokens
        )
        if new_encrypted:
            connection.encrypted_tokens = new_encrypted
        return google_calendar_service.fetch_events(connection.encrypted_tokens)
    elif connection.provider == "outlook":
        access_token, new_encrypted = outlook_calendar_service.refresh_if_needed(
            connection.encrypted_tokens
        )
        if new_encrypted:
            connection.encrypted_tokens = new_encrypted
        return outlook_calendar_service.fetch_events(connection.encrypted_tokens)
    else:
        raise ValueError(f"Unknown calendar provider: {connection.provider}")


def _parse_dt(val):
    """Parse ISO datetime string to timezone-aware datetime object if needed."""
    if isinstance(val, str):
        # Handle Z suffix (UTC) — fromisoformat doesn't support Z in Python <3.11
        val = val.replace("Z", "+00:00")
        return datetime.fromisoformat(val)
    return val


async def _initial_sync_and_watch(
    db: AsyncSession, connection: CalendarConnection
) -> None:
    """Do initial full sync and register push notification channel after OAuth connect.

    Uses the same sync logic as manual sync, then registers a watch channel
    (Google) or Graph subscription (Microsoft) for real-time updates.
    """
    import os

    # Step 1: Initial full sync (populates calendar_events + saves syncToken)
    events_data = _fetch_events_for_connection(connection)

    for ev in events_data:
        start = _parse_dt(ev["start_time"])
        end = _parse_dt(ev["end_time"])

        existing_event = await db.execute(
            select(CalendarEvent).where(
                CalendarEvent.connection_id == connection.id,
                CalendarEvent.provider_event_id == ev["provider_event_id"],
            )
        )
        cal_event = existing_event.scalar_one_or_none()

        if cal_event:
            cal_event.title = ev["title"]
            cal_event.description = ev.get("description")
            cal_event.start_time = start
            cal_event.end_time = end
            cal_event.meeting_url = ev.get("meeting_url")
            cal_event.attendees = ev.get("attendees")
            cal_event.updated_at = datetime.now(timezone.utc)
        else:
            cal_event = CalendarEvent(
                connection_id=connection.id,
                provider_event_id=ev["provider_event_id"],
                title=ev["title"],
                description=ev.get("description"),
                start_time=start,
                end_time=end,
                meeting_url=ev.get("meeting_url"),
                attendees=ev.get("attendees"),
            )
            db.add(cal_event)

    connection.last_synced_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(f"Initial sync completed for {connection.provider} connection {connection.id}: {len(events_data)} events")

    # Step 2: Register push notification channel/subscription
    # The webhook URL must be publicly accessible (HTTPS). In dev mode, skip registration.
    webhook_base_url = os.getenv("CALENDAR_WEBHOOK_BASE_URL", "")
    if not webhook_base_url:
        logger.info("CALENDAR_WEBHOOK_BASE_URL not set — skipping push notification registration (dev mode)")
        return

    try:
        if connection.provider == "google":
            webhook_url = f"{webhook_base_url}/api/webhooks/google-calendar"

            import uuid as uuid_mod
            import httpx

            token_data = google_calendar_service.decrypt_tokens(connection.encrypted_tokens)
            # refresh_if_needed returns (credentials, new_encrypted)
            credentials, new_encrypted = google_calendar_service.refresh_if_needed(connection.encrypted_tokens)
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted

            channel_id = str(uuid_mod.uuid4())
            resp = httpx.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events/watch",
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "id": channel_id,
                    "type": "web_hook",
                    "address": webhook_url,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            connection.watch_channel_id = channel_id
            connection.watch_resource_id = data.get("resourceId")
            expiration_ms = data.get("expiration")
            if expiration_ms:
                connection.watch_expires_at = datetime.fromtimestamp(
                    int(expiration_ms) / 1000, tz=timezone.utc
                )

            logger.info(f"Registered Google watch channel {channel_id}")

        elif connection.provider == "outlook":
            webhook_url = f"{webhook_base_url}/api/webhooks/microsoft-calendar"
            client_state = os.getenv("TEAMS_CLOUD_CLIENT_STATE_SECRET", "")

            import httpx
            access_token, new_encrypted = outlook_calendar_service.refresh_if_needed(connection.encrypted_tokens)
            if new_encrypted:
                connection.encrypted_tokens = new_encrypted

            expiration = datetime.now(timezone.utc) + timedelta(days=3)
            resp = httpx.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "changeType": "created,updated,deleted",
                    "notificationUrl": webhook_url,
                    "resource": "me/events",
                    "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
                    "clientState": client_state,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            connection.subscription_id = data["id"]
            connection.watch_expires_at = datetime.fromisoformat(
                data["expirationDateTime"].replace("Z", "+00:00")
            )

            logger.info(f"Registered Outlook subscription {data['id']}")

        await db.commit()

    except Exception as e:
        logger.warning(f"Push notification registration failed for {connection.provider} (non-fatal): {e}")
