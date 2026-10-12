"""
Team and TeamMember models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Boolean,
    Integer,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), nullable=False)  # user_id from auth service
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Onboarding / intelligence config
    crm_type = Column(String(50))  # hubspot, zoho, none
    team_size_range = Column(String(50))  # 1-5, 6-20, 21-50, 50+
    intelligence_priority = Column(String(100))  # product-market-fit, pricing, competition, objections, everything
    company_description = Column(Text)
    icp_description = Column(Text)
    known_competitors = Column(JSONB, default=list)  # ["Gong", "Chorus"]
    digest_day = Column(String(50), default="monday_morning")  # sunday_evening, monday_morning, friday_evening
    blocker_alerts = Column(Boolean, default=True)

    # Domain-based join
    domain = Column(String(255), nullable=True, index=True)

    # Relationships
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)  # from auth service
    role = Column(String(20), nullable=False, default="member")  # member, admin, owner
    invite_code = Column(String(20), unique=True)
    invite_status = Column(String(20), default="accepted")  # pending, accepted, declined
    joined_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    invited_email = Column(String(320), nullable=True)
    invited_name = Column(String(255), nullable=True)

    # Domain-based join request fields
    requested_at = Column(DateTime(timezone=True), nullable=True)
    requested_message = Column(String(500), nullable=True)

    team = relationship("Team", back_populates="members")

    __table_args__ = (
        Index("ix_team_members_team_user", "team_id", "user_id", unique=True),
    )
