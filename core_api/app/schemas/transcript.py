"""
Pydantic schemas for transcript operations.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TranscriptSegment(BaseModel):
    """A single transcript segment from the Whisper pipeline."""

    model_config = ConfigDict(extra="ignore")

    id: int = 0
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    speaker: Optional[str] = None
    original_start: Optional[float] = None
    original_end: Optional[float] = None
    detected_language: Optional[str] = None


class SpeakerMapEntry(BaseModel):
    """One entry in the speaker_map cache."""

    name: str
    profile_id: Optional[str] = None


class TranscriptResponse(BaseModel):
    """Full transcript response."""

    meeting_id: UUID
    segments: list[TranscriptSegment]
    speaker_count: int = 0
    word_count: int = 0
    language: str = "en"
    speaker_map: Optional[dict[str, SpeakerMapEntry]] = None
    is_edited: bool = False


class TranscriptEditRequest(BaseModel):
    """Request to edit a transcript segment."""

    segment_index: int
    text: str
