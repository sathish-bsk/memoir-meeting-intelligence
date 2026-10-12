"""Phase 2 bot_calendar logic — keeps the bot-specific branches out of
the main calendar_sync flow.

Responsibilities:
  * Resolve `(resolved_team_id, resolved_user_id, bot_invited)` for any
    calendar_events upsert — works for both user-OAuth and bot_calendar
    connections.
  * Record unmatched invites (organizer not a Memoir user) into
    `bot_calendar_unmatched` instead of `calendar_events`.
  * Detect ICS reschedules + cancellations and run the dispatch-reset
    path (stop_bot + mark meetings.status=failed + reset bot_scheduled).
  * Send RSVP-Accepted via the bot's service account when a new
    bot_calendar event is inserted.

The reset path here uses ONLY existing orchestrator primitives
(stop_bot, no new statuses) per plan §4.8.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection, CalendarEvent, BotCalendarUnmatched
from app.models.meeting import Meeting
from app.models.team import TeamMember

logger = logging.getLogger(__name__)

# Mirror the frontend's NEXT_PUBLIC_MEMOIR_BOT_INVITE_EMAIL — the address
# users invite to schedule a bot. Read once at module load.
BOT_INVITE_EMAIL = (os.getenv("MEMOIR_BOT_INVITE_EMAIL") or "hello@meetmemoir.com").lower()

# Pre-dispatch + in-flight statuses: rows in these states can still be
# safely cancelled in response to an organizer-side reschedule.
PRE_DISPATCH_STATUSES = {"pending", "scheduled"}
IN_FLIGHT_STATUSES = {"joining", "waiting_in_lobby"}

# Minimum time-shift to count as a real reschedule. Mere minute-level
# clock drift in different ICS sources shouldn't trigger the reset path.
RESCHEDULE_TIME_THRESHOLD_SECONDS = 60


# ── Bot connection lookup ──────────────────────────────────────────────


def is_bot_calendar_connection(connection: CalendarConnection) -> bool:
    """True iff this connection represents the synthetic bot calendar."""
    return connection.provider == "bot_calendar"


# ── Ownership resolution ──────────────────────────────────────────────


async def resolve_event_ownership(
    db: AsyncSession,
    connection: CalendarConnection,
    organizer_email: Optional[str],
    attendees: Optional[list],
) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID], bool]:
    """Return (resolved_team_id, resolved_user_id, bot_invited) for the
    upsert. Resolution rules:

    - User-OAuth connection (`google` / `outlook`): inherit from the
      connection's owner. bot_invited = bot email appears in attendees.
    - bot_calendar connection: derive from the organizer's email
      (lookup users.email → primary team_member). bot_invited = True
      always (it's the bot's own calendar — the event landed here
      because the bot was invited).
    - Anything that can't be resolved → (None, None, bot_invited).
    """
    bot_invited = _bot_in_attendees(attendees)

    if is_bot_calendar_connection(connection):
        # The bot's calendar — bot is obviously invited.
        bot_invited = True
        if not organizer_email:
            return (None, None, bot_invited)
        user_id, team_id = await _lookup_user_and_team(db, organizer_email)
        return (team_id, user_id, bot_invited)

    # User-OAuth connection — connection.user_id IS the owner.
    user_id = connection.user_id
    team_id = await _primary_team_for_user(db, user_id) if user_id else None
    return (team_id, user_id, bot_invited)


def _bot_in_attendees(attendees: Optional[list]) -> bool:
    """Scan an attendees JSONB-style list for the bot's invite email."""
    if not attendees:
        return False
    for a in attendees:
        if not isinstance(a, dict):
            continue
        email = (a.get("email") or "").strip().lower()
        if email == BOT_INVITE_EMAIL:
            return True
    return False


async def _lookup_user_and_team(
    db: AsyncSession, email: str
) -> tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
    """Match an organizer email to a Memoir user + their primary team.

    Memoir's user identity lives in auth_db (a separate service), not
    in videoclip_db, so we go through the internal email-check API at
    `auth_service/auth/internal/users/check-email` (already used elsewhere
    in core_api). If the auth service is unreachable, return (None, None)
    and the caller logs the unmatched invite.
    """
    from app.clients.auth_client import check_email_exists

    result = await check_email_exists(email.lower())
    if not result or not result.get("exists"):
        return (None, None)
    raw_user_id = result.get("user_id")
    if not raw_user_id:
        return (None, None)
    try:
        user_uuid = uuid.UUID(str(raw_user_id))
    except (TypeError, ValueError):
        return (None, None)
    team_id = await _primary_team_for_user(db, user_uuid)
    return (user_uuid, team_id)


async def _primary_team_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """Pick the user's earliest-joined accepted team membership."""
    result = await db.execute(
        select(TeamMember.team_id)
        .where(
            TeamMember.user_id == user_id,
            TeamMember.invite_status.in_(("accepted", None)),
        )
        .order_by(TeamMember.joined_at.asc().nullslast())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Unmatched invites ─────────────────────────────────────────────────


async def record_unmatched_invite(
    db: AsyncSession,
    *,
    organizer_email: str,
    ics_uid: str,
    title: Optional[str],
    start_time: Optional[datetime],
    meeting_url: Optional[str],
    reason: str,
    raw_event: Optional[dict] = None,
) -> None:
    """Insert into bot_calendar_unmatched (idempotent by ics_uid)."""
    existing = await db.execute(
        select(BotCalendarUnmatched).where(BotCalendarUnmatched.ics_uid == ics_uid)
    )
    if existing.scalar_one_or_none():
        return
    db.add(
        BotCalendarUnmatched(
            organizer_email=organizer_email,
            ics_uid=ics_uid,
            title=title,
            start_time=start_time,
            meeting_url=meeting_url,
            raw_event=raw_event,
            reason=reason,
        )
    )


# ── Reschedule / cancellation reset logic ─────────────────────────────


async def apply_dispatch_reset_if_needed(
    db: AsyncSession,
    *,
    cal_event: CalendarEvent,
    new_sequence: Optional[int],
    new_start_time: datetime,
    new_status: str,
    orchestrator_stop_bot,  # async callable: (job_id) -> Awaitable[None]
    affected_meetings: Optional[list] = None,
) -> bool:
    """Determine whether an inbound update is a reschedule/cancel that
    requires resetting dispatch state, and execute it if so.

    Returns True iff a reset was applied. The caller is responsible for
    actually persisting `cal_event` mutations via commit.

    The optional `affected_meetings` list is an out-parameter: if a
    linked meeting's status was flipped to "failed", its uuid is
    appended for the caller to cascade to the /status cache after
    committing. (cascade_from_db reads committed state, so it can't
    run inline before the caller's commit.)

    The three cases (plan §4.8):
      a) bot_scheduled = false → nothing to reset (caller proceeds with
         simple time-only update).
      b) bot_scheduled = true, meeting hasn't started → stop the bot,
         mark meetings.status=failed + failure_reason, reset
         bot_scheduled=false. Caller's normal upsert proceeds with the
         new start_time and the auto-scheduler dispatches afresh.
      c) Meeting is recording / completed / etc → no-op.
    """
    if not cal_event.bot_scheduled and new_status != "cancelled":
        return False  # Case (a) — no reset needed.

    # Decide whether the new payload represents a real reschedule.
    sequence_bumped = (
        new_sequence is not None
        and cal_event.ics_sequence is not None
        and new_sequence > cal_event.ics_sequence
    )
    time_shifted = (
        abs((new_start_time - cal_event.start_time).total_seconds())
        > RESCHEDULE_TIME_THRESHOLD_SECONDS
    )
    is_cancellation = new_status == "cancelled"

    if not (is_cancellation or sequence_bumped or time_shifted):
        return False  # Title-only / attendee-only update — nothing to do.

    failure_reason = "calendar_cancelled" if is_cancellation else "calendar_reschedule"

    if cal_event.linked_meeting_id:
        meeting = await db.get(Meeting, cal_event.linked_meeting_id)
        if meeting:
            if meeting.status in IN_FLIGHT_STATUSES and meeting.bot_job_id:
                try:
                    await orchestrator_stop_bot(meeting.bot_job_id)
                except Exception as e:
                    logger.warning(
                        "stop_bot(%s) failed during dispatch reset (continuing): %s",
                        meeting.bot_job_id,
                        e,
                    )
            if meeting.status in PRE_DISPATCH_STATUSES | IN_FLIGHT_STATUSES:
                meeting.status = "failed"
                meeting.failure_reason = failure_reason
                cal_event.linked_meeting_id = None
                if affected_meetings is not None:
                    affected_meetings.append(meeting.id)
            # If meeting is already recording/processing/completed/etc,
            # we don't disturb it — the prior recording stands.

    # Reset dispatch-claim flags so the auto-scheduler can fire fresh.
    cal_event.bot_scheduled = False
    cal_event.bot_retry_count = 0
    if is_cancellation:
        cal_event.cancelled_at = datetime.now(timezone.utc)
    return True


# ── Send the bot's RSVP-Accepted (skeleton — needs bot credentials) ──


async def send_bot_rsvp_accepted(
    *,
    bot_connection: CalendarConnection,
    provider_event_id: str,
) -> None:
    """Send an RSVP=accepted on the bot's behalf so the organizer sees
    "Memoir Notetaker accepted" in their calendar.

    Implementation note: this function is intentionally a thin wrapper.
    The actual Google API call (PATCH /calendars/{email}/events/{id})
    requires a delegated access token from the bot's service account
    (see integration_service/app/services/bot_calendar_auth.py). The
    HTTP call lives there to keep the credential-touching code in one
    place.

    For the v1 implementation, this is a no-op when the bot connection
    doesn't have a valid pointer (e.g., development environments that
    haven't run scripts/provision_bot_calendar.py). In production, the
    bot_calendar_auth module performs the actual PATCH.
    """
    if not is_bot_calendar_connection(bot_connection):
        return  # Defensive — caller should only invoke for bot connections.
    try:
        from app.services.bot_calendar_rsvp import patch_event_response  # type: ignore
    except ImportError:
        logger.info(
            "bot_calendar_rsvp module not present — skipping RSVP for event %s "
            "(this is expected in dev / test environments)",
            provider_event_id,
        )
        return
    try:
        await patch_event_response(bot_connection, provider_event_id, status="accepted")
    except Exception as e:
        # RSVP failure is not fatal — recording proceeds regardless.
        logger.warning(
            "Failed to send bot RSVP for event %s: %s", provider_event_id, e
        )
