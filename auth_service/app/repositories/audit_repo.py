"""
Audit log repository.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuthAuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuthAuditLog]):
    def __init__(self, db: AsyncSession):
        super().__init__(AuthAuditLog, db)

    async def get_events_for_user(
        self,
        user_id: uuid.UUID,
        event_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AuthAuditLog]:
        query = select(AuthAuditLog).where(AuthAuditLog.user_id == user_id)
        if event_type:
            query = query.where(AuthAuditLog.event_type == event_type)
        query = query.order_by(AuthAuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_recent_failures(
        self,
        user_id: uuid.UUID,
        event_type: str = "login_failed",
        since: Optional[datetime] = None,
    ) -> int:
        """Count recent failures for a user — useful for security alerts."""
        query = (
            select(AuthAuditLog)
            .where(
                AuthAuditLog.user_id == user_id,
                AuthAuditLog.event_type == event_type,
                AuthAuditLog.success == False,
            )
        )
        if since:
            query = query.where(AuthAuditLog.created_at >= since)
        result = await self.db.execute(query)
        return len(list(result.scalars().all()))
