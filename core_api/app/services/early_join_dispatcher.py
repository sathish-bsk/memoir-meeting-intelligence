"""
Shared bot dispatch service — used by both webhook handlers (early join) and
the calendar auto-scheduler to dispatch a bot for a calendar event.

Uses row-level locking (SELECT ... FOR UPDATE) on the calendar event to prevent
duplicate dispatch when webhooks and auto-scheduler race.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.calendar import CalendarConnection, CalendarEvent
from app.models.meeting import Meeting
from app.models.team import TeamMember
from videoclip_common.clients import OrchestratorClient

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


async def dispatch_bot_for_calendar_event(
    db: AsyncSession,
    cal_event: CalendarEvent,
    connection: CalendarConnection,
    trigger: str = "auto_scheduler",
) -> bool:
    """Dispatch a bot for a calendar event with row-level locking for dedup.

    Returns True if bot was successfully dispatched or event was deduped.
    """
    # Step 1: Acquire row-level lock and check bot_scheduled atomically
    lock_result = await db.execute(
        select(CalendarEvent)
        .where(CalendarEvent.id == cal_event.id)
        .with_for_update()
    )
    locked_event = lock_result.scalar_one_or_none()
    if not locked_event:
        return False

    if locked_event.bot_scheduled:
        logger.info(f"Event {cal_event.id} already scheduled (trigger={trigger}), skipping")
        return False

    # Step 2: Set bot_scheduled immediately to claim the event
    locked_event.bot_scheduled = True
    await db.flush()

    try:
        # Step 3: Resolve team ownership.
        # Phase 2 (notetaker-email plan §4.7): bot_calendar events
        # don't belong to a user — their team is in resolved_team_id.
        # For everything else, fall back to the connection's owner.
        team_id = None
        owner_user_id = None
        if locked_event.resolved_team_id is not None:
            team_id = locked_event.resolved_team_id
            owner_user_id = locked_event.resolved_user_id or connection.user_id
        else:
            team_member = await _get_user_team(db, connection.user_id)
            if team_member:
                team_id = team_member.team_id
                owner_user_id = connection.user_id

        if not team_id:
            logger.warning(
                f"No team resolvable for event {cal_event.id} "
                f"(connection={connection.id}, provider={connection.provider}), skipping"
            )
            locked_event.bot_scheduled = False
            await db.commit()
            return False

        # Step 4: Dedup — check if another bot is already scheduled for this meeting URL + time
        existing_meeting = await _find_existing_meeting(
            db, team_id, locked_event.meeting_url, locked_event.start_time
        )
        if existing_meeting:
            locked_event.linked_meeting_id = existing_meeting.id
            await db.commit()
            logger.info(
                f"Event {cal_event.id} deduped to existing meeting {existing_meeting.id} "
                f"(trigger={trigger})"
            )
            return True

        # Step 5: Check plan limit (fail open — don't block on auth service errors)
        try:
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

            from app.clients.auth_client import get_team_plan

            plan_info = await get_team_plan(str(team_id))
            if plan_info:
                limits = plan_info.get("limits", {})
                limit_value = limits.get("calls_per_month")
                if limit_value is not None and limit_value != -1 and current_month_calls >= limit_value:
                    logger.warning(
                        f"Team {team_id} hit plan limit ({current_month_calls}/{limit_value}), "
                        f"skipping dispatch for event {cal_event.id} (trigger={trigger})"
                    )
                    locked_event.bot_scheduled = False
                    await db.commit()
                    return False
        except Exception as e:
            logger.warning(f"Plan limit check failed, proceeding anyway: {e}")

        # Step 6: Create Meeting record. created_by reflects the
        # actual user who owns the event (Phase 2: may be the
        # resolved_user_id from a bot_calendar invite, not the
        # connection.user_id which is the synthetic system bot).
        meeting = Meeting(
            team_id=team_id,
            created_by=owner_user_id or connection.user_id,
            title=locked_event.title,
            meeting_url=locked_event.meeting_url,
            source="calendar",
            calendar_event_id=locked_event.provider_event_id,
            scheduled_start_at=locked_event.start_time,
            status="pending",
        )
        db.add(meeting)
        await db.flush()

        # Step 7: Dispatch bot via orchestrator
        orchestrator = OrchestratorClient(settings.ORCHESTRATOR_URL)
        result = await orchestrator.create_bot(
            meeting_url=locked_event.meeting_url,
            callback_url=settings.BOT_CALLBACK_URL,
            meeting_id=str(meeting.id),
        )

        # Step 8: Success — link everything
        meeting.bot_job_id = result.get("jobId") or result.get("job_id")
        locked_event.linked_meeting_id = meeting.id
        await db.commit()

        logger.info(
            f"Dispatched bot for event '{locked_event.title}' via {trigger} "
            f"(event={cal_event.id}, meeting={meeting.id}, job={meeting.bot_job_id})"
        )
        return True

    except Exception as e:
        await db.rollback()
        await _handle_scheduling_failure(db, cal_event, connection, e, trigger)
        return False


async def _handle_scheduling_failure(
    db: AsyncSession,
    cal_event: CalendarEvent,
    connection: CalendarConnection,
    error: Exception,
    trigger: str,
) -> None:
    """Handle a bot scheduling failure with retry logic."""
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.id == cal_event.id)
    )
    event = result.scalar_one()

    event.bot_retry_count += 1

    if event.bot_retry_count < MAX_RETRIES:
        event.bot_scheduled = False
        logger.warning(
            f"Bot dispatch failed for event {event.id} via {trigger} "
            f"(attempt {event.bot_retry_count}/{MAX_RETRIES}): {error}"
        )
    else:
        event.bot_scheduled = True

        team_member = await _get_user_team(db, connection.user_id)
        if team_member:
            failed_meeting = Meeting(
                team_id=team_member.team_id,
                created_by=connection.user_id,
                title=cal_event.title,
                meeting_url=cal_event.meeting_url,
                source="calendar",
                calendar_event_id=cal_event.provider_event_id,
                scheduled_start_at=cal_event.start_time,
                status="failed",
            )
            db.add(failed_meeting)
            await db.flush()
            event.linked_meeting_id = failed_meeting.id

        logger.error(
            f"Bot dispatch permanently failed for event {event.id} via {trigger} "
            f"after {MAX_RETRIES} attempts: {error}"
        )

    await db.commit()


async def _get_user_team(db: AsyncSession, user_id) -> TeamMember | None:
    """Get the user's first accepted team membership."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.user_id == user_id,
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def _find_existing_meeting(
    db: AsyncSession,
    team_id,
    meeting_url: str,
    start_time: datetime,
) -> Meeting | None:
    """Check if a meeting already exists for this URL + time window (dedup)."""
    window = timedelta(minutes=5)
    result = await db.execute(
        select(Meeting).where(
            Meeting.team_id == team_id,
            Meeting.meeting_url == meeting_url,
            Meeting.scheduled_start_at >= start_time - window,
            Meeting.scheduled_start_at <= start_time + window,
            Meeting.status.notin_(["failed", "cancelled"]),
        ).limit(1)
    )
    return result.scalar_one_or_none()
