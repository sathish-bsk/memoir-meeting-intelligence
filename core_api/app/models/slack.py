"""
Slack integration models — SlackConnection and SlackNotificationSettings.
Same table definitions as integration_service (shared videoclip_db).
Core_api needs these for Alembic migration detection.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class SlackConnection(Base):
    __tablename__ = "slack_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(String(50), nullable=False)
    workspace_name = Column(String(255), nullable=True)
    encrypted_tokens = Column(Text, nullable=False)
    scopes = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_slack_connections_user_workspace", "user_id", "workspace_id", unique=True),
    )


class SlackNotificationSettings(Base):
    __tablename__ = "slack_notification_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("slack_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    channel_id = Column(String(255), nullable=True)
    channel_name = Column(String(255), nullable=True)
    notify_on_meeting_complete = Column(Boolean, nullable=False, default=True)
    notify_on_crm_pushed = Column(Boolean, nullable=False, default=True)
    notify_on_blocker_detected = Column(Boolean, nullable=False, default=True)
    notify_on_team_invite = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
