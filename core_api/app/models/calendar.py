"""
Calendar integration models — CalendarConnection and CalendarEvent.
Same table definitions as integration_service (shared videoclip_db).
Core_api needs these for Alembic migration detection and reading events.
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

    # Proactive refresh scheduler state (migration 041, 2026-05-29).
    # next_refresh_at: when the row becomes refresh-eligible. Cron's
    # WHERE clause filters on this; NULL parks a row (used for terminal
    # giveup so we stop trying without flipping token_invalid for the
    # transient-attempt-exhausted case).
    next_refresh_at = Column(DateTime(timezone=True), nullable=True)
    # Incremented on transient failures (network / 5xx / 429). Reset to 0
    # on successful refresh. Cap enforced in calendar_token_refresher.py.
    refresh_attempt_count = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Free-text classification of the last refresh outcome. Format:
    # "transient: <name>" or "permanent: <name>" or NULL on success.
    last_refresh_error = Column(Text, nullable=True)

    # Channel-renewal tracking (migration 042, 2026-05-30). Separate
    # counter from refresh_attempt_count because the two failure modes
    # are independent — a connection can have a working OAuth grant but
    # a corrupted watch channel, or vice versa. Both ultimately set
    # token_invalid=true on permanent failure.
    renewal_failure_count = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_renewal_error = Column(Text, nullable=True)

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
    linked_meeting_id = Column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Auto-join scheduling state
    bot_scheduled = Column(Boolean, nullable=False, default=False)
    bot_retry_count = Column(Integer, nullable=False, default=0)

    # Meeting event subscription ID (Teams Graph / Google Workspace Events)
    meeting_subscription_id = Column(String(200), nullable=True)

    # ── Phase 2 (notetaker-email plan §4.6) — additive only ───────────
    # Resolved ownership: for user-OAuth events these mirror the connection's
    # user_id/team_id; for bot_calendar events they come from the organizer
    # email lookup. Lets us query event ownership without joining through
    # calendar_connections + team_members.
    resolved_team_id = Column(UUID(as_uuid=True), nullable=True)
    resolved_user_id = Column(UUID(as_uuid=True), nullable=True)
    # RFC 5546 SEQUENCE — bumps on every organizer-side edit. Used to
    # detect reschedules vs. mere title-only updates (§4.8).
    ics_sequence = Column(Integer, nullable=True)
    # Set when ICS STATUS:CANCELLED arrives. Scheduler skips rows where
    # this is non-NULL.
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    # True when the Memoir bot is an attendee on this event (whether
    # the row was sourced from the user's connected calendar OR from
    # the bot_calendar synthetic connection). Surfaces a UI badge.
    bot_invited = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_calendar_events_connection_provider_event", "connection_id", "provider_event_id", unique=True),
        Index("ix_calendar_events_resolved_team", "resolved_team_id"),
        Index("ix_calendar_events_resolved_user", "resolved_user_id"),
    )


class BotCalendarUnmatched(Base):
    """Phase 2 — invites where the organizer doesn't match any users.email.

    Per notetaker-email plan §4.9: we don't create a calendar_event for
    these (no team to dispatch under), but we log the attempt + send a
    "please sign up" autoresponder so the prospect knows their invite
    was received. 7-day retention enforced by a separate cleanup cron.
    """

    __tablename__ = "bot_calendar_unmatched"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organizer_email = Column(String(320), nullable=False)
    ics_uid = Column(String(500), nullable=False, unique=True)
    title = Column(Text, nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=True)
    meeting_url = Column(String(1000), nullable=True)
    raw_event = Column(JSONB, nullable=True)
    # Why we couldn't dispatch — examples: 'no_memoir_user',
    # 'no_meeting_url', 'unsupported_platform'.
    reason = Column(String(100), nullable=True)
    autoresponder_sent_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_bot_calendar_unmatched_organizer", "organizer_email"),
        Index("ix_bot_calendar_unmatched_received_at", "received_at"),
    )
