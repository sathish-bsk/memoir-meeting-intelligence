"""
Call intelligence models — per-call extraction results + flattened aggregation tables.
"""

import uuid
from datetime import datetime, time, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class CallIntelligence(Base):
    """Main per-call intelligence extraction results from the 6-prompt pipeline."""
    __tablename__ = "call_intelligence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    call_type = Column(Text, nullable=False)  # external_sales / internal / uncertain
    classification_confidence = Column(Text)

    # Prompt 2: CRM extraction
    crm_extraction = Column(JSONB)

    # Prompt 3: Intelligence extraction
    pain_points = Column(JSONB)
    desired_outcomes = Column(JSONB)
    feature_requests = Column(JSONB)
    competitors_mentioned = Column(JSONB)
    objections = Column(JSONB)
    buying_signals = Column(JSONB)
    red_flags = Column(JSONB)
    tech_stack = Column(JSONB)
    budget_signals = Column(JSONB)
    decision_process = Column(JSONB)
    product_perception = Column(JSONB)

    # Prompt 4: PMF signals
    pmf_signals = Column(JSONB)
    pull_score = Column(Integer)
    icp_fit_score = Column(Integer)
    pain_fit_score = Column(Integer)
    overall_pmf_signal = Column(Text)

    # Deal intelligence (from pipeline + validation)
    deal_momentum = Column(Text)  # accelerating | steady | decelerating | stalled
    validated_stage = Column(Text)  # AI stage after validation rules
    crm_stage_id = Column(String(100))  # CRM pipeline stage ID snapshot
    crm_stage_label = Column(String(255))  # CRM pipeline stage label snapshot

    # Buy signal (from P3 enhancement)
    has_committed_next_step = Column(Boolean)
    next_step_type = Column(Text)  # demo_booked | trial_started | pricing_requested | follow_up_confirmed | none

    # Prompt 5: Summary
    summary = Column(JSONB)
    call_health = Column(Text)

    # Prompt 6: FAQ
    questions = Column(JSONB)

    # Processing
    processing_status = Column(Text, nullable=False, default="completed")
    processing_started_at = Column(DateTime(timezone=True))
    processing_completed_at = Column(DateTime(timezone=True))
    processing_error = Column(Text)
    total_tokens_used = Column(Integer)
    total_cost_usd = Column(Numeric(10, 6))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Retranscribe support — multiple call_intelligence rows can exist per
    # meeting (one per pipeline run). The latest version is the authoritative
    # one; older versions are kept for audit/comparison. The unique constraint
    # below means upserts must always include the version in the conflict key.
    # Added in migration 035 (May 11, 2026) — prior rows were back-filled to
    # version=1 by created_at ordering.
    version = Column(Integer, nullable=False, default=1)

    # Relationships
    pain_point_rows = relationship("CallPainPoint", back_populates="call_intelligence", cascade="all, delete-orphan")
    feature_request_rows = relationship("CallFeatureRequest", back_populates="call_intelligence", cascade="all, delete-orphan")
    competitor_rows = relationship("CallCompetitor", back_populates="call_intelligence", cascade="all, delete-orphan")
    objection_rows = relationship("CallObjection", back_populates="call_intelligence", cascade="all, delete-orphan")
    question_rows = relationship("CallQuestion", back_populates="call_intelligence", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_call_intelligence_meeting", "meeting_id"),
        Index("ix_call_intelligence_meeting_version", "meeting_id", "version"),
        Index("ix_call_intelligence_team_created", "team_id", "created_at"),
    )


class CallPainPoint(Base):
    """Flattened pain points for aggregation queries."""
    __tablename__ = "call_pain_points"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_intelligence_id = Column(UUID(as_uuid=True), ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False)
    meeting_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    pain = Column(Text, nullable=False)
    severity = Column(Text)
    exact_quote = Column(Text)
    timestamp_in_call = Column(Text)
    speaker_name = Column(String(255))
    explicitly_stated = Column(Boolean, default=True)
    call_date = Column(Date)
    prospect_company = Column(String(255))

    call_intelligence = relationship("CallIntelligence", back_populates="pain_point_rows")

    __table_args__ = (
        Index("ix_call_pain_points_team_date", "team_id", "call_date"),
        Index("ix_call_pain_points_team_user_date", "team_id", "created_by", "call_date"),
    )


class CallFeatureRequest(Base):
    """Flattened feature requests for aggregation queries."""
    __tablename__ = "call_feature_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_intelligence_id = Column(UUID(as_uuid=True), ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False)
    meeting_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    feature = Column(Text, nullable=False)
    use_case = Column(Text)
    urgency = Column(Text)
    exact_quote = Column(Text)
    timestamp_in_call = Column(Text)
    speaker_name = Column(String(255))
    call_date = Column(Date)
    prospect_company = Column(String(255))

    call_intelligence = relationship("CallIntelligence", back_populates="feature_request_rows")

    __table_args__ = (
        Index("ix_call_feature_requests_team_date", "team_id", "call_date"),
        Index("ix_call_feature_requests_team_user_date", "team_id", "created_by", "call_date"),
    )


class CallCompetitor(Base):
    """Flattened competitor mentions for aggregation queries."""
    __tablename__ = "call_competitors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_intelligence_id = Column(UUID(as_uuid=True), ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False)
    meeting_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    competitor = Column(String(255), nullable=False)
    context = Column(Text)
    sentiment = Column(Text)
    what_they_like = Column(Text)
    what_they_dislike = Column(Text)
    exact_quote = Column(Text)
    timestamp_in_call = Column(Text)
    speaker_name = Column(String(255))
    call_date = Column(Date)
    prospect_company = Column(String(255))

    call_intelligence = relationship("CallIntelligence", back_populates="competitor_rows")

    __table_args__ = (
        Index("ix_call_competitors_team_date", "team_id", "call_date"),
        Index("ix_call_competitors_team_user_date", "team_id", "created_by", "call_date"),
    )


class CallObjection(Base):
    """Flattened objections for aggregation queries."""
    __tablename__ = "call_objections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_intelligence_id = Column(UUID(as_uuid=True), ForeignKey("call_intelligence.id", ondelete="CASCADE"), nullable=False)
    meeting_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    objection = Column(Text, nullable=False)
    category = Column(Text)
    how_handled = Column(Text)
    exact_quote = Column(Text)
    timestamp_in_call = Column(Text)
    speaker_name = Column(String(255))
    call_date = Column(Date)
    prospect_company = Column(String(255))

    call_intelligence = relationship("CallIntelligence", back_populates="objection_rows")

    __table_args__ = (
        Index("ix_call_objections_team_date", "team_id", "call_date"),
        Index("ix_call_objections_team_user_date", "team_id", "created_by", "call_date"),
    )


class CallQuestion(Base):
    """Flattened prospect questions (FAQ) for aggregation queries."""
    __tablename__ = "call_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_intelligence_id = Column(UUID(as_uuid=True), ForeignKey("call_intelligence.id", ondelete="CASCADE"))
    meeting_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    created_by = Column(UUID(as_uuid=True))
    question = Column(Text, nullable=False)
    exact_quote = Column(Text)
    timestamp_in_call = Column(Text)
    speaker_name = Column(String(255))
    category = Column(Text)
    was_answered = Column(Boolean)
    answer_quality = Column(Text)
    call_date = Column(Date)
    prospect_company = Column(String(255))

    call_intelligence = relationship("CallIntelligence", back_populates="question_rows")

    __table_args__ = (
        Index("ix_call_questions_team_date", "team_id", "call_date"),
        Index("ix_call_questions_team_user_date", "team_id", "created_by", "call_date"),
    )


class IntelligenceDigest(Base):
    """Periodic intelligence digest narratives."""
    __tablename__ = "intelligence_digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    narrative = Column(Text)
    metrics = Column(JSONB)
    total_calls = Column(Integer, default=0)
    cost_usd = Column(Numeric(10, 6))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_intelligence_digests_team", "team_id", "period_start"),
    )


class ScheduledJob(Base):
    """User-configurable scheduled job definitions."""
    __tablename__ = "scheduled_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    job_type = Column(String(50), nullable=False)  # intelligence_digest, faq_grouping
    frequency = Column(String(20), nullable=False, default="weekly")
    preferred_time = Column(Time, default=time(6, 0))
    preferred_day = Column(Integer)  # 0=Mon for weekly, 1-28 for monthly
    enabled = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime(timezone=True))
    last_run_at = Column(DateTime(timezone=True))
    last_status = Column(String(20))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    runs = relationship("ScheduledJobRun", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_scheduled_jobs_team_type", "team_id", "job_type", unique=True),
        Index("ix_scheduled_jobs_next_run", "enabled", "next_run_at"),
    )


class ShareLink(Base):
    """Generic share links for calls and intelligence deep dives."""
    __tablename__ = "share_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_token = Column(String(64), unique=True, nullable=False, index=True)
    share_type = Column(String(50), nullable=False)  # "call" | "intelligence"
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    title = Column(String(255), nullable=False)
    metadata_ = Column("metadata", JSONB, default={})
    snapshot_data = Column(JSONB, nullable=False, server_default='{}')
    share_views = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ScheduledJobRun(Base):
    """Audit log of every scheduled job execution."""
    __tablename__ = "scheduled_job_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    job_type = Column(String(50), nullable=False)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, default="running")
    result_summary = Column(Text)
    error_message = Column(Text)
    records_processed = Column(Integer, default=0)
    cost_usd = Column(Numeric(10, 6))

    job = relationship("ScheduledJob", back_populates="runs")

    __table_args__ = (
        Index("ix_scheduled_job_runs_job", "job_id", "started_at"),
    )
