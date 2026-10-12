"""
Calendar event listing and bot scheduling API routes (core_api).
Events are synced by integration_service; core_api handles listing and bot dispatch.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, cast, func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.calendar import CalendarConnection, CalendarEvent
from app.models.meeting import Meeting
from app.models.team import TeamMember
from app.schemas.calendar import (
    CalendarEventListV3Response,
    CalendarEventResponse,
    CalendarEventV3Response,
    ScheduleBotRequest,
)
from app.services import meeting_status_cache
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.clients import OrchestratorClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _attendees_as_array():
    """Return a JSONB expression that is always a JSONB array.

    Calendar attendees are stored as JSONB but can take three shapes in
    prod:
      - JSONB array (the normal case)
      - SQL NULL (column unset)
      - JSONB scalar `null` (provider returned null and sync wrote it)

    `jsonb_array_elements()` raises `cannot extract elements from a scalar`
    on the third shape, which 500s any query that touches it. Plain
    COALESCE only handles SQL NULL, not JSON null. The CASE expression
    handles both by checking `jsonb_typeof` first.
    """
    return case(
        (
            func.jsonb_typeof(CalendarEvent.attendees) == "array",
            CalendarEvent.attendees,
        ),
        else_=cast(text("'[]'"), JSONB),
    )


async def _verify_team_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: str
) -> TeamMember:
    """Verify user is a member of the team."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this team")
    return member


@router.get("/events", response_model=list[CalendarEventResponse])
async def list_calendar_events(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List calendar events for the current user (filtered to team context).

    Phase 2 (notetaker-email plan §4.7): events come from two sources:
      - the user's own connected calendars (existing path), and
      - the bot's calendar, when an event has been resolved to this team
        via the organizer-email lookup. Bot-calendar events surface as
        `source: "bot_calendar"` so the frontend can show a small badge.
    """
    await _verify_team_membership(db, team_id, user.user_id)

    # Connections owned by the current user (the existing path).
    conn_result = await db.execute(
        select(CalendarConnection.id, CalendarConnection.provider).where(
            CalendarConnection.user_id == uuid.UUID(user.user_id)
        )
    )
    user_connections = conn_result.all()
    user_connection_ids = [c.id for c in user_connections]
    provider_map = {c.id: c.provider for c in user_connections}

    # Build the union predicate: own connection OR resolved_team_id match.
    own_pred = (
        CalendarEvent.connection_id.in_(user_connection_ids)
        if user_connection_ids
        else None
    )
    team_pred = (
        CalendarEvent.resolved_team_id == team_id
        if True
        else None
    )
    where_clauses = [
        # Always exclude cancelled events from the listing surface.
        CalendarEvent.cancelled_at.is_(None),
    ]
    if own_pred is not None:
        where_clauses.append(or_(own_pred, team_pred))
    else:
        where_clauses.append(team_pred)

    # Future-only ASC by default for the legacy endpoint's home-page caller.
    # The home `Upcoming` list is meant to surface what's NEXT — not what
    # was in the past with the most-recent-DESC ordering that the previous
    # version returned. Past events used to leak through because there was
    # no time filter; they'd dominate the top-50-DESC slice on accounts
    # with no future events.
    now = datetime.now(timezone.utc)
    where_clauses.append(CalendarEvent.start_time >= now)

    result = await db.execute(
        select(CalendarEvent)
        .where(*where_clauses)
        .order_by(CalendarEvent.start_time.asc())
        .limit(50)
    )
    events = result.scalars().all()

    out: list[CalendarEventResponse] = []
    for ev in events:
        provider = provider_map.get(ev.connection_id)
        if provider is None:
            # Connection isn't user-owned — must be bot_calendar (the
            # only other way an event makes it into this team's listing
            # is through resolved_team_id). Fetch lazily.
            provider = "bot_calendar"
        base = CalendarEventResponse.model_validate(ev).model_dump()
        out.append(
            CalendarEventResponse(
                **{
                    **base,
                    "provider": "google" if provider != "bot_calendar" else provider,
                    "source": "bot_calendar" if provider == "bot_calendar" else "calendar",
                }
            )
        )
    return out


# ─── v3 list (plan/meetings-events-v3-list.md §3.2) ────────────────────


@router.get("/events/v3", response_model=CalendarEventListV3Response)
async def list_calendar_events_v3(
    team_id: uuid.UUID = Query(...),
    scope: Optional[str] = Query(None, pattern="^(hosted|participant|all)$"),
    include_past: bool = Query(False),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List calendar events for the v3 UI (plan §3.2).

    Differs from the legacy `/events` endpoint:
      - Adds `scope` (hosted | participant | all) with role gate on `all`
      - Adds `include_past` to surface events whose end_time has passed
      - Adds `start_date` / `end_date` for day/week/month-window filtering
      - Returns ASC by start_time (legacy was DESC)
      - LEFT JOINs meetings.status as `linked_meeting_status`
      - Envelope response with `current_user_email` + `current_user_role`
    """
    member = await _verify_team_membership(db, team_id, user.user_id)

    if scope == "all" and member.role not in ("owner", "admin"):
        raise HTTPException(
            403, "scope=all is restricted to team owners and admins"
        )

    now = datetime.now(timezone.utc)
    user_uuid = uuid.UUID(user.user_id)
    user_email_lower = (user.email or "").lower()

    # Always exclude cancelled events.
    where_clauses = [CalendarEvent.cancelled_at.is_(None)]

    # ── Scope branches ───────────────────────────────────────────
    if scope == "all":
        # Every event resolved to this team — across all team members.
        where_clauses.append(CalendarEvent.resolved_team_id == team_id)
    elif scope in ("hosted", "participant"):
        # Restrict to events the current user can see (their connections OR
        # team-resolved bot_calendar entries) AND apply organizer-flag filter
        # via the attendees JSONB.
        conn_result = await db.execute(
            select(CalendarConnection.id).where(
                CalendarConnection.user_id == user_uuid
            )
        )
        user_conn_ids = [c.id for c in conn_result.all()]

        visibility_preds: list = [CalendarEvent.resolved_user_id == user_uuid]
        if user_conn_ids:
            visibility_preds.append(
                CalendarEvent.connection_id.in_(user_conn_ids)
            )
        where_clauses.append(or_(*visibility_preds))

        # Test whether the current user is the organizer of this event.
        # Attendees emails are stored raw-case by both Google and Outlook
        # sync (integration_service/app/services/{google,outlook}_calendar.py:
        # `"email": a.get("email")` — no lower()), so a plain JSONB @>
        # containment would miss `User@example.com`. Use an EXISTS
        # subquery with LOWER() on both sides.
        #
        # CASE WHEN jsonb_typeof = 'array' ELSE '[]' protects against TWO
        # distinct empty-attendee shapes that have been observed in prod:
        #   (a) SQL NULL — column is unset
        #   (b) JSONB scalar `null` — provider returned null, sync wrote it
        # Plain COALESCE only catches (a); jsonb_array_elements() on the
        # scalar-null case raises "cannot extract elements from a scalar"
        # and 500s the entire query (May 25 bug: "Future Teams" event on
        # Apr 13 had attendees='null'::jsonb → scope=hosted listing 500'd
        # silently → strip dot vs day-detail disagreement).
        attendees_array = _attendees_as_array()
        attendee_elem = func.jsonb_array_elements(attendees_array).alias("a")
        organizer_match = (
            select(literal_column("1"))
            .select_from(attendee_elem)
            .where(
                func.lower(literal_column("a.value->>'email'")) == user_email_lower,
                literal_column("(a.value->>'organizer')::boolean") == True,  # noqa: E712
            )
            .exists()
        )
        # Fall back to resolved_user_id when attendees data carries no
        # organizer evidence at all (no attendees array, or array with
        # zero entries having `organizer:true`). This catches events the
        # provider returned with attendees=null or attendees=[] — the
        # user's own calendar block, typically the events they organized
        # but didn't invite anyone to. Without this, scope=hosted would
        # hide their own events whenever the provider omitted attendees
        # metadata. (May 25 "Future Teams" case: attendees=null, host
        # is the user, but no organizer flag.)
        has_organizer_flag = (
            select(literal_column("1"))
            .select_from(attendee_elem)
            .where(
                literal_column("(a.value->>'organizer')::boolean") == True,  # noqa: E712
            )
            .exists()
        )
        host_fallback = (~has_organizer_flag) & (
            CalendarEvent.resolved_user_id == user_uuid
        )
        if scope == "hosted":
            where_clauses.append(or_(organizer_match, host_fallback))
        else:  # participant
            where_clauses.append(~or_(organizer_match, host_fallback))
    else:
        # scope=None — legacy union (preserves prior /events behavior
        # for v3 callers that don't pass an explicit scope).
        conn_result = await db.execute(
            select(CalendarConnection.id).where(
                CalendarConnection.user_id == user_uuid
            )
        )
        user_conn_ids = [c.id for c in conn_result.all()]
        legacy_preds: list = [CalendarEvent.resolved_team_id == team_id]
        if user_conn_ids:
            legacy_preds.append(
                CalendarEvent.connection_id.in_(user_conn_ids)
            )
        where_clauses.append(or_(*legacy_preds))

    # ── Date-window filter ───────────────────────────────────────
    if start_date is not None:
        where_clauses.append(CalendarEvent.start_time >= start_date)
    if end_date is not None:
        # Inclusive upper bound on the displayed date — frontend passes
        # end-of-day in user's local TZ. Use <= to keep it inclusive.
        where_clauses.append(CalendarEvent.start_time <= end_date)

    # ── Past filter ──────────────────────────────────────────────
    # When the caller passes an explicit date range (calendar-strip
    # day-detail view), the range itself bounds the query — neither the
    # future-only nor the 30-day-cap layer fires. This keeps the strip
    # dot count and the day-detail list in agreement when the user
    # navigates to a past day. Bugs seen May 25:
    #   (1) 30-day cap excluded events past ~Apr 25 (fixed earlier).
    #   (2) `not include_past` future-only filter excluded past-day
    #       events from an explicit range (fixed now).
    date_range_explicit = start_date is not None or end_date is not None
    if not date_range_explicit:
        if include_past:
            where_clauses.append(
                or_(
                    CalendarEvent.start_time >= now,
                    CalendarEvent.end_time >= (now - timedelta(days=30)),
                )
            )
        else:
            where_clauses.append(CalendarEvent.start_time >= now)

    # ── LEFT JOIN meetings.status via linked_meeting_id ──────────
    query = (
        select(CalendarEvent, Meeting.status.label("linked_meeting_status"))
        .outerjoin(Meeting, Meeting.id == CalendarEvent.linked_meeting_id)
        .where(*where_clauses)
        .order_by(CalendarEvent.start_time.asc())
        .limit(200)
    )
    result = await db.execute(query)
    rows = result.all()

    # Build provider map for connections we touched (for the source
    # discriminator on the response).
    conn_ids_seen = {r[0].connection_id for r in rows if r[0].connection_id}
    provider_map: dict[uuid.UUID, str] = {}
    if conn_ids_seen:
        prov_result = await db.execute(
            select(CalendarConnection.id, CalendarConnection.provider).where(
                CalendarConnection.id.in_(conn_ids_seen)
            )
        )
        provider_map = {row.id: row.provider for row in prov_result.all()}

    events_out: list[CalendarEventV3Response] = []
    for ev, linked_status in rows:
        # provider_map yields the actual connection.provider ("google" /
        # "outlook" / etc); falls back to "bot_calendar" only when the
        # event has no user-owned connection (Phase 2 bot-calendar entries).
        provider = provider_map.get(ev.connection_id, "bot_calendar")
        base = CalendarEventV3Response.model_validate(ev).model_dump()
        events_out.append(
            CalendarEventV3Response(
                **{
                    **base,
                    "provider": provider,
                    "source": "bot_calendar" if provider == "bot_calendar" else "calendar",
                    "linked_meeting_status": linked_status,
                }
            )
        )

    return CalendarEventListV3Response(
        events=events_out,
        current_user_email=user.email or "",
        current_user_role=member.role,
    )


@router.get("/events/week-activity")
async def calendar_events_week_activity(
    team_id: uuid.UUID = Query(...),
    scope: Optional[str] = Query(None, pattern="^(hosted|participant|all)$"),
    range_start: datetime = Query(...),
    range_end: datetime = Query(...),
    tz_offset_minutes: int = Query(
        0,
        ge=-720,
        le=840,
        description=(
            "Minutes east of UTC for the user's local timezone (positive for "
            "east, e.g., IST=330). The activity-date GROUP BY runs after "
            "shifting start_time by this offset so the dots align with what "
            "the user sees on the DateStrip. Default 0 = UTC grouping."
        ),
    ),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Per-day calendar-event counts over a date range, for the v3
    DateStrip (plan §6.2). Same scope/role rules as `/events/v3`.
    """
    member = await _verify_team_membership(db, team_id, user.user_id)

    if scope == "all" and member.role not in ("owner", "admin"):
        raise HTTPException(
            403, "scope=all is restricted to team owners and admins"
        )

    user_uuid = uuid.UUID(user.user_id)
    user_email_lower = (user.email or "").lower()

    where_clauses = [
        CalendarEvent.cancelled_at.is_(None),
        CalendarEvent.start_time >= range_start,
        CalendarEvent.start_time <= range_end,
    ]

    if scope == "all":
        where_clauses.append(CalendarEvent.resolved_team_id == team_id)
    elif scope in ("hosted", "participant"):
        conn_result = await db.execute(
            select(CalendarConnection.id).where(
                CalendarConnection.user_id == user_uuid
            )
        )
        user_conn_ids = [c.id for c in conn_result.all()]
        visibility_preds: list = [CalendarEvent.resolved_user_id == user_uuid]
        if user_conn_ids:
            visibility_preds.append(
                CalendarEvent.connection_id.in_(user_conn_ids)
            )
        where_clauses.append(or_(*visibility_preds))

        # Same jsonb_typeof guard + host_fallback as list_calendar_events_v3.
        attendees_array = _attendees_as_array()
        attendee_elem = func.jsonb_array_elements(attendees_array).alias("a")
        organizer_match = (
            select(literal_column("1"))
            .select_from(attendee_elem)
            .where(
                func.lower(literal_column("a.value->>'email'"))
                == user_email_lower,
                literal_column("(a.value->>'organizer')::boolean")
                == True,  # noqa: E712
            )
            .exists()
        )
        has_organizer_flag = (
            select(literal_column("1"))
            .select_from(attendee_elem)
            .where(
                literal_column("(a.value->>'organizer')::boolean")
                == True,  # noqa: E712
            )
            .exists()
        )
        host_fallback = (~has_organizer_flag) & (
            CalendarEvent.resolved_user_id == user_uuid
        )
        if scope == "hosted":
            where_clauses.append(or_(organizer_match, host_fallback))
        else:
            where_clauses.append(~or_(organizer_match, host_fallback))
    else:
        conn_result = await db.execute(
            select(CalendarConnection.id).where(
                CalendarConnection.user_id == user_uuid
            )
        )
        user_conn_ids = [c.id for c in conn_result.all()]
        legacy_preds: list = [CalendarEvent.resolved_team_id == team_id]
        if user_conn_ids:
            legacy_preds.append(
                CalendarEvent.connection_id.in_(user_conn_ids)
            )
        where_clauses.append(or_(*legacy_preds))

    # Apply the user's TZ offset before extracting the date so the dot
    # density aligns with what the DateStrip renders. Postgres
    # `make_interval(mins => N)` is integer-parametric — safe vs. injection
    # because the bindparam path validates the int upstream.
    shifted_ts = CalendarEvent.start_time + func.make_interval(
        0, 0, 0, 0, 0, tz_offset_minutes, 0
    )
    activity_date = func.date(shifted_ts).label("activity_date")
    result = await db.execute(
        select(activity_date, func.count().label("c"))
        .where(*where_clauses)
        .group_by(activity_date)
    )
    return {row.activity_date.isoformat(): row.c for row in result.all()}


@router.post("/events/{event_id}/schedule-bot")
async def schedule_bot(
    event_id: uuid.UUID,
    body: ScheduleBotRequest = ScheduleBotRequest(),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Schedule a recording bot for a calendar event."""
    # Get the calendar event
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.id == event_id)
    )
    cal_event = result.scalar_one_or_none()
    if not cal_event:
        raise HTTPException(404, "Calendar event not found")

    # Get connection to verify ownership
    conn_result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.id == cal_event.connection_id)
    )
    connection = conn_result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "Calendar connection not found")

    # Phase 2: accept ownership via either connection.user_id (existing
    # path for user-OAuth events) OR resolved_team_id matching the
    # caller's active team (new path for bot_calendar events). Either
    # is sufficient — neither requires the other.
    is_connection_owner = str(connection.user_id) == user.user_id
    is_team_owner = (
        cal_event.resolved_team_id is not None
        and user.team_id is not None
        and str(cal_event.resolved_team_id) == user.team_id
    )
    if not (is_connection_owner or is_team_owner):
        raise HTTPException(403, "This calendar event belongs to a different team or user")

    # Get user's team for meeting creation
    team_id = uuid.UUID(user.team_id) if user.team_id else None
    if not team_id:
        raise HTTPException(400, "No active team")

    await _verify_team_membership(db, team_id, user.user_id)

    if not cal_event.meeting_url:
        raise HTTPException(400, "This event has no meeting URL")

    if cal_event.linked_meeting_id:
        raise HTTPException(409, "Bot already scheduled for this event")

    # Check plan limit
    from app.core.plan_limits import check_plan_limit

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(func.count())
        .select_from(Meeting)
        .where(
            Meeting.team_id == team_id,
            Meeting.created_at >= month_start,
        )
    )
    current_month_calls = count_result.scalar() or 0
    await check_plan_limit("calls_per_month", str(team_id), current_month_calls)

    # Create meeting + link calendar event in ONE transaction, then
    # release the connection back to the pool before the orchestrator
    # HTTP call. Previously this held the open transaction through the
    # orchestrator round-trip (typically 1-2 s, occasionally 5+ s under
    # bot launch delays), pinning a pool connection for the whole
    # duration. With the meeting row already committed as "pending", a
    # second short transaction afterward updates bot_job_id (or flips
    # to "failed") atomically.
    title = body.title or cal_event.title
    meeting = Meeting(
        team_id=team_id,
        created_by=uuid.UUID(user.user_id),
        title=title,
        meeting_url=cal_event.meeting_url,
        source="calendar",
        calendar_event_id=cal_event.provider_event_id,
        status="pending",
    )
    db.add(meeting)
    cal_event.linked_meeting_id = meeting.id
    await db.commit()
    await db.refresh(meeting)
    meeting_id = meeting.id
    meeting_url = cal_event.meeting_url

    # Orchestrator call with NO DB transaction open.
    bot_job_id: str | None = None
    final_status = "pending"
    try:
        orchestrator = OrchestratorClient(settings.ORCHESTRATOR_URL)
        result = await orchestrator.create_bot(
            meeting_url=meeting_url,
            callback_url=settings.BOT_CALLBACK_URL,
            meeting_id=str(meeting_id),
        )
        bot_job_id = result.get("jobId") or result.get("job_id")
    except Exception as e:
        logger.error(f"Failed to create bot for calendar event {event_id}: {e}")
        final_status = "failed"

    # Short DB write to record the outcome.
    if bot_job_id:
        await db.execute(
            text("UPDATE meetings SET bot_job_id = :j WHERE id = :id"),
            {"j": bot_job_id, "id": str(meeting_id)},
        )
    if final_status == "failed":
        await db.execute(
            text("UPDATE meetings SET status = 'failed' WHERE id = :id"),
            {"id": str(meeting_id)},
        )
    await db.commit()

    # Cascade — meeting was just created OR transitioned to failed.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    return {
        "meeting_id": str(meeting_id),
        "status": final_status,
        "event_id": str(cal_event.id),
    }


@router.delete("/events/{event_id}/cancel-bot")
async def cancel_bot(
    event_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a scheduled bot for a calendar event."""
    # Get the calendar event
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.id == event_id)
    )
    cal_event = result.scalar_one_or_none()
    if not cal_event:
        raise HTTPException(404, "Calendar event not found")

    if not cal_event.linked_meeting_id:
        raise HTTPException(400, "No bot scheduled for this event")

    # Get connection to verify ownership
    conn_result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.id == cal_event.connection_id)
    )
    connection = conn_result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "Calendar connection not found")

    # Phase 2: accept ownership via either connection.user_id (existing
    # path for user-OAuth events) OR resolved_team_id matching the
    # caller's active team (new path for bot_calendar events). Either
    # is sufficient — neither requires the other.
    is_connection_owner = str(connection.user_id) == user.user_id
    is_team_owner = (
        cal_event.resolved_team_id is not None
        and user.team_id is not None
        and str(cal_event.resolved_team_id) == user.team_id
    )
    if not (is_connection_owner or is_team_owner):
        raise HTTPException(403, "This calendar event belongs to a different team or user")

    # Get the meeting
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == cal_event.linked_meeting_id)
    )
    meeting = meeting_result.scalar_one_or_none()

    if meeting:
        # Try to stop the bot if it's active
        if meeting.bot_job_id and meeting.status in ("pending", "joining", "recording"):
            try:
                orchestrator = OrchestratorClient(settings.ORCHESTRATOR_URL)
                await orchestrator.stop_bot(meeting.bot_job_id)
            except Exception as e:
                logger.warning(f"Failed to stop bot for meeting {meeting.id}: {e}")

        await db.delete(meeting)

    # Unlink the calendar event
    cal_event.linked_meeting_id = None
    await db.commit()

    return {"message": "Bot cancelled", "event_id": str(event_id)}


# ─── Auto-record settings ─────────────────────────────────────────────


@router.get("/auto-record")
async def get_auto_record(
    connection_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get auto-record setting for a calendar connection."""
    result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "Calendar connection not found")

    if str(connection.user_id) != user.user_id:
        raise HTTPException(403, "This calendar connection belongs to a different user")

    return {
        "connection_id": str(connection.id),
        "auto_record": connection.auto_record,
        "provider": connection.provider,
    }


@router.put("/auto-record")
async def update_auto_record(
    connection_id: uuid.UUID = Query(...),
    enabled: bool = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle auto-record for a calendar connection."""
    result = await db.execute(
        select(CalendarConnection).where(CalendarConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(404, "Calendar connection not found")

    if str(connection.user_id) != user.user_id:
        raise HTTPException(403, "This calendar connection belongs to a different user")

    connection.auto_record = enabled
    connection.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "connection_id": str(connection.id),
        "auto_record": connection.auto_record,
        "provider": connection.provider,
    }
