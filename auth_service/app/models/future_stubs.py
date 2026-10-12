"""
Future stub tables — MFA, SSO, API Keys.
Empty tables created in initial migration. Zero cost, signals extensibility.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_uuid, utcnow


class MFAMethod(Base):
    """Multi-factor authentication methods (Phase 4+)."""

    __tablename__ = "mfa_methods"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    method_type: Mapped[str] = mapped_column(
        String(20), nullable=False  # 'totp', 'sms', 'webauthn'
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_enc: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    credential_id: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    public_key: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True  # JSON string for portability
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SSOConnection(Base):
    """Enterprise SSO connections (SAML/OIDC)."""

    __tablename__ = "sso_connections"
    __table_args__ = (
        UniqueConstraint("team_id", "domain", name="uq_sso_team_domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(
        String(20), nullable=False  # 'saml', 'oidc'
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class APIKey(Base):
    """Programmatic API access keys."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="[]"  # JSON array string for portability
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
