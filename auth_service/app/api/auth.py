"""
Auth routes — signup, login, logout, refresh, verify (ForwardAuth), switch-team.
"""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.dependencies import get_current_user, get_redis, get_request_id, CurrentUser
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshResponse,
    SignupRequest,
    SwitchTeamRequest,
    TokenResponse,
)
from app.schemas.common import MessageResponse
from app.services.auth_service import AuthService
from app.services.user_service import UserService


router = APIRouter(prefix="/auth", tags=["auth"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/signup", response_model=TokenResponse)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Register a new user with email and password."""
    svc = AuthService(db, redis)
    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    access_token, refresh_token, expires_in = await svc.signup(
        email=body.email,
        password=body.password,
        name=body.name,
        ip_address=ip,
        user_agent=ua,
    )

    # Set refresh token as httpOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )

    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Login with email and password."""
    svc = AuthService(db, redis)
    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    access_token, refresh_token, expires_in = await svc.login(
        email=body.email,
        password=body.password,
        ip_address=ip,
        user_agent=ua,
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )

    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    refresh_token: str = Cookie(default=None),
):
    """Refresh access token using refresh token from cookie."""
    if not refresh_token:
        raise HTTPException(401, "No refresh token provided")

    svc = AuthService(db, redis)
    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    access_token, new_refresh, expires_in = await svc.refresh(
        refresh_token_raw=refresh_token,
        ip_address=ip,
        user_agent=ua,
    )

    response.set_cookie(
        key="refresh_token",
        value=new_refresh,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )

    return RefreshResponse(access_token=access_token, expires_in=expires_in)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    body: LogoutRequest = LogoutRequest(),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: CurrentUser = Depends(get_current_user),
):
    """Logout — revoke refresh token."""
    svc = AuthService(db, redis)
    ip = _get_client_ip(request)

    await svc.logout(
        user_id=user.user_id,
        refresh_token_raw=body.refresh_token,
        ip_address=ip,
    )

    # Clear refresh token cookie
    response.delete_cookie(
        key="refresh_token",
        path="/auth/refresh",
    )

    return MessageResponse(message="Logged out successfully")


@router.get("/verify-email", response_model=MessageResponse)
async def verify_email(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address using the token sent via email."""
    svc = UserService(db)
    ip = _get_client_ip(request)
    await svc.verify_email(token=token, ip_address=ip)
    return MessageResponse(message="Email verified successfully")


@router.get("/verify-email-change", response_model=MessageResponse)
async def verify_email_change(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verify email change using the token sent to the new email."""
    svc = UserService(db)
    ip = _get_client_ip(request)
    await svc.verify_email_change(token=token, ip_address=ip)
    return MessageResponse(message="Email changed successfully")


@router.post("/switch-team", response_model=TokenResponse)
async def switch_team(
    body: SwitchTeamRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: CurrentUser = Depends(get_current_user),
):
    """Switch active team — re-issues access + refresh tokens."""
    svc = AuthService(db, redis)
    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    access_token, refresh_token, expires_in = await svc.switch_team(
        user_id=user.user_id,
        team_id=body.team_id,
        ip_address=ip,
        user_agent=ua,
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth/refresh",
    )

    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.get("/verify")
async def verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """ForwardAuth endpoint — called by Traefik to validate JWT.

    Returns 200 with X-User-* headers on success, 401 on failure.
    Traefik copies the response headers onto the original request.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = auth_header[7:]
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(401, "Invalid or expired token")

    # Check token_version and JTI blacklist via Redis (best-effort)
    # If Redis is unavailable, we trust the JWT claims (checked on refresh)
    user_id = payload.get("sub")
    tv = payload.get("tv", 0)

    try:
        cached_tv = await redis.get(f"tv:{user_id}")
        if cached_tv is not None:
            if tv < int(cached_tv):
                raise HTTPException(401, "Token has been revoked")

        jti = payload.get("jti")
        if jti and await redis.exists(f"blacklist:{jti}"):
            raise HTTPException(401, "Token has been revoked")
    except HTTPException:
        raise
    except Exception:
        # Redis unavailable — fail open, JWT signature is still valid
        pass

    # Derive scopes from team role + overrides
    from app.core.permissions import get_effective_scopes

    team_role = payload.get("team_role", "")
    scope_overrides = payload.get("so")
    scopes = get_effective_scopes(team_role, scope_overrides) if team_role else frozenset()
    scopes_header = ",".join(sorted(scopes)) if scopes else ""

    # Return user context as headers — Traefik copies these to the upstream request
    headers = {
        "X-User-ID": payload.get("sub", ""),
        "X-User-Email": payload.get("email", ""),
        "X-User-Name": payload.get("name", ""),
        "X-Team-ID": payload.get("team_id", ""),
        "X-Team-Role": team_role,
        "X-Plan": payload.get("plan", ""),
        "X-User-Scopes": scopes_header,
        "X-Onboarding-Completed": str(payload.get("onboarding_completed", False)).lower(),
    }
    if scope_overrides:
        headers["X-Scope-Overrides"] = scope_overrides
    if payload.get("is_super_admin"):
        headers["X-Super-Admin"] = "true"

    return Response(status_code=200, headers=headers)
