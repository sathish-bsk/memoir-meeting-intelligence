"""
Refresh token and auth token (password reset, email verify) models.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import new_uuid, utcnow


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    device_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True  # Portable: supports IPv4 + IPv6
    )
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True  # NULL = active
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class AuthToken(Base):
    """Generic auth tokens: password reset, email verification, magic link."""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(
        String(50), nullable=False  # 'password_reset', 'email_verify', 'magic_link'
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="{}"  # JSON string for portability
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


# Avoid circular import
from app.models.user import User  # noqa: E402, F401
