"""
Auth audit log model.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_uuid, utcnow


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True  # Portable: IPv4 + IPv6
    )
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="{}"  # JSON string for portability
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
