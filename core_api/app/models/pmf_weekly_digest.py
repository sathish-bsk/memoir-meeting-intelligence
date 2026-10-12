"""
PMF weekly digest model — pre-computed 5-dimension PMF data per week.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Float, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class PmfWeeklyDigest(Base):
    __tablename__ = "pmf_weekly_digest"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    week_start = Column(Date, nullable=False)
    total_calls = Column(Integer, nullable=False)

    # Dimension 1: Feature Demand — [{value, calls_mentioned, total_calls, stars, trend, prev_count, mentions}]
    feature_demand = Column(JSONB, nullable=True)

    # Dimension 2: Objection Frequency — same structure
    objections = Column(JSONB, nullable=True)

    # Dimension 3: Competitor Mentions — same structure
    competitors = Column(JSONB, nullable=True)

    # Dimension 4: Buy Signal Strength
    buy_signal_rate = Column(Float, nullable=True)
    buy_signal_stars = Column(Integer, nullable=True)
    buy_signal_prev_rate = Column(Float, nullable=True)

    # Dimension 5: Pain Clarity
    pain_clarity_rate = Column(Float, nullable=True)
    pain_clarity_stars = Column(Integer, nullable=True)
    pain_clarity_top_pain = Column(Text, nullable=True)
    pain_clarity_prev_rate = Column(Float, nullable=True)

    # LLM narrative
    narrative = Column(Text, nullable=True)

    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("team_id", "week_start", name="uq_pmf_weekly_digest_team_week"),
        Index("ix_pmf_weekly_digest_team", "team_id", "week_start"),
    )
