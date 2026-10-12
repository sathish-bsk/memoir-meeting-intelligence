"""
FastAPI dependencies — DB session, Redis, current user extraction.
"""

import uuid
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db


# ─── Redis singleton ──────────────────────────────────────────────
_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Dependency: returns the shared Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _redis_pool


async def close_redis():
    """Shutdown: close the Redis pool."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


# ─── Request ID ───────────────────────────────────────────────────
def get_request_id(request: Request) -> str:
    """Extract or generate a request ID for tracing."""
    return request.headers.get("X-Request-ID", str(uuid.uuid4()))


# ─── Internal service key verification ────────────────────────────
async def verify_internal_call(
    x_service_key: Optional[str] = Header(default=None, alias="X-Service-Key"),
):
    """Verify that the caller is an internal service."""
    if not x_service_key:
        raise HTTPException(403, "Missing service key")
    if not settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(500, "Internal service key not configured")
    if x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid service key")


# ─── Current user from ForwardAuth headers ────────────────────────
class CurrentUser:
    """User context extracted from ForwardAuth X-User-* headers."""

    def __init__(
        self,
        user_id: str,
        email: str,
        name: str,
        team_id: Optional[str] = None,
        team_role: Optional[str] = None,
        plan: Optional[str] = None,
        is_super_admin: bool = False,
        scope_overrides: Optional[str] = None,
    ):
        self.user_id = user_id
        self.email = email
        self.name = name
        self.team_id = team_id
        self.team_role = team_role
        self.plan = plan
        self.is_super_admin = is_super_admin
        self.scope_overrides = scope_overrides


async def get_current_user(request: Request) -> CurrentUser:
    """Extract current user from X-User-* headers set by ForwardAuth.

    These headers are set by Traefik after calling /auth/verify.
    For direct calls (bypassing Traefik), falls back to JWT in Authorization header.
    """
    user_id = request.headers.get("X-User-ID")

    if not user_id:
        # Fallback: decode JWT directly (for direct service calls or tests)
        from app.core.security import decode_access_token

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Not authenticated")

        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload is None:
            raise HTTPException(401, "Invalid or expired token")

        return CurrentUser(
            user_id=payload["sub"],
            email=payload.get("email", ""),
            name=payload.get("name", ""),
            team_id=payload.get("team_id"),
            team_role=payload.get("team_role"),
            plan=payload.get("plan"),
            is_super_admin=bool(payload.get("is_super_admin")),
            scope_overrides=payload.get("so"),
        )

    return CurrentUser(
        user_id=user_id,
        email=request.headers.get("X-User-Email", ""),
        name=request.headers.get("X-User-Name", ""),
        team_id=request.headers.get("X-Team-ID"),
        team_role=request.headers.get("X-Team-Role"),
        plan=request.headers.get("X-Plan"),
        is_super_admin=request.headers.get("X-Super-Admin", "").lower() == "true",
        scope_overrides=request.headers.get("X-Scope-Overrides"),
    )


def require_role(min_role: str):
    """Dependency factory: require a minimum team role.

    Deprecated: prefer require_scope() for fine-grained checks.
    """
    ROLE_HIERARCHY = {"member": 0, "admin": 1, "owner": 2}

    async def check(user: CurrentUser = Depends(get_current_user)):
        user_level = ROLE_HIERARCHY.get(user.team_role, -1)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if user_level < required_level:
            raise HTTPException(403, f"Requires {min_role} role or above")
        return user

    return check


def require_scope(scope: str):
    """Dependency factory: require a specific scope based on team role + overrides.

    Reads the user's team_role and scope_overrides and checks against effective scopes.
    No DB call needed — pure role-to-scope mapping with overrides.
    """
    from app.core.permissions import has_scope

    async def check(user: CurrentUser = Depends(get_current_user)):
        if not has_scope(user.team_role, scope, user.scope_overrides):
            raise HTTPException(403, f"Requires {scope} permission")
        return user

    return check


async def require_super_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Dependency: require the user to be a super admin."""
    if not user.is_super_admin:
        raise HTTPException(403, "Requires super admin access")
    return user
