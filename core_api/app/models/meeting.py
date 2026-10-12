"""
Meeting model — lifecycle tracking + file paths to stored content.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=False)  # user_id from auth
    title = Column(String(500))
    meeting_url = Column(Text, nullable=True)
    bot_job_id = Column(String(255))  # orchestrator job ID

    # Lifecycle
    status = Column(
        String(100),
        nullable=False,
        default="pending",
        index=True,
    )
    # Allowed values (DB-enforced via `meetings_status_valid` CHECK constraint,
    # alembic migration 037_meetings_status_check_constraint):
    #   pending            — created, awaiting trigger
    #   scheduled          — calendar-sourced, future meeting
    #   joining            — bot dispatching to meeting URL
    #   waiting_in_lobby   — bot in lobby, awaiting host admit
    #   recording          — bot admitted, recorder spinning up / capturing
    #   waiting_for_transcription — recording uploaded, waiting for Type 1
    #   processing         — pipeline running (Type 1-7 in flight)
    #   completed          — full pipeline succeeded (T1-T2 done)
    #   failed             — terminal error somewhere in pipeline
    #   fast_email_only    — T6 diarization failed both first + retry; Type 7
    #                        fast email sent with UNKNOWN labels best-effort;
    #                        T5/T3/T2 skipped. User can retranscribe to retry.
    # To add a status, file a new alembic migration that widens the IN list
    # AND update this comment block in the same commit. Friction is intentional.
    intelligence_status = Column(String(50), default="pending")

    # Source tracking
    # Known values: manual, calendar, extension, zoom_cloud, teams_cloud,
    # upload, link_upload, extension_tab_audio, web_recorder
    source = Column(String(30), default="manual")

    # User-selected transcription language (BCP-47 code: "en", "en-US", "es", "ta"…)
    # NULL means "multilingual / auto-detect" — preserves today's behavior for every
    # pre-migration row. Set by /record page language picker; carried through to
    # transcription_jobs.params.language by tab_audio.py finalizers.
    language = Column(String(16), nullable=True)
    calendar_event_id = Column(String(500))  # for calendar dedup
    zoom_meeting_id = Column(String(100))  # Zoom numeric meeting ID for dedup
    teams_meeting_id = Column(String(255))  # Teams meeting ID for dedup
    scheduled_start_at = Column(DateTime(timezone=True))  # when bot should join

    # Timing
    started_at = Column(DateTime(timezone=True))
    # Distinct from started_at: started_at marks when the bot was admitted
    # (joined_meeting webhook). recording_started_at marks when the bot's
    # recorder actually began capturing audio (recording_started webhook),
    # which is typically ~2-3 s later because of FFmpeg cold start.
    # The extension's floating button uses this to know when it's safe to
    # flip to "Stop Recording" — flipping on started_at alone would lose
    # the first 1-2 s of audio because the user starts speaking thinking
    # recording is active.
    recording_started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)

    # File paths (relative — resolved by StorageService at runtime)
    original_recording_path = Column(Text)  # raw source file (never overwritten after creation)
    recording_path = Column(Text)  # processed audio (set at creation, updated by pipeline)
    transcript_path = Column(Text)  # meetings/{id}/transcript_segments.json
    analysis_path = Column(Text)  # meetings/{id}/analysis.json
    intelligence_path = Column(Text)  # meetings/{id}/intelligence.json

    # Deal threading
    memoir_deal_id = Column(UUID(as_uuid=True), ForeignKey("memoir_deals.id", ondelete="SET NULL"), nullable=True)
    call_number_in_deal = Column(Integer, nullable=True)

    # Set when an auto-summary email has been sent for this meeting (fast-P5 path
    # or polished-path fallback). Reset to None by retranscribe to allow a fresh
    # send on the new run.
    summary_email_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Editable next-steps fields surfaced on the v3 detail page
    # (milestone + next_meeting_date). Shared across team — not per-user.
    next_steps_data = Column(JSONB, nullable=True)

    # Structured reason a meeting reached status='failed'. Set in addition
    # to status by code paths that need to communicate WHY the row failed
    # without widening the status enum. Phase 2 uses 'calendar_reschedule'
    # and 'calendar_cancelled' (notetaker-email plan §4.8).
    failure_reason = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    team = relationship("Team", back_populates="meetings")
    transcriptions = relationship("Transcription", back_populates="meeting", cascade="all, delete-orphan")
    memoir_summaries = relationship("MemoirSummary", back_populates="meeting", cascade="all, delete-orphan", foreign_keys="[MemoirSummary.meeting_id]")
    memoir_deal = relationship("MemoirDeal", back_populates="meetings")

    __table_args__ = (
        Index("ix_meetings_team_status", "team_id", "status"),
        Index("ix_meetings_team_created", "team_id", "created_at"),
        Index("ix_meetings_zoom_meeting_id", "zoom_meeting_id"),
        Index("ix_meetings_teams_meeting_id", "teams_meeting_id"),
        Index("ix_meetings_deal", "memoir_deal_id"),
    )
