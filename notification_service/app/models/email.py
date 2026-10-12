"""
Mirror models for email_templates and email_log tables.
No Alembic here — core_api owns the migrations.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    subject = Column(Text, nullable=False)
    html_body = Column(Text, nullable=False)
    description = Column(String(500), nullable=True)
    category = Column(String(50), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class EmailLog(Base):
    __tablename__ = "email_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    to_email = Column(String(320), nullable=False)
    template_name = Column(String(255), nullable=False)
    template_data = Column(JSONB, nullable=True)
    trigger_type = Column(String(50), nullable=False)
    triggered_by = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(50), nullable=False)
    via = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
