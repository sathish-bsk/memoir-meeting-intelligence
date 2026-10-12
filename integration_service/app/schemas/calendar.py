"""
Pydantic schemas for calendar integration endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AuthUrlResponse(BaseModel):
    auth_url: str


# Backwards-compatible alias
GoogleAuthUrlResponse = AuthUrlResponse


class CalendarCallbackRequest(BaseModel):
    code: str
    state: str


class CalendarConnectionResponse(BaseModel):
    id: UUID
    user_id: UUID
    provider: str
    provider_email: str
    auto_record: bool = True
    token_invalid: bool = False
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CalendarEventResponse(BaseModel):
    id: UUID
    provider_event_id: str
    title: str
    start_time: datetime
    end_time: datetime
    meeting_url: Optional[str] = None
    linked_meeting_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


class CalendarSyncResponse(BaseModel):
    synced_count: int
    events: list[CalendarEventResponse]
