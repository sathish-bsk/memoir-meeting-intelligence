"""
Calendar auto-scheduler — polls calendar_events for upcoming meetings and dispatches bots.

Runs every 60 seconds from the main scheduler loop. Finds events starting within
the next 1 minute (and up to 5 minutes past) that haven't been scheduled yet,
then dispatches bots via the shared early_join_dispatcher.

This is the fallback mechanism — if a platform webhook (Zoom meeting.started,
Teams callStarted, Meet conference.started) already dispatched a bot, the
bot_scheduled flag will be True and this scheduler skips the event.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarConnection, CalendarEvent
from app.services.early_join_dispatcher import MAX_RETRIES, dispatch_bot_for_calendar_event

logger = logging.getLogger(__name__)


async def check_and_schedule_upcoming_bots(db: AsyncSession) -> int:
    """Find calendar events about to start and dispatch bots for them.

    Returns the number of bots successfully scheduled.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=5)
    window_end = now + timedelta(minutes=1)

    # Find eligible events: has meeting URL, not yet scheduled, retries remaining,
    # within the time window, the calendar connection has auto_record enabled,
    # and the event hasn't been cancelled (Phase 2 — notetaker-email plan §4.8).
    result = await db.execute(
        select(CalendarEvent, CalendarConnection)
        .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.bot_scheduled == False,  # noqa: E712
            CalendarEvent.bot_retry_count < MAX_RETRIES,
            CalendarEvent.start_time >= window_start,
            CalendarEvent.start_time <= window_end,
            CalendarEvent.cancelled_at.is_(None),
            CalendarConnection.auto_record == True,  # noqa: E712
        )
    )
    rows = result.all()

    if not rows:
        return 0

    scheduled_count = 0

    for cal_event, connection in rows:
        try:
            success = await dispatch_bot_for_calendar_event(
                db, cal_event, connection, trigger="auto_scheduler"
            )
            if success:
                scheduled_count += 1
        except Exception as e:
            logger.error(f"Auto-scheduler error for event {cal_event.id}: {e}")

    return scheduled_count
