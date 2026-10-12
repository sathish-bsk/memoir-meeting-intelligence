"""
User profile routes — GET/PATCH /auth/me, POST /auth/change-email, POST /auth/resend-verification.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_current_user, CurrentUser
from app.schemas.common import MessageResponse
from app.schemas.user import ChangeEmailRequest, UpdateProfileRequest, UserProfile
from app.services.user_service import UserService


router = APIRouter(prefix="/auth", tags=["profile"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/me", response_model=UserProfile)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Get current user's profile."""
    svc = UserService(db)
    db_user = await svc.get_profile(user.user_id)

    return UserProfile(
        id=str(db_user.id),
        email=db_user.email,
        email_verified=db_user.email_verified,
        name=db_user.name,
        avatar_url=db_user.avatar_url,
        is_active=db_user.is_active,
        has_password=db_user.password_hash is not None,
        onboarding_completed=db_user.onboarding_completed,
        oauth_providers=[oa.provider for oa in db_user.oauth_accounts],
        created_at=db_user.created_at,
        updated_at=db_user.updated_at,
    )


@router.patch("/me", response_model=UserProfile)
async def update_profile(
    body: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update current user's profile (name, avatar, onboarding status)."""
    svc = UserService(db)
    db_user = await svc.update_profile(
        user_id=user.user_id,
        name=body.name,
        avatar_url=body.avatar_url,
        onboarding_completed=body.onboarding_completed,
    )

    return UserProfile(
        id=str(db_user.id),
        email=db_user.email,
        email_verified=db_user.email_verified,
        name=db_user.name,
        avatar_url=db_user.avatar_url,
        is_active=db_user.is_active,
        has_password=db_user.password_hash is not None,
        onboarding_completed=db_user.onboarding_completed,
        oauth_providers=[oa.provider for oa in db_user.oauth_accounts],
        created_at=db_user.created_at,
        updated_at=db_user.updated_at,
    )


@router.post("/change-email", response_model=MessageResponse)
async def change_email(
    body: ChangeEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Request email change. Sends verification link to the new email."""
    svc = UserService(db)
    ip = _get_client_ip(request)

    token = await svc.request_email_change(
        user_id=user.user_id,
        new_email=body.new_email,
        password=body.password,
        ip_address=ip,
    )

    from app.clients import notification_client

    await notification_client.send_email_change_verification(
        email=body.new_email, name=user.name, token=token
    )

    return MessageResponse(message="Verification email sent to your new address")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Resend email verification for the current user's email."""
    svc = UserService(db)
    ip = _get_client_ip(request)

    db_user = await svc.get_profile(user.user_id)
    if db_user.email_verified:
        return MessageResponse(message="Email is already verified")

    token = await svc.create_email_verification_token(
        user_id=user.user_id, email=db_user.email
    )

    from app.clients import notification_client

    await notification_client.send_verification_email(
        email=db_user.email, name=db_user.name, token=token
    )

    return MessageResponse(message="Verification email sent")
