"""
MemoirSummary model — CRM status + metadata (narrative/fields/signals in analysis.json).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from sqlalchemy.orm import relationship

from app.core.database import Base


class MemoirSummary(Base):
    __tablename__ = "memoir_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    version = Column(Integer, default=1, nullable=False)

    # Confidence
    overall_confidence_score = Column(Integer, nullable=True)  # 0-100, NULL until analysis completes

    # CRM push tracking
    crm_push_status = Column(String(20), nullable=True)  # pending, pushed, skipped, failed — NULL until ready
    crm_pushed_at = Column(DateTime(timezone=True))

    # Denormalized for list-view (avoids reading analysis.json per row)
    summary_excerpt = Column(String(200), nullable=True)  # first ~150 chars of narrative_summary
    primary_participant_name = Column(String(255), nullable=True)  # prospect/customer name
    primary_participant_company = Column(String(255), nullable=True)  # their company

    # External share approval
    external_share_approved = Column(Boolean, nullable=False, default=False, server_default="false")
    external_share_approved_at = Column(DateTime(timezone=True), nullable=True)
    external_share_approved_by = Column(UUID(as_uuid=True), nullable=True)

    # Structured summary from P5 pipeline
    summary_json = Column(JSONB, nullable=True)

    # Follow-up threading
    previous_meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="SET NULL"))
    field_changes_path = Column(Text)  # meetings/{id}/field_changes.json (if follow-up)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    meeting = relationship("Meeting", back_populates="memoir_summaries", foreign_keys=[meeting_id])

    __table_args__ = (
        UniqueConstraint("meeting_id", "version", name="uq_memoir_summaries_meeting_version"),
    )
