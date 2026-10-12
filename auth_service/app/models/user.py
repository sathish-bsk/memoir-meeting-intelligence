"""
User and OAuth account models.
"""

import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, new_uuid, utcnow


# Use String(36) for UUID portability across databases.
# For PostgreSQL, SQLAlchemy auto-maps uuid.UUID to native UUID type.
# For other DBs, it falls back to CHAR(36).


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True  # NULL for OAuth-only users
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    is_super_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Relationships
    oauth_accounts: Mapped[List["OAuthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OAuthAccount(Base, TimestampMixin):
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    access_token_enc: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    refresh_token_enc: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_profile: Mapped[Optional[dict]] = mapped_column(
        Text, nullable=True  # Store as JSON string for portability
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="oauth_accounts")


# Import here to avoid circular — RefreshToken is in token.py
from app.models.token import RefreshToken  # noqa: E402, F401
