"""
CRM push audit model — audit trail for every CRM push (auto + manual).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class CrmPushAudit(Base):
    __tablename__ = "crm_push_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    push_result_id = Column(UUID(as_uuid=True), ForeignKey("crm_push_results.id", ondelete="SET NULL"), nullable=True)

    provider = Column(String(20), nullable=False)  # "hubspot" | "zoho"
    push_type = Column(String(10), nullable=False)  # "auto" | "manual"
    previous_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)

    pushed_by = Column(UUID(as_uuid=True), nullable=True)
    pushed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_crm_push_audit_meeting", "meeting_id"),
        Index("ix_crm_push_audit_team", "team_id", "pushed_at"),
    )
