"""
CRM integration model — CrmConnection for OAuth tokens and configuration.
Table lives in shared videoclip_db; migrations managed by core_api.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class CrmConnection(Base):
    __tablename__ = "crm_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    provider = Column(String(20), nullable=False)  # "hubspot" | "zoho"

    # OAuth tokens (Fernet encrypted JSON blob: {access_token, refresh_token, expires_at})
    encrypted_tokens = Column(Text, nullable=False)

    # Provider-specific account info
    provider_account_id = Column(String(255), nullable=True)
    provider_account_name = Column(String(255), nullable=True)
    api_domain = Column(String(255), nullable=True)       # Zoho only
    accounts_url = Column(String(255), nullable=True)      # Zoho only

    # Configuration
    field_mapping = Column(JSONB, default=dict, nullable=False)
    stage_mapping = Column(JSONB, default=dict, nullable=False)
    auto_push_enabled = Column(Boolean, default=True, nullable=False)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    connection_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_crm_connections_team", "team_id"),
    )
