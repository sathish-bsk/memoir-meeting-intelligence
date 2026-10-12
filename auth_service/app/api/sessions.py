"""
Session management routes — list, revoke sessions.
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_current_user, CurrentUser
from app.schemas.common import MessageResponse
from app.schemas.team import SessionResponse
from app.services.session_service import SessionService


router = APIRouter(prefix="/auth", tags=["sessions"])


@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List all active sessions for the current user."""
    svc = SessionService(db)
    sessions = await svc.list_sessions(user_id=user.user_id)
    return [SessionResponse(**s) for s in sessions]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Revoke a specific session."""
    svc = SessionService(db)
    await svc.revoke_session(session_id=session_id, user_id=user.user_id)
    return MessageResponse(message="Session revoked")


@router.delete("/sessions", response_model=MessageResponse)
async def revoke_all_sessions(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Revoke all other sessions (keep current)."""
    svc = SessionService(db)
    await svc.revoke_all_other_sessions(user_id=user.user_id)
    return MessageResponse(message="All other sessions revoked")
