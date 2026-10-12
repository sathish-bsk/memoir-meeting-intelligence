"""
Calendar integration models — CalendarConnection and CalendarEvent.
Tables live in shared videoclip_db; migrations managed by core_api.

NOTE: ForeignKeys to tables owned by core_api (teams, meetings) are omitted here
because integration_service doesn't register those models. The actual DB constraints
exist via the migration; SQLAlchemy just doesn't need to resolve them in this service.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    provider = Column(String(50), nullable=False, default="google")
    encrypted_tokens = Column(Text, nullable=False)
    provider_email = Column(String(320), nullable=False)
    last_synced_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Auto-join settings
    auto_record = Column(Boolean, nullable=False, default=True)

    # Token health — set to True when refresh permanently fails (invalid_grant)
    token_invalid = Column(Boolean, nullable=False, default=False)
    token_invalid_at = Column(DateTime(timezone=True), nullable=True)

    # Push notification state (Google watch channel / Microsoft Graph subscription)
    sync_token = Column(Text, nullable=True)
    watch_channel_id = Column(String(100), nullable=True)
    watch_resource_id = Column(String(200), nullable=True)
    watch_expires_at = Column(DateTime(timezone=True), nullable=True)
    subscription_id = Column(String(200), nullable=True)  # Microsoft Graph subscription ID

    __table_args__ = (
        Index("ix_calendar_connections_user_provider", "user_id", "provider", unique=True),
    )


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_event_id = Column(String(500), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    meeting_url = Column(String(1000), nullable=True)
    attendees = Column(JSONB, nullable=True)  # [{email, name, response, organizer}, ...]
    linked_meeting_id = Column(UUID(as_uuid=True), nullable=True)

    # Auto-join scheduling state
    bot_scheduled = Column(Boolean, nullable=False, default=False)
    bot_retry_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_calendar_events_connection_provider_event", "connection_id", "provider_event_id", unique=True),
    )
