"""
Memoir deal model — internal deal tracker for threading calls into deals.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class MemoirDeal(Base):
    __tablename__ = "memoir_deals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    prospect_name = Column(String(255), nullable=True)
    prospect_company = Column(String(255), nullable=False)
    prospect_email = Column(String(255), nullable=True)
    prospect_domain = Column(String(255), nullable=True)

    # Deal status: open | won | lost | stalled
    status = Column(Text, nullable=False, default="open")

    # AI-validated stage: awareness | interest | consideration | intent | evaluation | purchase
    current_stage = Column(Text, nullable=True)

    # CRM linkage
    hubspot_deal_id = Column(String(50), nullable=True)
    zoho_deal_id = Column(String(50), nullable=True)
    crm_current_stage_id = Column(String(100), nullable=True)
    crm_current_stage_label = Column(String(255), nullable=True)

    # Aggregates
    total_calls = Column(Integer, nullable=False, default=0)
    first_call_date = Column(DateTime(timezone=True), nullable=True)
    last_call_date = Column(DateTime(timezone=True), nullable=True)
    latest_sentiment = Column(Text, nullable=True)
    latest_call_health = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    meetings = relationship("Meeting", back_populates="memoir_deal")
    match_logs = relationship("DealMatchLog", back_populates="memoir_deal", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_memoir_deals_team", "team_id"),
        Index("ix_memoir_deals_team_company", "team_id", "prospect_company"),
        Index("ix_memoir_deals_team_domain", "team_id", "prospect_domain"),
        Index("ix_memoir_deals_team_email", "team_id", "prospect_email"),
    )
