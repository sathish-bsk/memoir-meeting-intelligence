"""
OAuth flow orchestration — login, signup, account linking.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_event
from app.core.config import settings
from app.core.security import create_access_token
from app.core.tokens import generate_refresh_token, hash_token
from app.repositories.team_repo import TeamMemberRepository, TeamRepository
from app.repositories.token_repo import RefreshTokenRepository
from app.repositories.user_repo import OAuthAccountRepository, UserRepository


class OAuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis
        self.user_repo = UserRepository(db)
        self.oauth_repo = OAuthAccountRepository(db)
        self.token_repo = RefreshTokenRepository(db)
        self.team_repo = TeamRepository(db)
        self.member_repo = TeamMemberRepository(db)

    async def handle_oauth_callback(
        self,
        provider: str,
        provider_user_id: str,
        provider_email: str,
        provider_name: str,
        email_verified: bool,
        access_token_enc: Optional[bytes] = None,
        refresh_token_enc: Optional[bytes] = None,
        token_expires_at: Optional[datetime] = None,
        raw_profile: Optional[dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Handle OAuth callback — login, signup, or link.

        Rules:
        - If OAuth account exists → login
        - If email matches existing user AND email is verified → auto-link + login
        - If email matches but NOT verified → reject (prevent account takeover)
        - If no match → create new user + link
        """
        # Check if this OAuth account already exists
        existing_oauth = await self.oauth_repo.find_by_provider(
            provider, provider_user_id
        )

        if existing_oauth:
            # Existing OAuth account — login
            user = await self.user_repo.get_by_id(existing_oauth.user_id)
            if not user or not user.is_active:
                raise HTTPException(403, "Account is deactivated")

            await self.user_repo.update_last_login(user.id)
            membership = await self.member_repo.get_first_team_for_user(user.id)

            plan = None
            if membership:
                team = await self.team_repo.get_by_id(membership.team_id)
                plan = team.plan if team else None

            access_token, refresh_token, expires_in = await self._issue_tokens(
                user=user,
                team_id=str(membership.team_id) if membership else None,
                team_role=membership.role if membership else None,
                plan=plan,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            await log_event(
                self.db, "oauth_login", True,
                user_id=str(user.id), ip_address=ip_address,
                metadata={"provider": provider},
            )
            await self.db.commit()
            return access_token, refresh_token, expires_in

        # Check if email matches an existing user
        existing_user = await self.user_repo.find_by_email(provider_email)

        if existing_user:
            if not email_verified:
                raise HTTPException(
                    400,
                    "Cannot link OAuth account: provider email is not verified"
                )

            # Auto-link OAuth account to existing user
            await self.oauth_repo.create_oauth_account(
                user_id=existing_user.id,
                provider=provider,
                provider_user_id=provider_user_id,
                provider_email=provider_email,
                access_token_enc=access_token_enc,
                refresh_token_enc=refresh_token_enc,
                token_expires_at=token_expires_at,
                raw_profile=json.dumps(raw_profile) if raw_profile else None,
            )

            await self.user_repo.update_last_login(existing_user.id)
            membership = await self.member_repo.get_first_team_for_user(existing_user.id)

            plan = None
            if membership:
                team = await self.team_repo.get_by_id(membership.team_id)
                plan = team.plan if team else None

            access_token, refresh_token, expires_in = await self._issue_tokens(
                user=existing_user,
                team_id=str(membership.team_id) if membership else None,
                team_role=membership.role if membership else None,
                plan=plan,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            await log_event(
                self.db, "oauth_link", True,
                user_id=str(existing_user.id), ip_address=ip_address,
                metadata={"provider": provider},
            )
            await self.db.commit()
            return access_token, refresh_token, expires_in

        # New user — create account
        user = await self.user_repo.create_user(
            email=provider_email,
            name=provider_name,
            email_verified=email_verified,
        )

        await self.oauth_repo.create_oauth_account(
            user_id=user.id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_email=provider_email,
            access_token_enc=access_token_enc,
            refresh_token_enc=refresh_token_enc,
            token_expires_at=token_expires_at,
            raw_profile=json.dumps(raw_profile) if raw_profile else None,
        )

        # Create placeholder team in auth_db (required for JWT team_id claim
        # and refresh token chain). Onboarding will rename it with the real
        # company name via core_api's createTeam upsert.
        slug = provider_email.split("@")[0].lower().replace(".", "-")
        base_slug = slug
        counter = 0
        while await self.team_repo.find_by_slug(slug):
            counter += 1
            slug = f"{base_slug}-{counter}"

        team = await self.team_repo.create_team(
            name=f"{provider_name}'s Team", slug=slug, created_by=user.id
        )
        team.plan = "trial"
        team.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
        await self.db.flush()

        await self.member_repo.add_member(
            team_id=team.id, user_id=user.id, role="owner"
        )

        access_token, refresh_token, expires_in = await self._issue_tokens(
            user=user,
            team_id=str(team.id),
            team_role="owner",
            plan=team.plan,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        await log_event(
            self.db, "signup", True,
            user_id=str(user.id), ip_address=ip_address,
            metadata={"provider": provider, "method": "oauth"},
        )
        await self.db.commit()
        return access_token, refresh_token, expires_in

    async def _issue_tokens(
        self, user, team_id=None, team_role=None,
        plan=None, ip_address=None, user_agent=None,
    ) -> Tuple[str, str, int]:
        access_token = create_access_token(
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            token_version=user.token_version,
            team_id=team_id,
            team_role=team_role,
            plan=plan,
            onboarding_completed=user.onboarding_completed,
            is_super_admin=user.is_super_admin,
        )

        refresh_raw = generate_refresh_token()
        refresh_hash = hash_token(refresh_raw)

        await self.token_repo.create_refresh_token(
            user_id=user.id,
            token_hash=refresh_hash,
            family_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
            team_id=uuid.UUID(team_id) if team_id else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return access_token, refresh_raw, settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
