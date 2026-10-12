"""
Speaker identification models — SpeakerProfile, MeetingParticipant, VoiceEmbeddingSample.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class SpeakerProfile(Base):
    """Team-level contact book — each person who appears in any meeting gets a profile."""

    __tablename__ = "speaker_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Identity
    display_name = Column(String(255), nullable=False)
    email = Column(String(320), nullable=True)
    phone = Column(String(50), nullable=True)
    company = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)

    # Internal user link (null for external contacts)
    internal_user_id = Column(UUID(as_uuid=True), nullable=True)

    # CRM linkage — e.g. {"hubspot": "hs_contact_123", "zoho": "zo_contact_456"}
    crm_links = Column(JSONB, default=dict, nullable=False)

    # How this profile was first created
    source = Column(String(50), nullable=False, default="manual")

    # Denormalized stats (updated on each meeting)
    meeting_count = Column(Integer, nullable=False, default=0)
    total_talk_seconds = Column(Float, nullable=False, default=0)
    first_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Extensibility
    metadata_ = Column("metadata", JSONB, default=dict)

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    participants = relationship(
        "MeetingParticipant", back_populates="speaker_profile", cascade="all, delete-orphan"
    )
    voice_samples = relationship(
        "VoiceEmbeddingSample", back_populates="speaker_profile", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Email is the primary dedup key — unique per team when not null
        Index(
            "uq_speaker_profiles_team_email",
            "team_id",
            "email",
            unique=True,
            postgresql_where=Column("email").isnot(None),
        ),
        Index("ix_speaker_profiles_team_name", "team_id", "display_name"),
        Index(
            "ix_speaker_profiles_team_user",
            "team_id",
            "internal_user_id",
            postgresql_where=Column("internal_user_id").isnot(None),
        ),
    )


class MeetingParticipant(Base):
    """Per-meeting participation record linking a diarization label to a speaker profile."""

    __tablename__ = "meeting_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    speaker_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("speaker_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Diarization label from pyannote
    diarization_label = Column(String(20), nullable=True)

    # What the bot saw in the meeting UI
    platform_display_name = Column(String(255), nullable=True)

    # Role from LLM memoir output
    role = Column(String(20), nullable=True)

    # How the mapping was determined
    mapping_source = Column(String(50), nullable=False, default="unidentified")
    mapping_confidence = Column(Float, nullable=False, default=0)

    # Talk stats for this meeting
    talk_time_seconds = Column(Float, nullable=False, default=0)
    segment_count = Column(Integer, nullable=False, default=0)
    word_count = Column(Integer, nullable=False, default=0)

    # Join/leave timing (seconds after meeting start)
    joined_at = Column(Float, nullable=True)
    left_at = Column(Float, nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    speaker_profile = relationship("SpeakerProfile", back_populates="participants")
    meeting = relationship("Meeting")

    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "diarization_label",
            name="uq_meeting_participants_meeting_label",
        ),
        Index("ix_meeting_participants_meeting", "meeting_id"),
        Index("ix_meeting_participants_profile", "speaker_profile_id"),
    )


class VoiceEmbeddingSample(Base):
    """Voice embedding sample for a speaker profile — multiple per profile for variability."""

    __tablename__ = "voice_embedding_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    speaker_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("speaker_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The embedding vector (192-dim ECAPA-TDNN, stored as bytes)
    embedding = Column(LargeBinary, nullable=False)
    embedding_model = Column(String(100), nullable=False, default="ecapa-tdnn")

    # How much confirmed speech this was extracted from
    sample_duration_seconds = Column(Float, nullable=False)

    # Traceability
    source_meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_diarization_label = Column(String(20), nullable=True)
    source = Column(String(50), nullable=False)

    # Quality — longer duration = higher; caps at 5.0
    quality_score = Column(Float, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    speaker_profile = relationship("SpeakerProfile", back_populates="voice_samples")

    __table_args__ = (
        Index("ix_voice_embedding_samples_profile", "speaker_profile_id"),
    )
