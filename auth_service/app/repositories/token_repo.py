"""
Token repository — refresh tokens and auth tokens (password reset, email verify).
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token import AuthToken, RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, db: AsyncSession):
        super().__init__(RefreshToken, db)

    async def find_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def find_active_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def create_refresh_token(
        self,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        expires_at: datetime,
        team_id: Optional[uuid.UUID] = None,
        device_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            team_id=team_id,
            token_hash=token_hash,
            family_id=family_id,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        return await self.create(token)

    async def revoke(self, token_id: uuid.UUID) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke all tokens in a family (reuse detection). Returns count revoked."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke all tokens for a user (password change, logout all)."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount

    async def find_active_sessions(self, user_id: uuid.UUID) -> List[RefreshToken]:
        """List active sessions for a user (for session management UI)."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .order_by(RefreshToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_last_used(
        self, token_id: uuid.UUID, ip_address: Optional[str] = None
    ) -> None:
        values = {"last_used_at": datetime.now(timezone.utc)}
        if ip_address:
            values["ip_address"] = ip_address
        await self.db.execute(
            update(RefreshToken).where(RefreshToken.id == token_id).values(**values)
        )


class AuthTokenRepository(BaseRepository[AuthToken]):
    def __init__(self, db: AsyncSession):
        super().__init__(AuthToken, db)

    async def find_active_by_hash(
        self, token_hash: str, purpose: str
    ) -> Optional[AuthToken]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == token_hash,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def create_auth_token(
        self,
        email: str,
        token_hash: str,
        purpose: str,
        expires_at: datetime,
        user_id: Optional[uuid.UUID] = None,
        metadata_json: Optional[str] = None,
    ) -> AuthToken:
        token = AuthToken(
            user_id=user_id,
            email=email,
            token_hash=token_hash,
            purpose=purpose,
            expires_at=expires_at,
            metadata_json=metadata_json,
        )
        return await self.create(token)

    async def mark_used(self, token_id: uuid.UUID) -> None:
        await self.db.execute(
            update(AuthToken)
            .where(AuthToken.id == token_id)
            .values(used_at=datetime.now(timezone.utc))
        )

    async def invalidate_previous(self, email: str, purpose: str) -> None:
        """Invalidate all previous tokens for an email+purpose (e.g., new password reset)."""
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(AuthToken)
            .where(
                AuthToken.email == email,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
