"""
WeeklyDigest model — digest metadata + file path (all sections in digest_content.json).
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
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class WeeklyDigest(Base):
    __tablename__ = "weekly_digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    week_start = Column(DateTime(timezone=True), nullable=False)
    week_end = Column(DateTime(timezone=True), nullable=False)
    total_calls = Column(Integer, default=0)
    total_reps = Column(Integer, default=0)
    headline = Column(Text)

    # File path (relative)
    content_path = Column(Text)  # digests/{id}/digest_content.json

    # Thread to previous digest
    previous_digest_id = Column(UUID(as_uuid=True), ForeignKey("weekly_digests.id", ondelete="SET NULL"))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_weekly_digests_team_week", "team_id", "week_start", unique=True),
    )
