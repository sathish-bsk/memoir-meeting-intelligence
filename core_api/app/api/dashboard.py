"""
Dashboard API routes for aggregated stats and trends.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.team import TeamMember
from app.models.transcription import Transcription
from app.schemas.intelligence import DashboardStats
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    team_id: uuid.UUID = Query(...),
    user_id: Optional[uuid.UUID] = Query(None),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated dashboard statistics for a team.

    Returns total meetings, total minutes, unique speakers,
    and week-over-week meeting counts.
    """
    # Verify team membership
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    # Build base filters — optionally scope to a single user
    base_filters = [Meeting.team_id == team_id, Meeting.status == "completed"]
    if user_id is not None:
        base_filters.append(Meeting.created_by == user_id)

    # Total meetings (completed)
    total_meetings_result = await db.execute(
        select(func.count(Meeting.id)).where(*base_filters)
    )
    total_meetings = total_meetings_result.scalar() or 0

    # Total minutes
    total_seconds_result = await db.execute(
        select(func.coalesce(func.sum(Meeting.duration_seconds), 0)).where(*base_filters)
    )
    total_seconds = total_seconds_result.scalar() or 0
    total_minutes = total_seconds // 60

    # Total unique speakers across all transcriptions
    speaker_query = (
        select(func.coalesce(func.sum(Transcription.speaker_count), 0))
        .join(Meeting, Meeting.id == Transcription.meeting_id)
        .where(Meeting.team_id == team_id)
    )
    if user_id is not None:
        speaker_query = speaker_query.where(Meeting.created_by == user_id)
    total_speakers_result = await db.execute(speaker_query)
    total_speakers = total_speakers_result.scalar() or 0

    # This week and last week meeting counts
    now = datetime.now(timezone.utc)
    # Calculate start of this week (Monday)
    this_week_start = now - timedelta(days=now.weekday())
    this_week_start = this_week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_start = this_week_start - timedelta(days=7)

    this_week_result = await db.execute(
        select(func.count(Meeting.id)).where(
            *base_filters,
            Meeting.created_at >= this_week_start,
        )
    )
    this_week_meetings = this_week_result.scalar() or 0

    last_week_result = await db.execute(
        select(func.count(Meeting.id)).where(
            *base_filters,
            Meeting.created_at >= last_week_start,
            Meeting.created_at < this_week_start,
        )
    )
    last_week_meetings = last_week_result.scalar() or 0

    return DashboardStats(
        total_meetings=total_meetings,
        total_minutes=total_minutes,
        total_speakers=total_speakers,
        this_week_meetings=this_week_meetings,
        last_week_meetings=last_week_meetings,
    )


@router.get("/coaching-trends")
async def get_coaching_trends(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=90),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get coaching metrics trends over time.

    Returns daily aggregated coaching metrics for the given time range.
    """
    # Verify team membership
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get meetings with their creation dates in the range
    meetings_result = await db.execute(
        select(
            func.date_trunc("day", Meeting.created_at).label("day"),
            func.count(Meeting.id).label("meeting_count"),
            func.coalesce(func.avg(Meeting.duration_seconds), 0).label("avg_duration"),
        )
        .where(
            Meeting.team_id == team_id,
            Meeting.status == "completed",
            Meeting.created_at >= start_date,
        )
        .group_by(func.date_trunc("day", Meeting.created_at))
        .order_by(func.date_trunc("day", Meeting.created_at))
    )
    rows = meetings_result.all()

    trends = [
        {
            "date": row.day.isoformat() if row.day else None,
            "meeting_count": row.meeting_count,
            "avg_duration_seconds": round(float(row.avg_duration), 1),
        }
        for row in rows
    ]

    return {
        "team_id": str(team_id),
        "days": days,
        "trends": trends,
    }
