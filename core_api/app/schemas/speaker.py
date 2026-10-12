"""
Pydantic schemas for speaker identification and correction.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class SpeakerAssignRequest(BaseModel):
    """Assign a display name (and optionally a profile) to a diarization label."""

    diarization_label: str  # e.g. "SPEAKER_00"
    speaker_name: str  # e.g. "John Smith"
    speaker_profile_id: Optional[UUID] = None  # link to existing profile
    email: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None


class ParticipantOut(BaseModel):
    """A meeting participant with speaker profile info."""

    id: UUID
    diarization_label: Optional[str] = None
    display_name: Optional[str] = None
    speaker_profile_id: Optional[UUID] = None
    email: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    crm_links: Optional[dict] = None
    mapping_source: str = "unidentified"
    mapping_confidence: float = 0
    talk_time_seconds: float = 0
    segment_count: int = 0
    word_count: int = 0


class MeetingSpeakersResponse(BaseModel):
    """All participants for a meeting."""

    meeting_id: UUID
    participants: list[ParticipantOut]


class SpeakerProfileOut(BaseModel):
    """A team-level speaker profile for dropdown lists."""

    id: UUID
    display_name: str
    email: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    meeting_count: int = 0
