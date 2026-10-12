"""
Recording connection model — unified table for Zoom and Teams cloud recording connections.
Table lives in shared videoclip_db; migrations managed by core_api.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class RecordingConnection(Base):
    __tablename__ = "recording_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    team_id = Column(UUID(as_uuid=True), nullable=False)

    # Provider discriminator: "zoom" | "teams"
    provider = Column(String(20), nullable=False)

    # OAuth tokens (Fernet encrypted JSON blob: {access_token, refresh_token, expires_at})
    encrypted_tokens = Column(Text, nullable=False)

    # Provider account info
    provider_user_id = Column(String(255), nullable=False)
    provider_email = Column(String(320), nullable=False)

    # User preferences
    auto_import = Column(Boolean, default=True, nullable=False)
    recording_preference = Column(String(20), default="auto", nullable=False)

    # Teams-specific (nullable for Zoom rows)
    tenant_id = Column(String(255), nullable=True)
    subscription_id = Column(String(255), nullable=True)
    subscription_expiry = Column(DateTime(timezone=True), nullable=True)
    admin_consent_granted = Column(Boolean, default=False, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_recording_connections_user_provider", "user_id", "provider", unique=True),
        Index("ix_recording_connections_email", "provider_email"),
        Index("ix_recording_connections_team", "team_id"),
        Index("ix_recording_connections_tenant", "tenant_id"),
    )
