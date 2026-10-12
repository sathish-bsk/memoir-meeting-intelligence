"""
Calendar sync service — incremental sync via syncToken/deltaLink and
watch channel/subscription management for push notifications.

Google: uses events.watch() for push, events.list(syncToken) for incremental sync.
Microsoft: uses Graph subscriptions for push, calendarView/delta for incremental sync.
"""

import json
import logging
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.calendar import CalendarConnection, CalendarEvent

logger = logging.getLogger(__name__)

# Google Calendar API
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GOOGLE_WATCH_URL = f"{GOOGLE_EVENTS_URL}/watch"
GOOGLE_STOP_CHANNEL_URL = "https://www.googleapis.com/calendar/v3/channels/stop"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Microsoft Graph API
GRAPH_CALENDAR_DELTA_URL = "https://graph.microsoft.com/v1.0/me/calendarView/delta"
GRAPH_SUBSCRIPTIONS_URL = "https://graph.microsoft.com/v1.0/subscriptions"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

# Meeting URL regex patterns (shared with integration_service)
import re

MEETING_URL_PATTERNS = [
    re.compile(r"https?://meet\.google\.com/[a-z\-]+", re.IGNORECASE),
    re.compile(r"https?://[\w.-]*zoom\.us/j/\d+[^\s\"<]*", re.IGNORECASE),
    re.compile(r"https?://teams\.microsoft\.com/l/meetup-join/[^\s\"<]+", re.IGNORECASE),
]


def _get_fernet() -> Fernet:
    """Get Fernet instance for token encryption/decryption."""
    import os
    # Prefer env var (set by systemd EnvironmentFile in production),
    # fall back to settings (loaded from .env.{ENVIRONMENT} file)
    key = os.getenv("CALENDAR_ENCRYPTION_KEY", "")
    if not key or key.startswith("<"):
        key = getattr(settings, "CALENDAR_ENCRYPTION_KEY", "")
    if not key or key.startswith("<"):
        raise ValueError("CALENDAR_ENCRYPTION_KEY not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


# ─── Google Calendar ─────────────────────────────────────────────────


def _decrypt_google_tokens(encrypted_tokens: str) -> dict:
    """Decrypt Google OAuth tokens."""
    fernet = _get_fernet()
    return json.loads(fernet.decrypt(encrypted_tokens.encode()))


def _encrypt_tokens(token_data: dict) -> str:
    """Encrypt token data."""
    fernet = _get_fernet()
    return fernet.encrypt(json.dumps(token_data).encode()).decode()


def _refresh_google_token_if_needed(token_data: dict) -> tuple[str, bool]:
    """Refresh Google access token if expired. Returns (access_token, was_refreshed)."""
    expiry_str = token_data.get("expiry")
    if expiry_str:
        expiry = datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > datetime.now(timezone.utc) + timedelta(seconds=60):
            return token_data["token"], False

    # Token expired — refresh
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("No refresh token available")

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": token_data.get("client_id", settings.GOOGLE_CLIENT_ID),
            "client_secret": token_data.get("client_secret", settings.GOOGLE_CLIENT_SECRET),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    new_tokens = resp.json()

    token_data["token"] = new_tokens["access_token"]
    if "expires_in" in new_tokens:
        expiry = datetime.now(timezone.utc) + timedelta(seconds=new_tokens["expires_in"])
        token_data["expiry"] = expiry.isoformat()

    return token_data["token"], True


async def google_incremental_sync(
    db: AsyncSession,
    connection: CalendarConnection,
) -> int:
    """Perform incremental sync using Google syncToken. Returns count of events changed."""
    # Meeting IDs whose status was flipped to "failed" by an inbound
    # cancellation/reschedule mid-sync. Cascaded to the /status cache
    # AFTER the commit at the bottom of this function — cascade_from_db
    # reads committed state, so calling it inside the loop would push
    # stale data if the upsert is later rolled back.
    affected_meetings: list = []
    token_data = _decrypt_google_tokens(connection.encrypted_tokens)
    access_token, was_refreshed = _refresh_google_token_if_needed(token_data)

    if was_refreshed:
        connection.encrypted_tokens = _encrypt_tokens(token_data)

    params = {"singleEvents": "true", "orderBy": "startTime"}

    if connection.sync_token:
        # Incremental sync — only changed events
        params["syncToken"] = connection.sync_token
    else:
        # Initial full sync — fetch next 7 days
        now = datetime.now(timezone.utc)
        params["timeMin"] = now.isoformat()
        params["timeMax"] = (now + timedelta(days=7)).isoformat()
        params["maxResults"] = "100"

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        resp = httpx.get(GOOGLE_EVENTS_URL, params=params, headers=headers)

        # syncToken expired — do full resync
        if resp.status_code == 410:
            logger.info(f"Google syncToken expired for connection {connection.id}, doing full resync")
            connection.sync_token = None
            return await google_incremental_sync(db, connection)

        resp.raise_for_status()
    except httpx.HTTPStatusError:
        raise

    data = resp.json()
    events = data.get("items", [])
    changed = 0

    for event in events:
        provider_event_id = event["id"]
        ics_sequence = event.get("sequence")
        organizer_email = (event.get("organizer") or {}).get("email")
        event_status = event.get("status", "confirmed")

        # Cancelled events — Phase 2 keeps the row (sets cancelled_at)
        # so the dispatch-reset path can stop any in-flight bot. The
        # auto-scheduler's predicate filters out cancelled rows.
        if event_status == "cancelled":
            existing = await db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.connection_id == connection.id,
                    CalendarEvent.provider_event_id == provider_event_id,
                )
            )
            cal_event = existing.scalar_one_or_none()
            if cal_event:
                # Use the standard upsert path with status='cancelled'
                # so dispatch-reset logic fires uniformly.
                start_dt_str = cal_event.start_time.isoformat()
                end_dt_str = cal_event.end_time.isoformat()
                await _upsert_event(
                    db,
                    connection_id=connection.id,
                    provider_event_id=provider_event_id,
                    title=cal_event.title,
                    description=cal_event.description,
                    start_time=start_dt_str,
                    end_time=end_dt_str,
                    meeting_url=cal_event.meeting_url,
                    attendees=cal_event.attendees,
                    ics_sequence=ics_sequence,
                    organizer_email=organizer_email,
                    event_status="cancelled",
                    raw_event=event,
                    affected_meetings=affected_meetings,
                )
                changed += 1
            continue

        start = event.get("start", {})
        end = event.get("end", {})
        start_dt = start.get("dateTime")
        end_dt = end.get("dateTime")

        # Skip all-day events
        if not start_dt or not end_dt:
            continue

        meeting_url = _extract_google_meeting_url(event)
        attendees = _extract_google_attendees(event)

        await _upsert_event(
            db,
            connection_id=connection.id,
            provider_event_id=provider_event_id,
            title=event.get("summary", "Untitled"),
            description=event.get("description"),
            start_time=start_dt,
            end_time=end_dt,
            meeting_url=meeting_url,
            attendees=attendees,
            ics_sequence=ics_sequence,
            organizer_email=organizer_email,
            event_status=event_status,
            raw_event=event,
            affected_meetings=affected_meetings,
        )
        changed += 1

    # Save new sync token
    new_sync_token = data.get("nextSyncToken")
    if new_sync_token:
        connection.sync_token = new_sync_token

    connection.last_synced_at = datetime.now(timezone.utc)
    await db.commit()

    # Cascade /status cache for any meetings whose status got flipped to
    # "failed" by a reschedule/cancellation during this sync pass.
    if affected_meetings:
        from app.services import meeting_status_cache
        for mid in affected_meetings:
            await meeting_status_cache.cascade_from_db(
                db, settings.REDIS_URL, mid
            )

    return changed


async def register_google_watch_channel(
    connection: CalendarConnection,
    webhook_url: str,
) -> None:
    """Register a Google Calendar push notification channel."""
    token_data = _decrypt_google_tokens(connection.encrypted_tokens)
    access_token, _ = _refresh_google_token_if_needed(token_data)

    channel_id = str(uuid_mod.uuid4())

    resp = httpx.post(
        GOOGLE_WATCH_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
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

    # Google returns expiration as Unix milliseconds
    expiration_ms = data.get("expiration")
    if expiration_ms:
        connection.watch_expires_at = datetime.fromtimestamp(
            int(expiration_ms) / 1000, tz=timezone.utc
        )

    logger.info(f"Registered Google watch channel {channel_id} for connection {connection.id}")


async def stop_google_watch_channel(connection: CalendarConnection) -> None:
    """Stop an existing Google Calendar push notification channel."""
    if not connection.watch_channel_id or not connection.watch_resource_id:
        return

    token_data = _decrypt_google_tokens(connection.encrypted_tokens)
    access_token, _ = _refresh_google_token_if_needed(token_data)

    try:
        resp = httpx.post(
            GOOGLE_STOP_CHANNEL_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "id": connection.watch_channel_id,
                "resourceId": connection.watch_resource_id,
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to stop Google watch channel {connection.watch_channel_id}: {e}")

    connection.watch_channel_id = None
    connection.watch_resource_id = None
    connection.watch_expires_at = None


# ─── Microsoft Graph ─────────────────────────────────────────────────


def _decrypt_outlook_tokens(encrypted_tokens: str) -> dict:
    """Decrypt Outlook OAuth tokens."""
    return _decrypt_google_tokens(encrypted_tokens)  # Same Fernet encryption


def _refresh_outlook_token_if_needed(token_data: dict) -> tuple[str, bool]:
    """Refresh Microsoft access token if expired. Returns (access_token, was_refreshed)."""
    import time

    if token_data.get("expires_at", 0) > time.time() + 60:
        return token_data["access_token"], False

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("No refresh token available")

    resp = httpx.post(
        MS_TOKEN_URL,
        data={
            "client_id": settings.OUTLOOK_CLIENT_ID,
            "client_secret": settings.OUTLOOK_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "Calendars.Read User.Read offline_access",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    new_tokens = resp.json()

    token_data["access_token"] = new_tokens["access_token"]
    token_data["expires_at"] = int(time.time()) + new_tokens.get("expires_in", 3600)
    if new_tokens.get("refresh_token"):
        token_data["refresh_token"] = new_tokens["refresh_token"]

    return token_data["access_token"], True


async def outlook_incremental_sync(
    db: AsyncSession,
    connection: CalendarConnection,
) -> int:
    """Perform incremental sync using Microsoft Graph delta. Returns count of events changed."""
    token_data = _decrypt_outlook_tokens(connection.encrypted_tokens)
    access_token, was_refreshed = _refresh_outlook_token_if_needed(token_data)

    if was_refreshed:
        connection.encrypted_tokens = _encrypt_tokens(token_data)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.timezone="UTC"',
    }

    if connection.sync_token:
        # sync_token stores the deltaLink for Outlook
        url = connection.sync_token
        params = {}
    else:
        # Initial sync — fetch next 7 days
        now = datetime.now(timezone.utc)
        url = GRAPH_CALENDAR_DELTA_URL
        params = {
            "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%S.0000000"),
            "endDateTime": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.0000000"),
        }

    resp = httpx.get(url, params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("value", [])
    changed = 0

    for event in events:
        # Deleted events
        if event.get("@removed"):
            existing = await db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.connection_id == connection.id,
                    CalendarEvent.provider_event_id == event["id"],
                )
            )
            cal_event = existing.scalar_one_or_none()
            if cal_event:
                await db.delete(cal_event)
                changed += 1
            continue

        if event.get("isAllDay"):
            continue

        start = event.get("start", {})
        end = event.get("end", {})
        start_time = start.get("dateTime")
        end_time = end.get("dateTime")
        if not start_time or not end_time:
            continue

        # Ensure UTC timezone suffix
        if not start_time.endswith("Z") and "+" not in start_time:
            start_time = start_time.rstrip("0").rstrip(".") + "Z"
        if not end_time.endswith("Z") and "+" not in end_time:
            end_time = end_time.rstrip("0").rstrip(".") + "Z"

        meeting_url = _extract_outlook_meeting_url(event)
        attendees = _extract_outlook_attendees(event)

        await _upsert_event(
            db,
            connection_id=connection.id,
            provider_event_id=event["id"],
            title=event.get("subject", "Untitled"),
            description=(event.get("body") or {}).get("content"),
            start_time=start_time,
            end_time=end_time,
            meeting_url=meeting_url,
            attendees=attendees,
        )
        changed += 1

    # Save deltaLink for next incremental sync
    delta_link = data.get("@odata.deltaLink")
    if delta_link:
        connection.sync_token = delta_link

    connection.last_synced_at = datetime.now(timezone.utc)
    await db.commit()

    return changed


async def register_outlook_subscription(
    connection: CalendarConnection,
    webhook_url: str,
) -> None:
    """Register a Microsoft Graph subscription for calendar changes."""
    token_data = _decrypt_outlook_tokens(connection.encrypted_tokens)
    access_token, _ = _refresh_outlook_token_if_needed(token_data)

    expiration = datetime.now(timezone.utc) + timedelta(days=3)

    resp = httpx.post(
        GRAPH_SUBSCRIPTIONS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "changeType": "created,updated,deleted",
            "notificationUrl": webhook_url,
            "resource": "me/events",
            "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
            "clientState": settings.TEAMS_CLOUD_CLIENT_STATE_SECRET,
        },
    )
    resp.raise_for_status()
    data = resp.json()

    connection.subscription_id = data["id"]
    connection.watch_expires_at = datetime.fromisoformat(
        data["expirationDateTime"].replace("Z", "+00:00")
    )

    logger.info(f"Registered Outlook subscription {data['id']} for connection {connection.id}")


async def renew_outlook_subscription(connection: CalendarConnection) -> None:
    """Renew a Microsoft Graph subscription."""
    if not connection.subscription_id:
        return

    token_data = _decrypt_outlook_tokens(connection.encrypted_tokens)
    access_token, _ = _refresh_outlook_token_if_needed(token_data)

    expiration = datetime.now(timezone.utc) + timedelta(days=3)

    try:
        resp = httpx.patch(
            f"{GRAPH_SUBSCRIPTIONS_URL}/{connection.subscription_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        connection.watch_expires_at = datetime.fromisoformat(
            data["expirationDateTime"].replace("Z", "+00:00")
        )
    except Exception as e:
        logger.error(f"Failed to renew Outlook subscription {connection.subscription_id}: {e}")
        raise


# ─── Shared: channel/subscription renewal ────────────────────────────


# ── Channel-renewal failure classification (migration 042) ──────────
#
# Mirrors the OAuth-refresh classifier in calendar_token_refresher.py.
# Channel renewal can fail for two distinct reasons that need different
# treatment:
#   * Permanent — the OAuth grant has been revoked, the channel state
#     on Google's side is corrupted, the user lost access org-side.
#     No amount of retrying will help. Mark token_invalid=true so the
#     selection query drops it from future cron passes AND the UI's
#     existing calendar-health banner picks up the signal.
#   * Transient — network blip, 5xx, 429. Retry next 6-hour tick.
#     Capped at MAX_RENEWAL_RETRIES so a chronically broken connection
#     eventually falls into the permanent bucket instead of retrying
#     for years.
_PERMANENT_GOOGLE_CHANNEL = frozenset({
    "invalid_grant",
    "unauthorized_client",
    "invalid_client",
    "channelIdNotUnique",
    "watchAlreadyExists",
    "forbidden",
})
_PERMANENT_OUTLOOK_CHANNEL = frozenset({
    "invalid_grant",
    "unauthorized_client",
    "AADSTS70008",
    "AADSTS50105",
})

MAX_RENEWAL_RETRIES = 5


class _RenewalPermanent(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _RenewalTransient(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _classify_renewal_error(provider: str, err: Exception) -> Exception:
    """Map a raised exception from a channel-renewal attempt to either
    _RenewalPermanent or _RenewalTransient. Pure function — no DB or
    network. Caller decides what to do with the outcome.
    """
    if isinstance(err, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return _RenewalTransient(f"network: {type(err).__name__}")

    if isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        if status >= 500 or status == 429:
            return _RenewalTransient(f"http_{status}")
        if status in (400, 401, 403):
            try:
                body = err.response.json()
            except Exception:
                body = {}
            # Microsoft Graph nests the code under error.code. Check
            # that shape FIRST so the later top-level extraction doesn't
            # pull a dict value into the set-membership test (which
            # would crash with "unhashable type: dict").
            err_code = ""
            raw_error = body.get("error")
            if isinstance(raw_error, dict):
                err_code = raw_error.get("code", "")
            elif isinstance(raw_error, str):
                err_code = raw_error
            if not err_code:
                err_code = body.get("error_code") or ""
            permanent_set = (
                _PERMANENT_OUTLOOK_CHANNEL if provider == "outlook"
                else _PERMANENT_GOOGLE_CHANNEL
            )
            if err_code in permanent_set:
                return _RenewalPermanent(err_code)
            return _RenewalTransient(f"http_{status}: {err_code or 'unknown'}")

    # ValueError("No refresh token") shows up if the OAuth side already
    # gave up — treat as permanent so we don't spam Google with calls
    # we know will fail.
    if isinstance(err, ValueError):
        msg = str(err).lower()
        if "refresh token" in msg or "refresh_token" in msg:
            return _RenewalPermanent("no_refresh_token")

    return _RenewalTransient(f"unknown: {type(err).__name__}")


async def renew_expiring_channels(db: AsyncSession) -> int:
    """Renew Google watch channels and Microsoft subscriptions expiring within 1 day.

    Classifier-driven failure handling (migration 042): a permanent
    failure flips token_invalid=true so the connection drops out of
    future cron passes (and the UI surfaces a Reconnect banner). A
    transient failure increments renewal_failure_count and retries on
    the next tick — capped at MAX_RENEWAL_RETRIES, after which the
    connection is parked the same way as a permanent failure.

    Token-already-invalid connections are excluded from the selection
    so the cron doesn't keep paying the ~4 s HTTP-hold-while-failing
    cost on connections we already know are dead.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=1)

    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.auto_record == True,  # noqa: E712
            CalendarConnection.watch_expires_at.isnot(None),
            CalendarConnection.watch_expires_at <= cutoff,
            CalendarConnection.token_invalid == False,  # noqa: E712
        )
    )
    connections = result.scalars().all()

    if not connections:
        return 0

    renewed = 0
    for conn in connections:
        try:
            if conn.provider == "google":
                google_webhook_url = f"{settings.FRONTEND_URL.replace('localhost:3000', 'localhost:8005')}/api/webhooks/google-calendar"
                if not settings.is_dev:
                    google_webhook_url = f"{settings.BOT_CALLBACK_URL.rsplit('/api/', 1)[0]}/api/webhooks/google-calendar"
                await stop_google_watch_channel(conn)
                await register_google_watch_channel(conn, google_webhook_url)
                # Phase 2 Track A — catch-up sync on watch renewal.
                # Any events created or updated during a gap (watch
                # expired, integration_service downtime) get backfilled
                # here so we don't silently miss invites.
                try:
                    backfilled = await google_incremental_sync(db, conn)
                    if backfilled:
                        logger.info(
                            "Catch-up sync after watch renewal pulled %s event(s) "
                            "for connection %s",
                            backfilled,
                            conn.id,
                        )
                except Exception as e:
                    logger.warning(
                        "Catch-up sync failed for connection %s (renewal still ok): %s",
                        conn.id,
                        e,
                    )
                renewed += 1
            elif conn.provider == "outlook":
                await renew_outlook_subscription(conn)
                try:
                    backfilled = await outlook_incremental_sync(db, conn)
                    if backfilled:
                        logger.info(
                            "Catch-up sync after subscription renewal pulled %s event(s) "
                            "for connection %s",
                            backfilled,
                            conn.id,
                        )
                except Exception as e:
                    logger.warning(
                        "Catch-up sync failed for connection %s (renewal still ok): %s",
                        conn.id,
                        e,
                    )
                renewed += 1
            # Success — reset failure-counter / clear last error for
            # this connection so a future transient failure starts
            # fresh.
            conn.renewal_failure_count = 0
            conn.last_renewal_error = None
        except Exception as raw_err:
            classified = _classify_renewal_error(conn.provider, raw_err)
            if isinstance(classified, _RenewalPermanent):
                conn.token_invalid = True
                conn.token_invalid_at = now
                conn.last_renewal_error = f"permanent: {classified.reason}"
                logger.warning(
                    "Calendar channel renewal permanently failed for %s "
                    "(%s, %s) — marking token_invalid; user must reconnect",
                    conn.id,
                    conn.provider,
                    classified.reason,
                )
            else:
                conn.renewal_failure_count = (conn.renewal_failure_count or 0) + 1
                if conn.renewal_failure_count >= MAX_RENEWAL_RETRIES:
                    # Transient cap reached — converge with the permanent
                    # path so we stop trying. After ~5 6-hour cycles of
                    # the same failure, it's no longer transient.
                    conn.token_invalid = True
                    conn.token_invalid_at = now
                    conn.last_renewal_error = (
                        f"transient_exhausted_after_{conn.renewal_failure_count}: "
                        f"{classified.reason}"
                    )
                    logger.warning(
                        "Calendar channel renewal transient cap reached for %s "
                        "(%s) — parking as invalid: %s",
                        conn.id,
                        conn.provider,
                        classified.reason,
                    )
                else:
                    conn.last_renewal_error = f"transient: {classified.reason}"
                    logger.info(
                        "Calendar channel renewal transient failure for %s "
                        "(%s) attempt %d/%d: %s",
                        conn.id,
                        conn.provider,
                        conn.renewal_failure_count,
                        MAX_RENEWAL_RETRIES,
                        classified.reason,
                    )

    await db.commit()
    return renewed


# ─── Shared: incremental sync dispatcher ─────────────────────────────


async def incremental_sync(db: AsyncSession, connection: CalendarConnection) -> int:
    """Run incremental sync for a connection (auto-detects provider).

    Phase 2 Track B (notetaker-email plan §4.5 layer 3): after a
    successful sync, the connection's tokens have just been refreshed.
    Take this opportunity to also check the watch_expires_at horizon —
    if it's within 1 day of expiry, kick off an async watch renewal so
    we don't drift toward a missed-event window.
    """
    if connection.provider == "google":
        changed = await google_incremental_sync(db, connection)
    elif connection.provider == "outlook":
        changed = await outlook_incremental_sync(db, connection)
    else:
        logger.warning(f"Unknown provider {connection.provider} for connection {connection.id}")
        return 0

    # Piggyback on the successful API call — see §4.5 layer 3.
    try:
        from app.services.calendar_health import maybe_refresh_watch_on_token_use
        await maybe_refresh_watch_on_token_use(db, connection)
    except Exception as e:
        logger.debug("Watch-refresh check after sync skipped: %s", e)

    return changed


# ─── Helpers ─────────────────────────────────────────────────────────


async def _upsert_event(
    db: AsyncSession,
    connection_id,
    provider_event_id: str,
    title: str,
    description: Optional[str],
    start_time: str,
    end_time: str,
    meeting_url: Optional[str],
    attendees: Optional[list],
    *,
    # Phase 2 additions (notetaker-email plan §4.6 + §4.8). All optional
    # so older call sites continue to work without modification.
    ics_sequence: Optional[int] = None,
    organizer_email: Optional[str] = None,
    event_status: str = "confirmed",  # "confirmed" | "cancelled"
    raw_event: Optional[dict] = None,
    affected_meetings: Optional[list] = None,
) -> None:
    """Insert or update a calendar event, applying Phase 2 ownership
    resolution and (for existing rows) the reschedule/cancel reset logic.

    Optional `affected_meetings` out-parameter is forwarded to
    apply_dispatch_reset_if_needed; meeting_ids whose status got flipped
    during this upsert are appended so the caller can cascade the
    /status cache after committing.
    """
    from app.services.bot_calendar_handler import (
        apply_dispatch_reset_if_needed,
        is_bot_calendar_connection,
        record_unmatched_invite,
        resolve_event_ownership,
    )

    start_dt = _parse_dt(start_time)
    end_dt = _parse_dt(end_time)

    # Fetch the connection so we know whether this is bot_calendar.
    conn_result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.id == connection_id)
    )
    connection = conn_result.scalar_one_or_none()
    if not connection:
        logger.error(
            "_upsert_event: no calendar_connection for id=%s — skipping event",
            connection_id,
        )
        return

    # Resolve ownership (works for both user-OAuth and bot_calendar).
    resolved_team_id, resolved_user_id, bot_invited = await resolve_event_ownership(
        db, connection, organizer_email, attendees
    )

    # If this is a bot_calendar invite from a non-Memoir organizer, don't
    # create a calendar_events row — log to bot_calendar_unmatched and
    # bail (the autoresponder cron picks it up later).
    if is_bot_calendar_connection(connection) and resolved_team_id is None:
        await record_unmatched_invite(
            db,
            organizer_email=organizer_email or "<unknown>",
            ics_uid=provider_event_id,
            title=title,
            start_time=start_dt,
            meeting_url=meeting_url,
            reason="no_memoir_user",
            raw_event=raw_event,
        )
        return

    existing = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.connection_id == connection_id,
            CalendarEvent.provider_event_id == provider_event_id,
        )
    )
    cal_event = existing.scalar_one_or_none()

    if cal_event:
        # If the inbound payload is a reschedule or cancellation,
        # apply the dispatch-reset path BEFORE updating the row's
        # fields — the reset reads cal_event's current start_time and
        # bot_scheduled state to make its decision.
        async def _stop_bot(job_id: str):
            from videoclip_common.clients import OrchestratorClient
            await OrchestratorClient().stop_bot(job_id)

        await apply_dispatch_reset_if_needed(
            db,
            cal_event=cal_event,
            new_sequence=ics_sequence,
            new_start_time=start_dt,
            new_status=event_status,
            orchestrator_stop_bot=_stop_bot,
            affected_meetings=affected_meetings,
        )

        cal_event.title = title
        cal_event.description = description
        cal_event.start_time = start_dt
        cal_event.end_time = end_dt
        cal_event.meeting_url = meeting_url
        cal_event.attendees = attendees
        cal_event.resolved_team_id = resolved_team_id
        cal_event.resolved_user_id = resolved_user_id
        cal_event.bot_invited = bot_invited
        if ics_sequence is not None:
            cal_event.ics_sequence = ics_sequence
        cal_event.updated_at = datetime.now(timezone.utc)
    else:
        cal_event = CalendarEvent(
            connection_id=connection_id,
            provider_event_id=provider_event_id,
            title=title,
            description=description,
            start_time=start_dt,
            end_time=end_dt,
            meeting_url=meeting_url,
            attendees=attendees,
            resolved_team_id=resolved_team_id,
            resolved_user_id=resolved_user_id,
            bot_invited=bot_invited,
            ics_sequence=ics_sequence,
            cancelled_at=datetime.now(timezone.utc) if event_status == "cancelled" else None,
        )
        db.add(cal_event)


def _parse_dt(val) -> datetime:
    """Parse ISO datetime string to timezone-aware datetime."""
    if isinstance(val, str):
        val = val.replace("Z", "+00:00")
        return datetime.fromisoformat(val)
    return val


def _extract_google_meeting_url(event: dict) -> Optional[str]:
    """Extract meeting URL from a Google Calendar event."""
    # Check conferenceData entry points
    conference_data = event.get("conferenceData", {})
    for entry_point in conference_data.get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            return entry_point.get("uri")

    # Fallback: regex in description and location
    for field in [event.get("description", ""), event.get("location", "")]:
        if not field:
            continue
        for pattern in MEETING_URL_PATTERNS:
            match = pattern.search(field)
            if match:
                return match.group(0)

    return None


def _extract_google_attendees(event: dict) -> Optional[list]:
    """Extract attendees from a Google Calendar event."""
    raw = event.get("attendees")
    if not raw:
        return None
    attendees = []
    for a in raw:
        attendees.append({
            "email": a.get("email"),
            "name": a.get("displayName"),
            "response": a.get("responseStatus"),
            "organizer": a.get("organizer", False),
            "self": a.get("self", False),
        })
    return attendees if attendees else None


def _extract_outlook_meeting_url(event: dict) -> Optional[str]:
    """Extract meeting URL from an Outlook calendar event."""
    online_meeting = event.get("onlineMeeting")
    if online_meeting and online_meeting.get("joinUrl"):
        return online_meeting["joinUrl"]

    body_content = (event.get("body") or {}).get("content", "")
    location = (event.get("location") or {}).get("displayName", "")

    for field in [body_content, location]:
        if not field:
            continue
        for pattern in MEETING_URL_PATTERNS:
            match = pattern.search(field)
            if match:
                return match.group(0)

    return None


def _extract_outlook_attendees(event: dict) -> Optional[list]:
    """Extract attendees from an Outlook calendar event."""
    raw = event.get("attendees")
    if not raw:
        return None
    organizer_email = (
        (event.get("organizer", {}).get("emailAddress", {}).get("address") or "").lower()
    )
    attendees = []
    for a in raw:
        email_addr = a.get("emailAddress", {})
        email = email_addr.get("address")
        attendees.append({
            "email": email,
            "name": email_addr.get("name"),
            "response": (a.get("status", {}).get("response") or "").replace("Response", "").lower(),
            "organizer": email and email.lower() == organizer_email,
            "self": False,
        })
    return attendees if attendees else None
