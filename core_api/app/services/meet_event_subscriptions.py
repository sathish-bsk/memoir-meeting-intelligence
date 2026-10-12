"""
Google Meet event subscriptions — creates per-meeting Workspace Events API
subscriptions for conference.v2.started events to detect early meeting joins.

Requires:
- Google Meet REST API enabled in Google Cloud Console
- OAuth scope: https://www.googleapis.com/auth/meetings.space.readonly

Only works for Google Workspace accounts (not personal Gmail).
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.calendar import CalendarConnection, CalendarEvent
from app.utils.meeting_url import extract_meet_code

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MEET_SPACES_URL = "https://meet.googleapis.com/v2/spaces"
WORKSPACE_EVENTS_URL = "https://workspaceevents.googleapis.com/v1/subscriptions"


def _get_fernet() -> Fernet:
    """Get Fernet instance for token decryption."""
    import os
    key = os.getenv("CALENDAR_ENCRYPTION_KEY") or settings.CALENDAR_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


async def _refresh_google_token(encrypted_tokens: str) -> tuple[dict | None, bool]:
    """Decrypt and refresh Google OAuth tokens if needed.

    Returns (tokens, is_permanent_failure). is_permanent_failure=True means
    the refresh token is permanently revoked and the user must re-authenticate.
    """
    fernet = _get_fernet()
    tokens = json.loads(fernet.decrypt(encrypted_tokens.encode()).decode())

    expires_at = tokens.get("expires_at", 0)
    if datetime.now(timezone.utc).timestamp() < expires_at - 300:
        return tokens, False

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            body = resp.text
            logger.error(f"Google token refresh failed: {body}")
            is_permanent = "invalid_grant" in body
            return None, is_permanent

        new_tokens = resp.json()
        tokens["access_token"] = new_tokens["access_token"]
        tokens["expires_at"] = datetime.now(timezone.utc).timestamp() + new_tokens.get("expires_in", 3600)
        return tokens, False


async def _resolve_space_id(access_token: str, meet_code: str) -> str | None:
    """Resolve a Google Meet code to a space name/ID via the Meet REST API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MEET_SPACES_URL}/{meet_code}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("name")  # e.g. "spaces/abc-defg-hij"
        else:
            logger.warning(
                f"Failed to resolve Meet space for code {meet_code}: "
                f"{resp.status_code} {resp.text}"
            )
            return None


async def create_meet_start_subscription(
    db: AsyncSession,
    connection: CalendarConnection,
    cal_event: CalendarEvent,
) -> bool:
    """Create a Workspace Events subscription for Meet conference start.

    Returns True if subscription was created successfully.
    """
    meet_code = extract_meet_code(cal_event.meeting_url)
    if not meet_code:
        return False

    tokens, is_permanent = await _refresh_google_token(connection.encrypted_tokens)
    if not tokens:
        if is_permanent:
            connection.token_invalid = True
            connection.token_invalid_at = datetime.now(timezone.utc)
            await db.commit()
            logger.error(
                f"Connection {connection.id} ({connection.provider_email}): "
                f"token permanently revoked — marked as invalid, stopping retries"
            )
        else:
            logger.error(f"Cannot refresh tokens for connection {connection.id}")
        return False

    # Resolve the Meet code to a space ID
    space_name = await _resolve_space_id(tokens["access_token"], meet_code)
    if not space_name:
        # Likely a personal Gmail account or Meet API not enabled
        return False

    # Subscription TTL: event end_time + 1 hour, max 7 days
    ttl_seconds = int(
        min(
            (cal_event.end_time + timedelta(hours=1) - datetime.now(timezone.utc)).total_seconds(),
            7 * 24 * 3600,
        )
    )
    ttl_seconds = max(ttl_seconds, 600)  # Minimum 10 minutes

    pubsub_topic = settings.GOOGLE_MEET_PUBSUB_TOPIC
    if not pubsub_topic:
        logger.warning("GOOGLE_MEET_PUBSUB_TOPIC not configured, skipping Meet subscription")
        return False

    subscription_body = {
        "targetResource": f"//meet.googleapis.com/{space_name}",
        "eventTypes": ["google.workspace.meet.conference.v2.started"],
        "notificationEndpoint": {
            "pubsubTopic": pubsub_topic,
        },
        "ttl": f"{ttl_seconds}s",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WORKSPACE_EVENTS_URL,
            json=subscription_body,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=15,
        )

        if resp.status_code in (200, 201):
            sub_data = resp.json()
            # Store the space name (e.g. "spaces/O2OWWt3SzssB") for matching
            # against ce-subject in incoming Pub/Sub notifications.
            cal_event.meeting_subscription_id = space_name
            await db.commit()
            logger.info(
                f"Created Meet subscription for event {cal_event.id} "
                f"(space={space_name})"
            )
            return True
        else:
            logger.error(
                f"Failed to create Meet subscription for event {cal_event.id}: "
                f"{resp.status_code} {resp.text}"
            )
            return False


async def create_subscriptions_for_upcoming_meet_events(db: AsyncSession) -> int:
    """Scan for Google Meet events starting within 30 minutes and create subscriptions.

    Returns number of subscriptions created.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)

    result = await db.execute(
        select(CalendarEvent, CalendarConnection)
        .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.meeting_url.like("%meet.google.com%"),
            CalendarEvent.start_time >= now,
            CalendarEvent.start_time <= window_end,
            CalendarEvent.bot_scheduled == False,  # noqa: E712
            CalendarEvent.meeting_subscription_id.is_(None),
            CalendarConnection.auto_record == True,  # noqa: E712
            CalendarConnection.token_invalid == False,  # noqa: E712
            CalendarConnection.provider == "google",
        )
    )
    rows = result.all()

    created = 0
    for cal_event, connection in rows:
        try:
            if await create_meet_start_subscription(db, connection, cal_event):
                created += 1
        except Exception as e:
            logger.error(f"Failed to create Meet subscription for event {cal_event.id}: {e}")

    return created


async def cleanup_expired_subscriptions(db: AsyncSession) -> int:
    """Delete subscriptions for meetings that have already ended."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)

    result = await db.execute(
        select(CalendarEvent, CalendarConnection)
        .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.meeting_subscription_id.isnot(None),
            CalendarEvent.end_time < cutoff,
            CalendarConnection.provider == "google",
        )
    )
    rows = result.all()

    cleaned = 0
    for cal_event, connection in rows:
        try:
            tokens, _ = await _refresh_google_token(connection.encrypted_tokens)
            if tokens:
                async with httpx.AsyncClient() as client:
                    # DELETE the workspace events subscription
                    await client.delete(
                        f"{WORKSPACE_EVENTS_URL}/{cal_event.meeting_subscription_id}",
                        headers={"Authorization": f"Bearer {tokens['access_token']}"},
                        timeout=10,
                    )
            cal_event.meeting_subscription_id = None
            await db.commit()
            cleaned += 1
        except Exception as e:
            logger.error(f"Failed to cleanup Meet subscription for event {cal_event.id}: {e}")

    return cleaned
