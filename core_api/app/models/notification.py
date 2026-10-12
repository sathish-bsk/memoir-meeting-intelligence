"""
Notification models — preferences per user/team/event and activity log for bell icon.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class NotificationPreference(Base):
    """Per-user per-event-type per-channel toggle (email / slack)."""
    __tablename__ = "notification_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_notif_pref_user_team", "user_id", "team_id"),
    )


class NotificationLog(Base):
    """Activity log entry — powers the bell icon dropdown."""
    __tablename__ = "notification_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text)
    link = Column(String(500))
    channel = Column(String(20))
    status = Column(String(20), nullable=False, default="sent")
    read = Column(Boolean, default=False, nullable=False)
    metadata_ = Column("metadata", JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_notif_log_user_read_created", "user_id", "read", "created_at"),
        Index("ix_notif_log_team_created", "team_id", "created_at"),
    )


class EmailUnsubscribeToken(Base):
    __tablename__ = "email_unsubscribe_tokens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(320), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), nullable=False)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(50), default="unsubscribed")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("email", "team_id", name="uq_email_suppression"),)
