"""
CRM push result model — tracks per-meeting per-provider CRM push attempts.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class CrmPushResult(Base):
    __tablename__ = "crm_push_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    provider = Column(String(20), nullable=False)  # "hubspot" | "zoho"

    # Push status: pending | processing | completed | failed | skipped
    status = Column(String(20), nullable=False, default="pending")

    # CRM record IDs
    crm_contact_id = Column(String(255), nullable=True)
    crm_deal_id = Column(String(255), nullable=True)
    crm_record_id = Column(String(255), nullable=True)
    crm_record_url = Column(String(1024), nullable=True)

    # Data
    pushed_data = Column(JSONB, default=dict, nullable=False)
    edited_data = Column(JSONB, nullable=True)
    extraction_snapshot = Column(JSONB, default=dict, nullable=False)

    # Error tracking
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)

    # Timestamps
    pushed_at = Column(DateTime(timezone=True), nullable=True)
    pushed_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("meeting_id", "provider", name="uq_crm_push_results_meeting_provider"),
        Index("ix_crm_push_results_meeting", "meeting_id"),
        Index("ix_crm_push_results_team_status", "team_id", "status"),
    )
