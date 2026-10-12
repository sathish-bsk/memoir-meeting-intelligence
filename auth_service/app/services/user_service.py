"""
User profile and email verification logic.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_event
from app.core.password import (
    check_hibp,
    hash_password,
    validate_password,
    verify_password,
)
from app.core.tokens import generate_refresh_token, hash_token
from app.repositories.token_repo import AuthTokenRepository
from app.repositories.user_repo import UserRepository


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.auth_token_repo = AuthTokenRepository(db)

    async def get_profile(self, user_id: str):
        user = await self.user_repo.find_by_id_with_oauth(uuid.UUID(user_id))
        if not user:
            raise HTTPException(404, "User not found")
        return user

    async def update_profile(
        self,
        user_id: str,
        name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        onboarding_completed: Optional[bool] = None,
    ):
        user = await self.user_repo.update_profile(
            uuid.UUID(user_id), name=name, avatar_url=avatar_url,
            onboarding_completed=onboarding_completed,
        )
        if not user:
            raise HTTPException(404, "User not found")
        await self.db.commit()
        return user

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(404, "User not found")

        if not user.password_hash:
            raise HTTPException(400, "Account uses OAuth login only")

        if not verify_password(current_password, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")

        errors = validate_password(new_password, email=user.email, name=user.name)
        if errors:
            raise HTTPException(400, "; ".join(errors))

        if await check_hibp(new_password):
            raise HTTPException(400, "This password has appeared in a data breach")

        new_hash = hash_password(new_password)
        await self.user_repo.update_password(user.id, new_hash)
        await self.user_repo.increment_token_version(user.id)

        await log_event(
            self.db, "password_change", True,
            user_id=user_id, ip_address=ip_address, user_agent=user_agent,
        )
        await self.db.commit()

    async def forgot_password(
        self, email: str, ip_address: Optional[str] = None
    ) -> Optional[str]:
        """Create a password reset token. Returns token (to be sent via email).

        Always returns success (even if email not found) to prevent enumeration.
        """
        user = await self.user_repo.find_by_email(email)

        if not user:
            # Don't reveal whether email exists
            return None

        # Invalidate previous reset tokens
        await self.auth_token_repo.invalidate_previous(email, "password_reset")

        # Generate token
        raw_token = generate_refresh_token()
        token_hash = hash_token(raw_token)

        await self.auth_token_repo.create_auth_token(
            email=email,
            token_hash=token_hash,
            purpose="password_reset",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user_id=user.id,
        )

        await log_event(
            self.db, "password_reset_request", True,
            user_id=str(user.id), ip_address=ip_address,
        )
        await self.db.commit()

        return raw_token

    async def reset_password(
        self,
        token: str,
        new_password: str,
        ip_address: Optional[str] = None,
    ):
        """Reset password using a token from forgot_password."""
        token_hash = hash_token(token)
        auth_token = await self.auth_token_repo.find_active_by_hash(
            token_hash, "password_reset"
        )
        if not auth_token:
            raise HTTPException(400, "Invalid or expired reset token")

        errors = validate_password(new_password, email=auth_token.email)
        if errors:
            raise HTTPException(400, "; ".join(errors))

        if await check_hibp(new_password):
            raise HTTPException(400, "This password has appeared in a data breach")

        new_hash = hash_password(new_password)

        if auth_token.user_id:
            await self.user_repo.update_password(auth_token.user_id, new_hash)
            await self.user_repo.increment_token_version(auth_token.user_id)

        await self.auth_token_repo.mark_used(auth_token.id)

        await log_event(
            self.db, "password_reset_complete", True,
            user_id=str(auth_token.user_id) if auth_token.user_id else None,
            ip_address=ip_address,
        )
        await self.db.commit()

    async def create_email_verification_token(self, user_id: str, email: str) -> str:
        """Create an email verification token."""
        raw_token = generate_refresh_token()
        token_hash = hash_token(raw_token)

        await self.auth_token_repo.invalidate_previous(email, "email_verify")
        await self.auth_token_repo.create_auth_token(
            email=email,
            token_hash=token_hash,
            purpose="email_verify",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            user_id=uuid.UUID(user_id),
        )
        await self.db.commit()
        return raw_token

    async def verify_email(self, token: str, ip_address: Optional[str] = None):
        """Verify email address using token."""
        token_hash = hash_token(token)
        auth_token = await self.auth_token_repo.find_active_by_hash(
            token_hash, "email_verify"
        )
        if not auth_token:
            raise HTTPException(400, "Invalid or expired verification token")

        if auth_token.user_id:
            await self.user_repo.verify_email(auth_token.user_id)

        await self.auth_token_repo.mark_used(auth_token.id)

        await log_event(
            self.db, "email_verify", True,
            user_id=str(auth_token.user_id) if auth_token.user_id else None,
            ip_address=ip_address,
        )
        await self.db.commit()

    async def request_email_change(
        self,
        user_id: str,
        new_email: str,
        password: str,
        ip_address: Optional[str] = None,
    ) -> str:
        """Request email change. Verifies password, creates token. Returns raw token."""
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(404, "User not found")

        if not user.password_hash:
            raise HTTPException(400, "Account uses OAuth login only")

        if not verify_password(password, user.password_hash):
            raise HTTPException(400, "Incorrect password")

        # Check new email isn't already taken
        existing = await self.user_repo.find_by_email(new_email)
        if existing:
            raise HTTPException(409, "Email already in use")

        # Invalidate previous email change tokens
        await self.auth_token_repo.invalidate_previous(user.email, "email_change")

        raw_token = generate_refresh_token()
        token_hash = hash_token(raw_token)

        await self.auth_token_repo.create_auth_token(
            email=user.email,
            token_hash=token_hash,
            purpose="email_change",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            user_id=user.id,
            metadata_json=json.dumps({"new_email": new_email}),
        )

        await log_event(
            self.db, "email_change_request", True,
            user_id=user_id, ip_address=ip_address,
            metadata={"new_email": new_email},
        )
        await self.db.commit()

        return raw_token

    async def verify_email_change(self, token: str, ip_address: Optional[str] = None):
        """Verify email change using token. Updates user email."""
        token_hash = hash_token(token)
        auth_token = await self.auth_token_repo.find_active_by_hash(
            token_hash, "email_change"
        )
        if not auth_token:
            raise HTTPException(400, "Invalid or expired email change token")

        metadata = json.loads(auth_token.metadata_json or "{}")
        new_email = metadata.get("new_email")
        if not new_email:
            raise HTTPException(400, "Invalid email change token")

        # Check new email isn't already taken (could have been claimed since request)
        existing = await self.user_repo.find_by_email(new_email)
        if existing:
            raise HTTPException(409, "Email already in use")

        if auth_token.user_id:
            await self.user_repo.update_email(auth_token.user_id, new_email)

        await self.auth_token_repo.mark_used(auth_token.id)

        await log_event(
            self.db, "email_change_complete", True,
            user_id=str(auth_token.user_id) if auth_token.user_id else None,
            ip_address=ip_address,
            metadata={"new_email": new_email},
        )
        await self.db.commit()
