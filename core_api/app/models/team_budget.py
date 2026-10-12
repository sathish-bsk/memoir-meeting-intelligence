"""
Team budget model — monthly spend limits and alert thresholds per team.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class TeamBudget(Base):
    __tablename__ = "team_budget"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)

    monthly_budget_usd = Column(Float, nullable=True)
    alert_threshold_pct = Column(Integer, nullable=False, default=80)
    hard_limit_enabled = Column(Boolean, nullable=False, default=False)
    last_alert_sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_team_budget_team", "team_id", unique=True),
    )
