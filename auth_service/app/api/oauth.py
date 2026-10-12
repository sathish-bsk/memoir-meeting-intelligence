"""
OAuth routes — authorize redirect + callback for each provider.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import get_db
from app.dependencies import get_redis
from app.schemas.auth import TokenResponse
from app.services.oauth_service import OAuthService


router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_provider(provider: str):
    """Get OAuth provider implementation."""
    from app.providers.google import GoogleOAuthProvider
    from app.providers.microsoft import MicrosoftOAuthProvider

    providers = {
        "google": GoogleOAuthProvider,
        "microsoft": MicrosoftOAuthProvider,
    }
    cls = providers.get(provider)
    if not cls:
        raise HTTPException(400, f"Unsupported OAuth provider: {provider}")
    return cls()


@router.get("/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Get OAuth authorization URL. Frontend redirects user here."""
    oauth_provider = _get_provider(provider)
    url, state = await oauth_provider.get_authorize_url()

    # Store state in Redis for CSRF protection (5 min TTL)
    await redis.set(f"oauth_state:{state}", provider, ex=300)

    return {"redirect_url": url, "state": state}


@router.post("/{provider}/callback", response_model=TokenResponse)
async def oauth_callback(
    provider: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Handle OAuth callback — exchange code for tokens, create/link user."""
    body = await request.json()
    code = body.get("code")
    state = body.get("state")

    if not code:
        raise HTTPException(400, "Missing authorization code")

    # Verify state for CSRF protection
    if state:
        stored_provider = await redis.get(f"oauth_state:{state}")
        if stored_provider != provider:
            raise HTTPException(400, "Invalid OAuth state")
        await redis.delete(f"oauth_state:{state}")

    oauth_provider = _get_provider(provider)
    user_info = await oauth_provider.exchange_code(code)

    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    svc = OAuthService(db, redis)
    access_token, refresh_token, expires_in = await svc.handle_oauth_callback(
        provider=provider,
        provider_user_id=user_info["id"],
        provider_email=user_info["email"],
        provider_name=user_info.get("name", user_info["email"].split("@")[0]),
        email_verified=user_info.get("email_verified", False),
        raw_profile=user_info.get("raw_profile"),
        ip_address=ip,
        user_agent=ua,
    )

    # Set refresh token cookie
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
