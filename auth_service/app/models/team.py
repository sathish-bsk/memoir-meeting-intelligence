"""
Team and team membership models.
"""

import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, new_uuid, utcnow


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    plan: Mapped[str] = mapped_column(
        String(50), default="free", nullable=False, server_default="free"
    )
    plan_limits: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True  # JSON overrides for per-team limit customization
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    members: Mapped[List["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), default="member", nullable=False  # owner, admin, member
    )
    scope_overrides: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True  # JSON: {"meetings:delete": true, "clips:write": false}
    )
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    team: Mapped["Team"] = relationship(back_populates="members")
