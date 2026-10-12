"""
Deal match log model — records how each call was matched to a deal.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class DealMatchLog(Base):
    __tablename__ = "deal_match_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    memoir_deal_id = Column(UUID(as_uuid=True), ForeignKey("memoir_deals.id", ondelete="CASCADE"), nullable=False)

    # Match method: email | domain | company_name | new
    match_method = Column(Text, nullable=False)
    match_confidence = Column(Float, nullable=True)
    matched_on = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    memoir_deal = relationship("MemoirDeal", back_populates="match_logs")

    __table_args__ = (
        Index("ix_deal_match_log_meeting", "meeting_id"),
        Index("ix_deal_match_log_deal", "memoir_deal_id"),
    )
