"""
Tab audio upload session and chunk models — tracks chunked audio uploads
from the browser extension's tab-capture recording flow.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class TabAudioUploadSession(Base):
    __tablename__ = "tab_audio_upload_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(30), nullable=False, default="active")
    total_chunks = Column(Integer, default=0)
    total_bytes = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    chunks = relationship("TabAudioUploadChunk", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tab_audio_sessions_meeting", "meeting_id", unique=True),
    )


class TabAudioUploadChunk(Base):
    __tablename__ = "tab_audio_upload_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("tab_audio_upload_sessions.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    track = Column(String(10), nullable=False, server_default="mixed")  # 'tab', 'mic', or 'mixed'
    sha256 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    start_ms = Column(BigInteger, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)
    file_path = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    session = relationship("TabAudioUploadSession", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("session_id", "seq", "track", name="uq_chunk_session_seq_track"),
        Index("ix_tab_audio_chunks_session", "session_id"),
    )
