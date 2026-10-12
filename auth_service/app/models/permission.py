"""
Resource-level permission model — per-resource sharing (Google Docs pattern).

Each row grants one specific action to one subject on one resource.
Subject can be a 'user' or 'team' (for team-wide grants).
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_uuid, utcnow


class ResourcePermission(Base):
    __tablename__ = "resource_permissions"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "resource_id", "subject_type", "subject_id", "action",
            name="uq_resource_subject_action",
        ),
        Index("ix_resource_perms_subject", "subject_type", "subject_id"),
        Index("ix_resource_perms_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(
        String(50), nullable=False  # 'meeting', 'clip', 'project'
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(
        String(20), nullable=False  # 'user' or 'team'
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    action: Mapped[str] = mapped_column(
        String(50), nullable=False  # 'read', 'write', 'delete', 'share'
    )
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
