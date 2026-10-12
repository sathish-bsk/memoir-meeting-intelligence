"""
User repository — all user-related DB operations.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User, OAuthAccount
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def find_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.oauth_accounts))
            .where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def find_by_id_with_oauth(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(
            select(User)
            .options(selectinload(User.oauth_accounts))
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def create_user(
        self,
        email: str,
        name: str,
        password_hash: Optional[str] = None,
        email_verified: bool = False,
    ) -> User:
        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            email_verified=email_verified,
        )
        return await self.create(user)

    async def update_last_login(self, user_id: uuid.UUID) -> None:
        await self.db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=datetime.now(timezone.utc))
        )

    async def increment_token_version(self, user_id: uuid.UUID) -> int:
        """Increment token_version to invalidate all existing tokens. Returns new version."""
        result = await self.db.execute(
            select(User.token_version).where(User.id == user_id)
        )
        current = result.scalar_one()
        new_version = current + 1
        await self.db.execute(
            update(User)
            .where(User.id == user_id)
            .values(token_version=new_version)
        )
        return new_version

    async def update_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        await self.db.execute(
            update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash)
        )

    async def verify_email(self, user_id: uuid.UUID) -> None:
        await self.db.execute(
            update(User)
            .where(User.id == user_id)
            .values(email_verified=True)
        )

    async def update_email(self, user_id: uuid.UUID, new_email: str) -> None:
        await self.db.execute(
            update(User)
            .where(User.id == user_id)
            .values(email=new_email, email_verified=True, updated_at=datetime.now(timezone.utc))
        )

    async def update_profile(
        self,
        user_id: uuid.UUID,
        name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        onboarding_completed: Optional[bool] = None,
    ) -> Optional[User]:
        values = {}
        if name is not None:
            values["name"] = name
        if avatar_url is not None:
            values["avatar_url"] = avatar_url
        if onboarding_completed is not None:
            values["onboarding_completed"] = onboarding_completed
        if values:
            values["updated_at"] = datetime.now(timezone.utc)
            await self.db.execute(
                update(User).where(User.id == user_id).values(**values)
            )
        return await self.find_by_id_with_oauth(user_id)

    async def find_users_by_ids(self, user_ids: List[uuid.UUID]) -> List[User]:
        result = await self.db.execute(
            select(User).where(User.id.in_(user_ids))
        )
        return list(result.scalars().all())

    async def deactivate(self, user_id: uuid.UUID) -> None:
        await self.db.execute(
            update(User).where(User.id == user_id).values(is_active=False)
        )


class OAuthAccountRepository(BaseRepository[OAuthAccount]):
    def __init__(self, db: AsyncSession):
        super().__init__(OAuthAccount, db)

    async def find_by_provider(
        self, provider: str, provider_user_id: str
    ) -> Optional[OAuthAccount]:
        result = await self.db.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_user_id == provider_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_user_and_provider(
        self, user_id: uuid.UUID, provider: str
    ) -> Optional[OAuthAccount]:
        result = await self.db.execute(
            select(OAuthAccount).where(
                OAuthAccount.user_id == user_id,
                OAuthAccount.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    async def create_oauth_account(
        self,
        user_id: uuid.UUID,
        provider: str,
        provider_user_id: str,
        provider_email: Optional[str] = None,
        access_token_enc: Optional[bytes] = None,
        refresh_token_enc: Optional[bytes] = None,
        token_expires_at=None,
        raw_profile: Optional[str] = None,
    ) -> OAuthAccount:
        account = OAuthAccount(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_email=provider_email,
            access_token_enc=access_token_enc,
            refresh_token_enc=refresh_token_enc,
            token_expires_at=token_expires_at,
            raw_profile=raw_profile,
        )
        return await self.create(account)
