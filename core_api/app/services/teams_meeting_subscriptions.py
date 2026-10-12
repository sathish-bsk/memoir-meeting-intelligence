"""
Teams meeting event subscriptions — creates per-meeting Graph API subscriptions
for callStarted events to detect early meeting joins.

Subscriptions are created ~30 minutes before a Teams meeting starts and cleaned
up after the meeting ends.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.calendar import CalendarConnection, CalendarEvent

logger = logging.getLogger(__name__)

GRAPH_SUBSCRIPTIONS_URL = "https://graph.microsoft.com/v1.0/subscriptions"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def _get_fernet() -> Fernet:
    """Get Fernet instance for token decryption."""
    import os
    key = os.getenv("CALENDAR_ENCRYPTION_KEY") or settings.CALENDAR_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


async def _refresh_outlook_token(encrypted_tokens: str) -> dict | None:
    """Decrypt and refresh Outlook OAuth tokens if needed."""
    import json

    fernet = _get_fernet()
    tokens = json.loads(fernet.decrypt(encrypted_tokens.encode()).decode())

    # Check if token is expired (with 5-min buffer)
    expires_at = tokens.get("expires_at", 0)
    if datetime.now(timezone.utc).timestamp() < expires_at - 300:
        return tokens

    # Refresh the token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            MS_TOKEN_URL,
            data={
                "client_id": settings.OUTLOOK_CLIENT_ID,
                "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.error(f"Token refresh failed: {resp.text}")
            return None

        new_tokens = resp.json()
        tokens["access_token"] = new_tokens["access_token"]
        tokens["expires_at"] = datetime.now(timezone.utc).timestamp() + new_tokens.get("expires_in", 3600)
        if "refresh_token" in new_tokens:
            tokens["refresh_token"] = new_tokens["refresh_token"]

        return tokens


async def create_meeting_start_subscription(
    db: AsyncSession,
    connection: CalendarConnection,
    cal_event: CalendarEvent,
) -> bool:
    """Create a Graph subscription for meeting start (callStarted) events.

    Returns True if subscription was created successfully.
    """
    tokens = await _refresh_outlook_token(connection.encrypted_tokens)
    if not tokens:
        logger.error(f"Cannot refresh tokens for connection {connection.id}")
        return False

    # Encode the Teams join URL for the Graph resource path
    join_url = cal_event.meeting_url
    encoded_url = quote(join_url, safe="")

    # Subscription expires at event end_time + 1 hour (max 4230 minutes for online meetings)
    expiration = min(
        cal_event.end_time + timedelta(hours=1),
        datetime.now(timezone.utc) + timedelta(minutes=4200),
    )

    webhook_url = settings.CALENDAR_WEBHOOK_BASE_URL.rstrip("/") + "/api/webhooks/teams-meeting-events"

    subscription_body = {
        "changeType": "created",
        "notificationUrl": webhook_url,
        "resource": f"communications/onlineMeetings?$filter=joinWebUrl eq '{join_url}'",
        "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "clientState": settings.TEAMS_CLOUD_CLIENT_STATE_SECRET,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPH_SUBSCRIPTIONS_URL,
            json=subscription_body,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=15,
        )

        if resp.status_code in (200, 201):
            sub_data = resp.json()
            cal_event.meeting_subscription_id = sub_data["id"]
            await db.commit()
            logger.info(
                f"Created Teams meeting subscription {sub_data['id']} for event {cal_event.id}"
            )
            return True
        else:
            logger.error(
                f"Failed to create Teams meeting subscription for event {cal_event.id}: "
                f"{resp.status_code} {resp.text}"
            )
            return False


async def create_subscriptions_for_upcoming_teams_events(db: AsyncSession) -> int:
    """Scan for Teams calendar events starting within 30 minutes and create subscriptions.

    Returns number of subscriptions created.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)

    result = await db.execute(
        select(CalendarEvent, CalendarConnection)
        .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.meeting_url.op("~*")("teams\\.(microsoft\\.com|live\\.com)"),
            CalendarEvent.start_time >= now,
            CalendarEvent.start_time <= window_end,
            CalendarEvent.bot_scheduled == False,  # noqa: E712
            CalendarEvent.meeting_subscription_id.is_(None),
            CalendarConnection.auto_record == True,  # noqa: E712
            CalendarConnection.provider == "outlook",
        )
    )
    rows = result.all()

    created = 0
    for cal_event, connection in rows:
        try:
            if await create_meeting_start_subscription(db, connection, cal_event):
                created += 1
        except Exception as e:
            logger.error(f"Failed to create Teams subscription for event {cal_event.id}: {e}")

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
            CalendarConnection.provider == "outlook",
        )
    )
    rows = result.all()

    cleaned = 0
    for cal_event, connection in rows:
        try:
            tokens = await _refresh_outlook_token(connection.encrypted_tokens)
            if tokens:
                async with httpx.AsyncClient() as client:
                    await client.delete(
                        f"{GRAPH_SUBSCRIPTIONS_URL}/{cal_event.meeting_subscription_id}",
                        headers={"Authorization": f"Bearer {tokens['access_token']}"},
                        timeout=10,
                    )
            cal_event.meeting_subscription_id = None
            await db.commit()
            cleaned += 1
        except Exception as e:
            logger.error(f"Failed to cleanup subscription for event {cal_event.id}: {e}")

    return cleaned
