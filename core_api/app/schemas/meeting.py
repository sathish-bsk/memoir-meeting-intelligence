"""
Pydantic schemas for meeting operations.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LinkUploadRequest(BaseModel):
    url: str = Field(..., min_length=1)
    team_id: UUID = Field(...)
    title: Optional[str] = None


class MeetingCreate(BaseModel):
    """Schema for creating a new meeting.

    `meeting_url` is optional — NULL for web_recorder (in-browser recording
    has no associated meeting URL) and for uploads. Required for everything
    else (bot/calendar/extension tab-audio/manual all have real URLs).
    Application-layer validation in the create handler enforces this.
    """

    meeting_url: Optional[str] = None
    title: Optional[str] = None
    source: str = "manual"
    team_id: UUID = Field(...)
    scheduled_start_at: Optional[datetime] = None
    capture_mode: str = "bot"  # "bot" | "tab_audio" | "web_recorder"
    language: Optional[str] = None  # BCP-47; NULL means multilingual


class MeetingResponse(BaseModel):
    """Schema for meeting response."""

    id: UUID
    team_id: UUID
    created_by: UUID
    created_by_name: Optional[str] = None
    title: Optional[str] = None
    meeting_url: Optional[str] = None
    status: str
    language: Optional[str] = None
    intelligence_status: Optional[str] = None
    source: Optional[str] = None
    # Populated whenever a BotWorker container handled the recording
    # (orchestrator → bot path). NULL for tab_audio / web_recorder /
    # upload meetings. Frontend uses this as the principled "bot was
    # involved" signal — bot meetings can have source = extension /
    # manual / calendar / zoom_cloud depending on entry point, so source
    # alone is insufficient.
    bot_job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    # When the bot's recorder actually began capturing audio (vs started_at
    # which is admission). Null until the recording_started webhook fires.
    # Extension uses this to gate the "Stop Recording" label flip.
    recording_started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    confidence_score: Optional[int] = None
    crm_push_status: Optional[str] = None
    has_recording: bool = False
    scheduled_start_at: Optional[datetime] = None
    created_at: datetime
    summary_excerpt: Optional[str] = None
    primary_participant_name: Optional[str] = None
    primary_participant_company: Optional[str] = None

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    """Paginated list of meetings."""

    meetings: list[MeetingResponse]
    total: int
    page: int
    per_page: int
    counts: Optional[dict[str, int]] = None
    # Plan/meetings-events-v3-list.md §3.4 + review fix M2: surfaced so v3
    # UIs can hide the `All` scope chip without trusting the auth-store
    # role (which can be stale). Optional for back-compat with v2 callers.
    current_user_role: Optional[str] = None


class MeetingDetailResponse(MeetingResponse):
    """Extended meeting response with content availability flags."""

    has_transcript: bool = False
    has_analysis: bool = False
    has_memoir: bool = False
    memoir_deal_id: Optional[UUID] = None
    external_share_approved: bool = False
    external_participants_count: int = 0


class MeetingStatusResponse(BaseModel):
    """Lightweight polling response — used by the extension to poll meeting
    state ~every 1.5 s. The full MeetingResponse / MeetingDetailResponse
    runs 5-6 SELECTs (transcriptions, memoir_summaries, participants count,
    team_members, calendar_events) which under sustained polling load
    saturated the DB connection pool and caused webhook handlers to stall.
    This response is a single SELECT — auth check + the 3 fields the
    extension actually cares about.
    """

    status: str
    intelligence_status: Optional[str] = None
    recording_started_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MeetingNotesUpdate(BaseModel):
    """Schema for upserting meeting notes."""
    content: str = Field(..., max_length=10000)


class MeetingNotesResponse(BaseModel):
    """Schema for meeting notes response."""
    meeting_id: UUID
    user_id: UUID
    content: str
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
