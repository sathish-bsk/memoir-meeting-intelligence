"""
Pydantic schemas for calendar event listing and bot scheduling.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CalendarAttendee(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None
    response: Optional[str] = None
    organizer: Optional[bool] = None


class CalendarEventResponse(BaseModel):
    id: UUID
    provider_event_id: str
    title: str
    start_time: datetime
    end_time: datetime
    meeting_url: Optional[str] = None
    linked_meeting_id: Optional[UUID] = None
    provider: str = "google"
    bot_scheduled: bool = False
    attendees: Optional[list[CalendarAttendee]] = None
    # Phase 2: discriminator so the UI can label events sourced from
    # the bot's own calendar with a "via Memoir invite" badge.
    source: str = "calendar"  # "calendar" | "bot_calendar"
    bot_invited: bool = False

    model_config = {"from_attributes": True}


class ScheduleBotRequest(BaseModel):
    title: Optional[str] = None


# ─── v3 list (plan/meetings-events-v3-list.md §3.3) ────────────────────
# Strictly additive over CalendarEventResponse — the new fields are
# already on the model but were never surfaced to the v2 caller.


class CalendarEventV3Response(CalendarEventResponse):
    description: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    resolved_user_id: Optional[UUID] = None
    # Joined from meetings.status via linked_meeting_id (LEFT OUTER JOIN).
    # None when no linked meeting OR meeting was deleted.
    linked_meeting_status: Optional[str] = None


class CalendarEventListV3Response(BaseModel):
    events: list[CalendarEventV3Response]
    current_user_email: str
    # "owner" | "admin" | "member" — frontend uses this to hide the
    # `All` scope chip from members without a separate role-fetch.
    current_user_role: str
