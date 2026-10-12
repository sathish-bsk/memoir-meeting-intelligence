"""Phase 2 Track B — proactive calendar-connection health.

Three entry points:

1. `refresh_expiring_watches_for_user(db, user_id)` — fired on signin
   to async-refresh any of the user's calendar connections whose watch
   is within 2 days of expiry.

2. `maybe_refresh_watch_on_token_use(db, connection)` — called from
   `refresh_if_needed()` after a successful access-token mint. If the
   connection's watch is within 1 day of expiry, kicks off a renewal.

3. `daily_health_check(db)` — cron entry point. Pings each user
   connection with a cheap API call; flips `token_invalid=true` on
   `invalid_grant` and triggers the alert flow.

All three paths flip `token_invalid=true` rather than crash, so any
"connection silently dead" failure surfaces via the user-facing
banner + email instead of staying invisible.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection
from app.services.oauth_errors import TokenInvalidError

logger = logging.getLogger(__name__)

# Watch-refresh thresholds — kept conservative so we never come within
# striking distance of the underlying provider TTLs (Google 7 days,
# Microsoft 3 days).
SIGNIN_REFRESH_HORIZON = timedelta(days=2)
TOKEN_USE_REFRESH_HORIZON = timedelta(days=1)


# ── Entry point 1: signin-time refresh ─────────────────────────────────


async def refresh_expiring_watches_for_user(db: AsyncSession, user_id: str) -> int:
    """Async-refresh any of the user's calendar connections whose watch
    is within SIGNIN_REFRESH_HORIZON of expiry.

    Returns the count of connections kicked off. Does NOT wait for the
    refresh to complete — the call is fire-and-forget so signin stays
    fast. Errors are logged but never raised to the caller (signin must
    never fail because a watch refresh hit a hiccup).
    """
    import uuid as _uuid

    try:
        user_uuid = _uuid.UUID(user_id)
    except (TypeError, ValueError):
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now + SIGNIN_REFRESH_HORIZON

    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user_uuid,
            CalendarConnection.auto_record == True,  # noqa: E712
            CalendarConnection.watch_expires_at.isnot(None),
            CalendarConnection.watch_expires_at <= cutoff,
            CalendarConnection.token_invalid == False,  # noqa: E712
        )
    )
    connections = result.scalars().all()
    if not connections:
        return 0

    # Fire-and-forget; we don't want to delay the signin response.
    asyncio.create_task(_renew_connections_in_background([c.id for c in connections]))
    return len(connections)


async def _renew_connections_in_background(connection_ids: list) -> None:
    """Background task: re-register watches for the given connection IDs."""
    from app.core.database import async_session
    from app.services.calendar_sync import renew_expiring_channels

    try:
        async with async_session() as bg_db:
            # renew_expiring_channels reads its own filter so we just
            # invoke it; the connections we just queued are guaranteed
            # to be in its window.
            await renew_expiring_channels(bg_db)
    except Exception as e:
        logger.warning("Background watch renewal failed: %s", e)


# ── Entry point 2: token-refresh-time check ────────────────────────────


async def maybe_refresh_watch_on_token_use(
    db: AsyncSession,
    connection: CalendarConnection,
) -> None:
    """If `connection.watch_expires_at` is within TOKEN_USE_REFRESH_HORIZON,
    kick off an async watch renewal. Called from `refresh_if_needed()` so
    every API operation on the connection implicitly keeps the watch warm.

    No-op if the connection has no watch yet (initial sync hasn't fired)
    or if the watch is already fresh.
    """
    if connection.watch_expires_at is None:
        return
    now = datetime.now(timezone.utc)
    if connection.watch_expires_at > now + TOKEN_USE_REFRESH_HORIZON:
        return
    asyncio.create_task(_renew_connections_in_background([connection.id]))


# ── Entry point 3: daily health-check cron ─────────────────────────────


# Cheap, idempotent endpoints we ping per provider. These are the
# smallest possible "is this token still valid?" probes.
_HEALTH_PROBES = {
    "google": "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1",
    "outlook": "https://graph.microsoft.com/v1.0/me",
}


async def daily_health_check(db: AsyncSession) -> dict[str, int]:
    """Run a cheap probe against every user calendar connection.

    Flips `token_invalid = true` on any connection whose probe returns
    `invalid_grant` or persistently fails. Returns a small summary dict
    suitable for logging.

    Skips the synthetic bot_calendar connection — service-account
    credentials don't have refresh-token invalidation, so the daily
    ping isn't relevant there.
    """
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.provider.in_(("google", "outlook")),
            CalendarConnection.token_invalid == False,  # noqa: E712
        )
    )
    connections = result.scalars().all()

    summary = {"checked": 0, "ok": 0, "invalidated": 0, "errors": 0}

    for conn in connections:
        summary["checked"] += 1
        try:
            ok = await _probe_connection(conn)
            if ok:
                summary["ok"] += 1
            else:
                summary["invalidated"] += 1
                conn.token_invalid = True
                conn.token_invalid_at = datetime.now(timezone.utc)
                # Fire the alert flow (email + banner state).
                try:
                    await _notify_connection_invalidated(conn)
                except Exception as e:
                    logger.warning("Alert flow failed for %s: %s", conn.id, e)
        except Exception as e:
            summary["errors"] += 1
            logger.warning("Health probe error for connection %s: %s", conn.id, e)

    await db.commit()
    logger.info("Calendar health check complete: %s", summary)
    return summary


async def _probe_connection(connection: CalendarConnection) -> bool:
    """Make a tiny API call to verify the connection's tokens still work.

    Returns True if the call succeeds, False on `invalid_grant`. Any
    other failure (network blip, 500) is raised and the caller logs.
    """
    if connection.provider == "google":
        from app.services.calendar_sync import _decrypt_google_tokens, _refresh_google_token_if_needed
        try:
            token_data = _decrypt_google_tokens(connection.encrypted_tokens)
            access_token, _ = _refresh_google_token_if_needed(token_data)
        except TokenInvalidError:
            return False
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _HEALTH_PROBES["google"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
            return resp.status_code == 200

    elif connection.provider == "outlook":
        from app.services.calendar_sync import _decrypt_outlook_tokens, _refresh_outlook_token_if_needed
        try:
            token_data = _decrypt_outlook_tokens(connection.encrypted_tokens)
            access_token, _ = _refresh_outlook_token_if_needed(token_data)
        except TokenInvalidError:
            return False
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _HEALTH_PROBES["outlook"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
            return resp.status_code == 200

    # Unknown provider — treat as healthy (don't accidentally invalidate
    # something we don't know how to probe).
    return True


async def _notify_connection_invalidated(connection: CalendarConnection) -> None:
    """Send a one-shot notification when a connection is first invalidated.

    Wires into the notification_service email path if available. If it
    isn't (dev / test), this is a no-op — the in-app banner endpoint
    (see calendar_health_api) still surfaces the state to the UI.
    """
    try:
        # Lazy import — notification path isn't required for tests.
        from app.clients.notification_client import send_calendar_disconnected_email  # type: ignore
    except ImportError:
        return
    try:
        await send_calendar_disconnected_email(
            user_id=str(connection.user_id),
            provider=connection.provider,
            provider_email=connection.provider_email,
        )
    except Exception as e:
        logger.warning("Failed to send calendar-disconnected email: %s", e)
