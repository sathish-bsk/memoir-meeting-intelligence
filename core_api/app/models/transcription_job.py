"""
TranscriptionJob model — DB-based job queue for decoupled transcription processing.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID

from app.core.database import Base


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=True,
    )
    bot_job_id = Column(String(255))

    # 1 = transcription (default), 2 = crm_extraction
    operation_type = Column(Integer, nullable=False, default=1)

    # Status: waiting -> in_progress -> completed | failed
    status = Column(String(20), nullable=False, default="waiting")

    # Everything the job needs to run — single JSONB blob
    params = Column(JSON, nullable=False, default=dict)

    # Lightweight summary only (NOT the full transcription)
    result = Column(JSON, default=dict)

    # Error tracking
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    failed_at = Column(DateTime(timezone=True))

    # Extensible metadata
    metadata_ = Column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ix_transcription_jobs_status", "status"),
        Index("ix_transcription_jobs_meeting", "meeting_id"),
    )
