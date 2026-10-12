"""
Session management — list, revoke active sessions.
"""

import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_event
from app.repositories.token_repo import RefreshTokenRepository


class SessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.token_repo = RefreshTokenRepository(db)

    async def list_sessions(
        self,
        user_id: str,
        current_token_hash: Optional[str] = None,
    ) -> List[dict]:
        """List all active sessions for a user."""
        tokens = await self.token_repo.find_active_sessions(uuid.UUID(user_id))

        sessions = []
        for t in tokens:
            sessions.append({
                "id": str(t.id),
                "device_name": t.device_name,
                "ip_address": t.ip_address,
                "last_used_at": t.last_used_at,
                "created_at": t.created_at,
                "is_current": (
                    t.token_hash == current_token_hash if current_token_hash else False
                ),
            })
        return sessions

    async def revoke_session(
        self,
        session_id: str,
        user_id: str,
        ip_address: Optional[str] = None,
    ):
        """Revoke a specific session."""
        token = await self.token_repo.get_by_id(uuid.UUID(session_id))
        if not token or str(token.user_id) != user_id:
            raise HTTPException(404, "Session not found")

        if token.revoked_at is not None:
            return  # Already revoked

        await self.token_repo.revoke(token.id)

        await log_event(
            self.db, "session_revoke", True,
            user_id=user_id, ip_address=ip_address,
            metadata={"session_id": session_id},
        )
        await self.db.commit()

    async def revoke_all_other_sessions(
        self,
        user_id: str,
        current_token_hash: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        """Revoke all sessions except the current one."""
        tokens = await self.token_repo.find_active_sessions(uuid.UUID(user_id))

        for t in tokens:
            if current_token_hash and t.token_hash == current_token_hash:
                continue  # Keep current session
            await self.token_repo.revoke(t.id)

        await log_event(
            self.db, "session_revoke", True,
            user_id=user_id, ip_address=ip_address,
            metadata={"action": "revoke_all_others"},
        )
        await self.db.commit()
