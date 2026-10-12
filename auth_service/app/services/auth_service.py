"""
Authentication orchestration — signup, login, refresh, logout.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from user_agents import parse as parse_ua

from app.core.audit import log_event
from app.core.config import settings
from app.core.logging import app_logger
from app.core.password import (
    check_hibp,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.core.rate_limit import (
    apply_delay,
    check_rate_limit,
    record_attempt,
    reset_rate_limit,
)
from app.core.security import create_access_token
from app.core.tokens import generate_refresh_token, hash_token
from app.repositories.team_repo import TeamMemberRepository, TeamRepository
from app.repositories.token_repo import RefreshTokenRepository
from app.repositories.user_repo import UserRepository

TRIAL_DURATION_DAYS = 14


class AuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis
        self.user_repo = UserRepository(db)
        self.token_repo = RefreshTokenRepository(db)
        self.team_repo = TeamRepository(db)
        self.member_repo = TeamMemberRepository(db)

    async def signup(
        self,
        email: str,
        password: str,
        name: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Register a new user. Returns (access_token, refresh_token, expires_in)."""

        # Check if email already exists
        existing = await self.user_repo.find_by_email(email)
        if existing:
            raise HTTPException(409, "Email already registered")

        # Validate password (NIST 800-63B)
        errors = validate_password(password, email=email, name=name)
        if errors:
            raise HTTPException(400, detail="; ".join(errors))

        # Check HaveIBeenPwned
        if await check_hibp(password):
            raise HTTPException(
                400,
                "This password has appeared in a data breach. Please choose a different password.",
            )

        # Create user
        password_hash = hash_password(password)
        user = await self.user_repo.create_user(
            email=email, name=name, password_hash=password_hash
        )

        # Create default personal team
        slug = email.split("@")[0].lower().replace(".", "-")
        # Ensure slug uniqueness
        base_slug = slug
        counter = 0
        while await self.team_repo.find_by_slug(slug):
            counter += 1
            slug = f"{base_slug}-{counter}"

        team = await self.team_repo.create_team(
            name=f"{name}'s Team", slug=slug, created_by=user.id
        )
        # Set trial plan on new team
        team.plan = "trial"
        team.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=TRIAL_DURATION_DAYS)
        await self.db.flush()

        await self.member_repo.add_member(
            team_id=team.id, user_id=user.id, role="owner"
        )

        # Issue tokens
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
            user_id=str(user.id), ip_address=ip_address, user_agent=user_agent,
        )
        await self.db.commit()

        # Send verification email (non-blocking — user can still use the app)
        try:
            from app.services.user_service import UserService
            from app.clients import notification_client

            user_svc = UserService(self.db)
            verification_token = await user_svc.create_email_verification_token(
                user_id=str(user.id), email=user.email
            )
            await notification_client.send_verification_email(
                email=user.email, name=user.name, token=verification_token
            )
        except Exception as e:
            app_logger.warning(f"Failed to send verification email on signup: {e}")

        return access_token, refresh_token, expires_in

    async def login(
        self,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Authenticate user with email + password. Returns (access_token, refresh_token, expires_in)."""

        # Check rate limits (per email AND per IP)
        email_delay = await check_rate_limit(self.redis, f"login:email:{email}")
        ip_delay = await check_rate_limit(self.redis, f"login:ip:{ip_address}")
        delay = max(email_delay or 0, ip_delay or 0)
        await apply_delay(delay)

        user = await self.user_repo.find_by_email(email)
        if not user or not user.password_hash:
            # Record failed attempt
            await record_attempt(self.redis, f"login:email:{email}")
            if ip_address:
                await record_attempt(self.redis, f"login:ip:{ip_address}")
            await log_event(
                self.db, "login_failed", False,
                ip_address=ip_address, user_agent=user_agent,
                metadata={"email": email, "reason": "user_not_found"},
            )
            await self.db.commit()
            raise HTTPException(401, "Invalid email or password")

        if not user.is_active:
            raise HTTPException(403, "Account is deactivated")

        if not verify_password(password, user.password_hash):
            await record_attempt(self.redis, f"login:email:{email}")
            if ip_address:
                await record_attempt(self.redis, f"login:ip:{ip_address}")
            await log_event(
                self.db, "login_failed", False,
                user_id=str(user.id), ip_address=ip_address, user_agent=user_agent,
                metadata={"reason": "wrong_password"},
            )
            await self.db.commit()
            raise HTTPException(401, "Invalid email or password")

        # Rehash if params changed
        if needs_rehash(user.password_hash):
            await self.user_repo.update_password(user.id, hash_password(password))

        # Reset rate limit on success
        await reset_rate_limit(self.redis, f"login:email:{email}")

        # Get user's default team
        membership = await self.member_repo.get_first_team_for_user(user.id)
        team_id = str(membership.team_id) if membership else None
        team_role = membership.role if membership else None
        scope_overrides = membership.scope_overrides if membership else None

        # Get team plan
        plan = None
        if membership:
            team = await self.team_repo.get_by_id(membership.team_id)
            if team:
                plan = team.plan

        # Issue tokens
        access_token, refresh_token, expires_in = await self._issue_tokens(
            user=user,
            team_id=team_id,
            team_role=team_role,
            plan=plan,
            ip_address=ip_address,
            user_agent=user_agent,
            scope_overrides=scope_overrides,
        )

        await self.user_repo.update_last_login(user.id)
        await log_event(
            self.db, "login_success", True,
            user_id=str(user.id), ip_address=ip_address, user_agent=user_agent,
        )
        await self.db.commit()

        return access_token, refresh_token, expires_in

    async def refresh(
        self,
        refresh_token_raw: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Rotate refresh token and issue new access token.

        Implements family-based reuse detection:
        - Each refresh creates a new token and revokes the old one
        - If a revoked token is reused, the entire family is revoked (token theft)
        """
        token_hash = hash_token(refresh_token_raw)

        # First, check if this is a reuse of a revoked token
        existing = await self.token_repo.find_by_hash(token_hash)
        if existing and existing.revoked_at is not None:
            # REUSE DETECTED — revoke entire family
            app_logger.warning(
                f"Refresh token reuse detected for family {existing.family_id}"
            )
            revoked_count = await self.token_repo.revoke_family(existing.family_id)
            await log_event(
                self.db, "token_family_revoke", True,
                user_id=str(existing.user_id),
                ip_address=ip_address,
                metadata={"family_id": str(existing.family_id), "revoked_count": revoked_count},
            )
            await self.db.commit()
            raise HTTPException(401, "Token reuse detected. All sessions revoked for security.")

        # Find active token
        active_token = await self.token_repo.find_active_by_hash(token_hash)
        if not active_token:
            raise HTTPException(401, "Invalid or expired refresh token")

        # Load user
        user = await self.user_repo.get_by_id(active_token.user_id)
        if not user or not user.is_active:
            raise HTTPException(401, "User not found or inactive")

        # Revoke old token
        await self.token_repo.revoke(active_token.id)

        # Get team context
        team_id = str(active_token.team_id) if active_token.team_id else None
        team_role = None
        scope_overrides = None
        if active_token.team_id:
            membership = await self.member_repo.find_membership(
                active_token.team_id, user.id
            )
            team_role = membership.role if membership else None
            scope_overrides = membership.scope_overrides if membership else None

        # Issue new token pair (same family)
        new_refresh = generate_refresh_token()
        new_hash = hash_token(new_refresh)
        device_name = _parse_device(user_agent)

        await self.token_repo.create_refresh_token(
            user_id=user.id,
            token_hash=new_hash,
            family_id=active_token.family_id,  # Same family!
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
            team_id=active_token.team_id,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Get team plan
        plan = None
        if active_token.team_id:
            team = await self.team_repo.get_by_id(active_token.team_id)
            if team:
                plan = team.plan

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
            scope_overrides=scope_overrides,
        )
        expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

        await log_event(
            self.db, "token_refresh", True,
            user_id=str(user.id), ip_address=ip_address,
        )
        await self.db.commit()

        return access_token, new_refresh, expires_in

    async def logout(
        self,
        user_id: str,
        refresh_token_raw: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        """Revoke a specific refresh token or all tokens for the user."""
        if refresh_token_raw:
            token_hash = hash_token(refresh_token_raw)
            token = await self.token_repo.find_active_by_hash(token_hash)
            if token and str(token.user_id) == user_id:
                await self.token_repo.revoke(token.id)
        else:
            await self.token_repo.revoke_all_for_user(uuid.UUID(user_id))

        await log_event(
            self.db, "logout", True,
            user_id=user_id, ip_address=ip_address,
        )
        await self.db.commit()

    async def switch_team(
        self,
        user_id: str,
        team_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Switch active team context — re-issues token pair."""
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(404, "User not found")

        membership = await self.member_repo.find_membership(
            uuid.UUID(team_id), user.id
        )
        if not membership:
            raise HTTPException(403, "Not a member of this team")

        # Get team plan
        team = await self.team_repo.get_by_id(uuid.UUID(team_id))
        plan = team.plan if team else None

        access_token, refresh_token, expires_in = await self._issue_tokens(
            user=user,
            team_id=team_id,
            team_role=membership.role,
            plan=plan,
            ip_address=ip_address,
            user_agent=user_agent,
            scope_overrides=membership.scope_overrides,
        )
        await self.db.commit()
        return access_token, refresh_token, expires_in

    # ─── Private helpers ──────────────────────────────────────────

    async def _issue_tokens(
        self,
        user,
        team_id: Optional[str] = None,
        team_role: Optional[str] = None,
        plan: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        scope_overrides: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Create access + refresh token pair."""
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
            scope_overrides=scope_overrides,
        )

        refresh_raw = generate_refresh_token()
        refresh_hash = hash_token(refresh_raw)
        family_id = uuid.uuid4()
        device_name = _parse_device(user_agent)

        await self.token_repo.create_refresh_token(
            user_id=user.id,
            token_hash=refresh_hash,
            family_id=family_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
            team_id=uuid.UUID(team_id) if team_id else None,
            device_name=device_name,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
        return access_token, refresh_raw, expires_in


def _parse_device(user_agent: Optional[str]) -> Optional[str]:
    """Parse user agent string into a human-readable device name."""
    if not user_agent:
        return None
    try:
        ua = parse_ua(user_agent)
        return f"{ua.browser.family} on {ua.os.family}"
    except Exception:
        return None
