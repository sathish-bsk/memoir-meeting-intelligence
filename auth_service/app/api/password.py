"""
Password management routes — forgot, reset, change.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_current_user, CurrentUser
from app.schemas.common import MessageResponse
from app.schemas.user import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services.user_service import UserService


router = APIRouter(prefix="/auth", tags=["password"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Always returns success to prevent email enumeration.
    In production, this would send an email with the reset link.
    """
    svc = UserService(db)
    ip = _get_client_ip(request)

    token = await svc.forgot_password(email=body.email, ip_address=ip)

    if token:
        from app.clients import notification_client

        # Look up the user name for a nicer email
        user = await svc.user_repo.find_by_email(body.email)
        name = user.name if user else "there"
        await notification_client.send_password_reset_email(
            email=body.email, name=name, token=token
        )

    return MessageResponse(
        message="If an account exists with that email, a reset link has been sent"
    )


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using the token from forgot-password."""
    svc = UserService(db)
    ip = _get_client_ip(request)

    await svc.reset_password(
        token=body.token,
        new_password=body.new_password,
        ip_address=ip,
    )

    return MessageResponse(message="Password reset successfully")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Change password (requires current password)."""
    svc = UserService(db)
    ip = _get_client_ip(request)
    ua = request.headers.get("User-Agent")

    await svc.change_password(
        user_id=user.user_id,
        current_password=body.current_password,
        new_password=body.new_password,
        ip_address=ip,
        user_agent=ua,
    )

    return MessageResponse(message="Password changed successfully")
