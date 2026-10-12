"""
Transcription model — search index + metadata (large content in files).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID

from sqlalchemy.orm import relationship

from app.core.database import Base


class Transcription(Base):
    __tablename__ = "transcriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    version = Column(Integer, default=1, nullable=False)
    recording_source = Column(String(20), default="bot")  # bot, zoom_cloud, upload

    # Search index (generated from full_text before insert)
    search_vector = Column(TSVECTOR)

    # Metadata
    speaker_count = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    language = Column(String(10), default="en")

    # Speaker mapping cache (rebuilt when meeting_participants change)
    speaker_map = Column(JSONB, nullable=True)

    # File paths (relative)
    segments_path = Column(Text)  # meetings/{id}/v{N}/transcript_segments.json
    full_text_path = Column(Text)  # meetings/{id}/v{N}/transcript.txt

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    meeting = relationship("Meeting", back_populates="transcriptions")

    __table_args__ = (
        UniqueConstraint("meeting_id", "version", name="uq_transcriptions_meeting_version"),
        Index("ix_transcriptions_search", "search_vector", postgresql_using="gin"),
    )
