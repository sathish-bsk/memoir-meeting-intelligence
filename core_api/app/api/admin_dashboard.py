"""
Super Admin Dashboard API — cross-customer analytics.

All endpoints require is_super_admin=True. Aggregates data across all teams.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.ai_prompt_log import AiPromptLog
from app.models.meeting import Meeting
from app.models.team import Team, TeamMember
from videoclip_common.auth import UserContext, require_super_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/dashboard", tags=["admin-dashboard"])


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _week_start() -> datetime:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _day_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def get_overview(
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Summary cards: teams, users, meetings today/week, cost this month, failures."""
    today = _day_start()
    week = _week_start()
    month = _month_start()

    # P1.1 (2026-05-31): collapsed 9 sequential aggregate round-trips into
    # one SQL statement with scalar subqueries. Under prod RDS load each
    # round-trip costs ~500 ms, so this single-statement form drops the
    # endpoint's pool hold from ~4.5 s to ~500 ms. See plan/performance-
    # optimization-audit.md §1.1.
    from sqlalchemy import text as sa_text

    row = (await db.execute(
        sa_text("""
            SELECT
              (SELECT COUNT(*) FROM teams)        AS total_teams,
              (SELECT COUNT(*) FROM team_members) AS total_users,
              (SELECT COUNT(*) FROM meetings)     AS total_meetings_all_time,
              (SELECT COUNT(*) FROM meetings WHERE created_at >= :today) AS meetings_today,
              (SELECT COUNT(*) FROM meetings WHERE created_at >= :week)  AS meetings_this_week,
              (SELECT COUNT(*) FROM meetings WHERE status = 'failed'    AND updated_at >= :today) AS failed_today,
              (SELECT COUNT(*) FROM meetings WHERE status = 'completed' AND updated_at >= :today) AS completed_today,
              (SELECT COALESCE(SUM(cost_usd), 0)                 FROM ai_prompt_logs WHERE created_at >= :month) AS total_cost_this_month,
              (SELECT COALESCE(SUM(audio_duration_seconds), 0)   FROM ai_prompt_logs WHERE created_at >= :month
                                                                   AND audio_duration_seconds IS NOT NULL) AS total_audio_seconds_this_month
        """),
        {"today": today, "week": week, "month": month},
    )).mappings().first() or {}

    total_teams = int(row.get("total_teams") or 0)
    total_users = int(row.get("total_users") or 0)
    total_meetings_all_time = int(row.get("total_meetings_all_time") or 0)
    meetings_today = int(row.get("meetings_today") or 0)
    meetings_this_week = int(row.get("meetings_this_week") or 0)
    failed_today = int(row.get("failed_today") or 0)
    completed_today = int(row.get("completed_today") or 0)
    total_cost_this_month = float(row.get("total_cost_this_month") or 0.0)
    total_audio_hours_this_month = float(row.get("total_audio_seconds_this_month") or 0.0)
    success_rate_today = (
        round(completed_today / (completed_today + failed_today) * 100, 1)
        if (completed_today + failed_today) > 0 else 100.0
    )

    return {
        "total_teams": total_teams,
        "total_users": total_users,
        "total_meetings_all_time": total_meetings_all_time,
        "meetings_today": meetings_today,
        "meetings_this_week": meetings_this_week,
        "total_cost_this_month": round(float(total_cost_this_month), 4),
        "total_audio_hours_this_month": round(float(total_audio_hours_this_month) / 3600, 2),
        "failed_jobs_today": failed_today,
        "success_rate_today": success_rate_today,
    }


@router.get("/recent-activity")
async def get_recent_activity(
    limit: int = Query(20, ge=1, le=100),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recent meetings across all teams with team name, status, cost."""
    rows = (await db.execute(
        select(
            Meeting.id,
            Meeting.title,
            Meeting.created_at,
            Meeting.status,
            Meeting.source,
            Meeting.duration_seconds,
            Team.name.label("team_name"),
        )
        .join(Team, Meeting.team_id == Team.id)
        .order_by(Meeting.created_at.desc())
        .limit(limit)
    )).all()

    # Get per-meeting costs in one query
    meeting_ids = [r.id for r in rows]
    cost_rows = []
    if meeting_ids:
        cost_rows = (await db.execute(
            select(AiPromptLog.meeting_id, func.sum(AiPromptLog.cost_usd).label("total_cost"))
            .where(AiPromptLog.meeting_id.in_(meeting_ids))
            .group_by(AiPromptLog.meeting_id)
        )).all()
    cost_map = {str(r.meeting_id): float(r.total_cost or 0) for r in cost_rows}

    return [
        {
            "meeting_id": str(r.id),
            "team_name": r.team_name,
            "title": r.title or "Untitled",
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.status,
            "source": r.source,
            "duration_seconds": r.duration_seconds,
            "cost_usd": round(cost_map.get(str(r.id), 0.0), 4),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Cost Analytics
# ---------------------------------------------------------------------------

@router.get("/cost/summary")
async def get_cost_summary(
    period: str = Query("month", pattern="^(month|week|day)$"),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Total cost, ASR vs intelligence split, by recording source, projected monthly."""
    if period == "month":
        since = _month_start()
    elif period == "week":
        since = _week_start()
    else:
        since = _day_start()

    base = [AiPromptLog.created_at >= since]

    total_cost = (await db.execute(
        select(func.sum(AiPromptLog.cost_usd)).where(*base)
    )).scalar() or 0.0

    total_meetings = (await db.execute(
        select(func.count(func.distinct(AiPromptLog.meeting_id))).where(*base)
    )).scalar() or 0

    asr_cost = (await db.execute(
        select(func.sum(AiPromptLog.cost_usd)).where(
            *base,
            AiPromptLog.prompt_name.in_(["asr", "diarization", "llm_corrector"]),
        )
    )).scalar() or 0.0

    intelligence_cost = float(total_cost) - float(asr_cost)

    audio_hours = (await db.execute(
        select(func.sum(AiPromptLog.audio_duration_seconds)).where(
            *base,
            AiPromptLog.prompt_name == "asr",
        )
    )).scalar() or 0.0

    # Cost by recording source
    source_rows = (await db.execute(
        select(
            AiPromptLog.recording_source,
            func.sum(AiPromptLog.cost_usd).label("cost"),
        )
        .where(*base)
        .group_by(AiPromptLog.recording_source)
    )).all()
    cost_by_source = {(r.recording_source or "unknown"): round(float(r.cost or 0), 4) for r in source_rows}

    # Projected monthly (simple linear extrapolation from day/week)
    now = datetime.now(timezone.utc)
    days_elapsed = max((now - since).total_seconds() / 86400, 1)
    days_in_month = 30
    projected_monthly = float(total_cost) / days_elapsed * days_in_month if period != "month" else float(total_cost)

    return {
        "total_cost": round(float(total_cost), 4),
        "total_meetings": total_meetings,
        "avg_cost_per_meeting": round(float(total_cost) / max(total_meetings, 1), 4),
        "total_audio_hours": round(float(audio_hours) / 3600, 2),
        "asr_cost": round(float(asr_cost), 4),
        "intelligence_cost": round(intelligence_cost, 4),
        "cost_by_source": cost_by_source,
        "projected_monthly": round(projected_monthly, 4),
    }


@router.get("/cost/trends")
async def get_cost_trends(
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Cost trend over time — by day/week/month."""
    now = datetime.now(timezone.utc)
    if from_date:
        since = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
    elif granularity == "monthly":
        since = (now - timedelta(days=180)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "weekly":
        since = now - timedelta(days=84)  # 12 weeks
    else:
        since = now - timedelta(days=30)

    if to_date:
        until = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
    else:
        until = now

    trunc_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    trunc = trunc_map[granularity]
    period_col = func.date_trunc(trunc, AiPromptLog.created_at).label("period")

    rows = (await db.execute(
        select(
            period_col,
            func.count(func.distinct(AiPromptLog.meeting_id)).label("meetings"),
            func.sum(AiPromptLog.cost_usd).label("total_cost"),
            func.sum(
                case((AiPromptLog.prompt_name.in_(["asr", "diarization", "llm_corrector"]), AiPromptLog.cost_usd), else_=0)
            ).label("asr_cost"),
            func.sum(
                case((AiPromptLog.prompt_name.like("P%"), AiPromptLog.cost_usd), else_=0)
            ).label("intelligence_cost"),
        )
        .where(
            AiPromptLog.created_at >= since,
            AiPromptLog.created_at <= until,
        )
        .group_by(period_col)
        .order_by(period_col)
    )).all()

    return [
        {
            "period": r.period.isoformat() if r.period else None,
            "meetings": r.meetings or 0,
            "total_cost": round(float(r.total_cost or 0), 4),
            "asr_cost": round(float(r.asr_cost or 0), 4),
            "intelligence_cost": round(float(r.intelligence_cost or 0), 4),
        }
        for r in rows
    ]


@router.get("/cost/by-team")
async def get_cost_by_team(
    period: str = Query("month", pattern="^(month|week|all)$"),
    sort: str = Query("cost", pattern="^(cost|meetings|name)$"),
    limit: int = Query(50, ge=1, le=200),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-team cost breakdown."""
    if period == "month":
        since = _month_start()
    elif period == "week":
        since = _week_start()
    else:
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)

    cost_rows = (await db.execute(
        select(
            AiPromptLog.team_id,
            func.count(func.distinct(AiPromptLog.meeting_id)).label("meetings"),
            func.sum(AiPromptLog.cost_usd).label("total_cost"),
            func.sum(AiPromptLog.audio_duration_seconds).label("total_audio_seconds"),
        )
        .where(AiPromptLog.created_at >= since)
        .group_by(AiPromptLog.team_id)
    )).all()

    if not cost_rows:
        return []

    team_ids = [r.team_id for r in cost_rows if r.team_id]
    team_rows = (await db.execute(
        select(Team.id, Team.name).where(Team.id.in_(team_ids))
    )).all()
    team_map = {str(r.id): r.name for r in team_rows}

    items = []
    for r in cost_rows:
        tid = str(r.team_id) if r.team_id else "unknown"
        meetings = r.meetings or 0
        cost = float(r.total_cost or 0)
        items.append({
            "team_id": tid,
            "team_name": team_map.get(tid, "Unknown"),
            "meetings": meetings,
            "total_cost": round(cost, 4),
            "avg_cost_per_meeting": round(cost / max(meetings, 1), 4),
            "total_audio_hours": round(float(r.total_audio_seconds or 0) / 3600, 2),
        })

    if sort == "cost":
        items.sort(key=lambda x: x["total_cost"], reverse=True)
    elif sort == "meetings":
        items.sort(key=lambda x: x["meetings"], reverse=True)
    else:
        items.sort(key=lambda x: x["team_name"])

    return items[:limit]


@router.get("/cost/meetings")
async def get_cost_meetings(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    team_id: Optional[uuid.UUID] = Query(None),
    sort_by: str = Query("cost", pattern="^(cost|date|duration)$"),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-meeting cost table — paginated."""
    filters = []
    if team_id:
        filters.append(AiPromptLog.team_id == team_id)

    cost_rows = (await db.execute(
        select(
            AiPromptLog.meeting_id,
            AiPromptLog.team_id,
            func.sum(AiPromptLog.cost_usd).label("total_cost"),
            func.sum(
                case((AiPromptLog.prompt_name.in_(["asr", "diarization", "llm_corrector"]), AiPromptLog.cost_usd), else_=0)
            ).label("asr_cost"),
            func.sum(
                case((AiPromptLog.prompt_name.like("P%"), AiPromptLog.cost_usd), else_=0)
            ).label("intelligence_cost"),
            func.max(AiPromptLog.audio_duration_seconds).label("audio_duration_seconds"),
        )
        .where(*filters)
        .group_by(AiPromptLog.meeting_id, AiPromptLog.team_id)
    )).all()

    if not cost_rows:
        return {"items": [], "total": 0}

    meeting_ids = [r.meeting_id for r in cost_rows if r.meeting_id]
    meeting_rows = (await db.execute(
        select(Meeting.id, Meeting.title, Meeting.created_at, Meeting.source, Meeting.team_id)
        .join(Team, Meeting.team_id == Team.id)
        .add_columns(Team.name.label("team_name"))
        .where(Meeting.id.in_(meeting_ids))
    )).all()
    meeting_map = {
        str(r.id): {"title": r.title, "created_at": r.created_at, "source": r.source, "team_name": r.team_name}
        for r in meeting_rows
    }

    items = []
    for r in cost_rows:
        mid = str(r.meeting_id) if r.meeting_id else None
        m = meeting_map.get(mid, {})
        items.append({
            "meeting_id": mid,
            "team_id": str(r.team_id) if r.team_id else None,
            "team_name": m.get("team_name", "Unknown"),
            "title": m.get("title") or "Untitled",
            "date": m.get("created_at").isoformat() if m.get("created_at") else None,
            "source": m.get("source", "unknown"),
            "audio_duration_seconds": float(r.audio_duration_seconds or 0),
            "asr_cost": round(float(r.asr_cost or 0), 4),
            "intelligence_cost": round(float(r.intelligence_cost or 0), 4),
            "total_cost": round(float(r.total_cost or 0), 4),
        })

    if sort_by == "cost":
        items.sort(key=lambda x: x["total_cost"], reverse=True)
    elif sort_by == "date":
        items.sort(key=lambda x: x["date"] or "", reverse=True)
    else:
        items.sort(key=lambda x: x["audio_duration_seconds"], reverse=True)

    total = len(items)
    offset = (page - 1) * limit
    return {"items": items[offset:offset + limit], "total": total}


@router.get("/cost/top-expensive")
async def get_top_expensive(
    limit: int = Query(10, ge=1, le=50),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Top N most expensive meetings."""
    rows = (await db.execute(
        select(
            AiPromptLog.meeting_id,
            AiPromptLog.team_id,
            func.sum(AiPromptLog.cost_usd).label("total_cost"),
        )
        .where(AiPromptLog.meeting_id.isnot(None))
        .group_by(AiPromptLog.meeting_id, AiPromptLog.team_id)
        .order_by(func.sum(AiPromptLog.cost_usd).desc())
        .limit(limit)
    )).all()

    if not rows:
        return []

    meeting_ids = [r.meeting_id for r in rows]
    meeting_rows = (await db.execute(
        select(Meeting.id, Meeting.title, Team.name.label("team_name"))
        .join(Team, Meeting.team_id == Team.id)
        .where(Meeting.id.in_(meeting_ids))
    )).all()
    meeting_map = {str(r.id): {"title": r.title, "team_name": r.team_name} for r in meeting_rows}

    return [
        {
            "meeting_id": str(r.meeting_id),
            "team_id": str(r.team_id) if r.team_id else None,
            "team_name": meeting_map.get(str(r.meeting_id), {}).get("team_name", "Unknown"),
            "title": meeting_map.get(str(r.meeting_id), {}).get("title") or "Untitled",
            "total_cost": round(float(r.total_cost or 0), 4),
        }
        for r in rows
    ]


@router.get("/cost/cache-health")
async def get_cache_health(
    hours: int = Query(24, ge=1, le=168),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Tier 4.0.a — Anthropic prompt-cache health.

    Reports cache write/read tokens for Claude calls in the last N hours,
    grouped by model. The key health metric is hit_ratio = read / (read + creation):
    - > 0.6 after warmup → caching working well
    - close to 0 → cache key is breaking somewhere (timestamp in cached
      prefix, non-deterministic vocab order, missing beta header for 1h TTL)

    Cost-attribution sanity: cost_usd is computed by the Anthropic provider
    with the right multipliers (1.25× / 2× / 0.1×) baked in, so existing
    cost queries stay correct without code changes.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = (await db.execute(
        select(
            AiPromptLog.model,
            func.coalesce(func.sum(AiPromptLog.cache_creation_tokens), 0).label("creation"),
            func.coalesce(func.sum(AiPromptLog.cache_read_tokens), 0).label("read"),
            func.coalesce(func.sum(AiPromptLog.input_tokens), 0).label("uncached"),
            func.coalesce(func.sum(AiPromptLog.output_tokens), 0).label("output"),
            func.coalesce(func.sum(AiPromptLog.cost_usd), 0).label("cost_usd"),
            func.count(AiPromptLog.id).label("call_count"),
        )
        .where(
            AiPromptLog.provider == "anthropic",
            AiPromptLog.created_at >= since,
        )
        .group_by(AiPromptLog.model)
    )).all()

    items = []
    for r in rows:
        creation = int(r.creation or 0)
        read = int(r.read or 0)
        denom = creation + read
        hit_ratio = round(read / denom, 4) if denom > 0 else None
        items.append({
            "model": r.model,
            "call_count": int(r.call_count or 0),
            "cache_creation_tokens": creation,
            "cache_read_tokens": read,
            "uncached_input_tokens": int(r.uncached or 0),
            "output_tokens": int(r.output or 0),
            "cost_usd": round(float(r.cost_usd or 0), 4),
            "hit_ratio": hit_ratio,
        })
    return {
        "window_hours": hours,
        "since": since.isoformat(),
        "by_model": items,
        "_health_note": (
            "Healthy: hit_ratio > 0.6 after warmup. If hit_ratio is 0 across "
            "calls, check (a) cache key determinism (vocab order, no timestamps "
            "in cached prefix), (b) anthropic-beta: extended-cache-ttl-2025-04-11 "
            "header for 1h TTL, (c) provider 'anthropic' rows actually being written."
        ),
    }


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@router.get("/customers")
async def get_customers(
    sort: str = Query("last_active", pattern="^(last_active|cost|meetings|name)$"),
    limit: int = Query(50, ge=1, le=200),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """All customers with health indicators and churn risk."""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    month_start = _month_start()

    teams = (await db.execute(
        select(Team.id, Team.name)
    )).all()

    if not teams:
        return []

    team_ids = [t.id for t in teams]

    member_counts = {
        str(r.team_id): r.cnt
        for r in (await db.execute(
            select(TeamMember.team_id, func.count(TeamMember.id).label("cnt"))
            .where(TeamMember.team_id.in_(team_ids))
            .group_by(TeamMember.team_id)
        )).all()
    }

    week_meetings = {
        str(r.team_id): r.cnt
        for r in (await db.execute(
            select(Meeting.team_id, func.count(Meeting.id).label("cnt"))
            .where(Meeting.team_id.in_(team_ids), Meeting.created_at >= week_ago)
            .group_by(Meeting.team_id)
        )).all()
    }

    last_meeting = {
        str(r.team_id): r.last_at
        for r in (await db.execute(
            select(Meeting.team_id, func.max(Meeting.created_at).label("last_at"))
            .where(Meeting.team_id.in_(team_ids))
            .group_by(Meeting.team_id)
        )).all()
    }

    monthly_cost = {
        str(r.team_id): float(r.cost or 0)
        for r in (await db.execute(
            select(AiPromptLog.team_id, func.sum(AiPromptLog.cost_usd).label("cost"))
            .where(AiPromptLog.team_id.in_(team_ids), AiPromptLog.created_at >= month_start)
            .group_by(AiPromptLog.team_id)
        )).all()
    }

    items = []
    for t in teams:
        tid = str(t.id)
        last_at = last_meeting.get(tid)
        days_inactive = (datetime.now(timezone.utc) - last_at).days if last_at else 999

        if days_inactive > 14:
            churn_risk = "high"
        elif days_inactive > 7:
            churn_risk = "medium"
        else:
            churn_risk = "low"

        items.append({
            "team_id": tid,
            "team_name": t.name,
            "member_count": member_counts.get(tid, 0),
            "meetings_this_week": week_meetings.get(tid, 0),
            "last_meeting_at": last_at.isoformat() if last_at else None,
            "days_inactive": days_inactive if last_at else None,
            "total_cost_this_month": round(monthly_cost.get(tid, 0.0), 4),
            "churn_risk": churn_risk,
        })

    if sort == "last_active":
        items.sort(key=lambda x: x["last_meeting_at"] or "", reverse=True)
    elif sort == "cost":
        items.sort(key=lambda x: x["total_cost_this_month"], reverse=True)
    elif sort == "meetings":
        items.sort(key=lambda x: x["meetings_this_week"], reverse=True)
    else:
        items.sort(key=lambda x: x["team_name"])

    return items[:limit]


@router.get("/customers/{team_id}")
async def get_customer_detail(
    team_id: uuid.UUID,
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Single customer deep dive."""
    team = (await db.execute(
        select(Team).where(Team.id == team_id)
    )).scalar_one_or_none()
    if not team:
        from fastapi import HTTPException
        raise HTTPException(404, "Team not found")

    # Recording source distribution
    source_rows = (await db.execute(
        select(Meeting.source, func.count(Meeting.id).label("cnt"))
        .where(Meeting.team_id == team_id)
        .group_by(Meeting.source)
    )).all()
    recording_sources = {(r.source or "unknown"): r.cnt for r in source_rows}

    # Meeting trend (last 8 weeks)
    eight_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=8)
    week_col = func.date_trunc("week", Meeting.created_at).label("week")
    trend_rows = (await db.execute(
        select(week_col, func.count(Meeting.id).label("cnt"))
        .where(Meeting.team_id == team_id, Meeting.created_at >= eight_weeks_ago)
        .group_by(week_col)
        .order_by(week_col)
    )).all()
    meetings_trend = [{"week": r.week.isoformat(), "count": r.cnt} for r in trend_rows]

    # Cost trend (last 8 weeks)
    week_col2 = func.date_trunc("week", AiPromptLog.created_at).label("week")
    cost_trend_rows = (await db.execute(
        select(week_col2, func.sum(AiPromptLog.cost_usd).label("cost"))
        .where(AiPromptLog.team_id == team_id, AiPromptLog.created_at >= eight_weeks_ago)
        .group_by(week_col2)
        .order_by(week_col2)
    )).all()
    cost_trend = [{"week": r.week.isoformat(), "cost": round(float(r.cost or 0), 4)} for r in cost_trend_rows]

    return {
        "team_id": str(team.id),
        "team_name": team.name,
        "recording_sources": recording_sources,
        "meetings_trend": meetings_trend,
        "cost_trend": cost_trend,
    }


@router.get("/churn-risk")
async def get_churn_risk(
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Teams inactive for 7+ days (potential churn)."""
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    last_meeting = {
        str(r.team_id): r.last_at
        for r in (await db.execute(
            select(Meeting.team_id, func.max(Meeting.created_at).label("last_at"))
            .group_by(Meeting.team_id)
        )).all()
    }

    at_risk_team_ids = [
        uuid.UUID(tid) for tid, last_at in last_meeting.items()
        if last_at and last_at < week_ago
    ]

    if not at_risk_team_ids:
        return []

    teams = (await db.execute(
        select(Team.id, Team.name)
        .where(Team.id.in_(at_risk_team_ids))
    )).all()

    member_counts = {
        str(r.team_id): r.cnt
        for r in (await db.execute(
            select(TeamMember.team_id, func.count(TeamMember.id).label("cnt"))
            .where(TeamMember.team_id.in_(at_risk_team_ids))
            .group_by(TeamMember.team_id)
        )).all()
    }

    now = datetime.now(timezone.utc)
    result = []
    for t in teams:
        tid = str(t.id)
        last_at = last_meeting.get(tid)
        days = (now - last_at).days if last_at else 999
        result.append({
            "team_id": tid,
            "team_name": t.name,
            "last_meeting_at": last_at.isoformat() if last_at else None,
            "days_inactive": days,
            "member_count": member_counts.get(tid, 0),
        })

    result.sort(key=lambda x: x["days_inactive"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

@router.get("/processing/stats")
async def get_processing_stats(
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Queue stats, success rate, avg processing time."""
    today = _day_start()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # Use raw SQL for transcription_jobs since we don't have a model for it
    result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'in_progress') AS active_jobs,
            COUNT(*) FILTER (WHERE status = 'waiting') AS waiting_jobs,
            COUNT(*) FILTER (WHERE status = 'completed' AND updated_at >= :today) AS completed_today,
            COUNT(*) FILTER (WHERE status = 'failed' AND updated_at >= :today) AS failed_today,
            COUNT(*) FILTER (WHERE status = 'completed' AND updated_at >= :week_ago) AS completed_week,
            COUNT(*) FILTER (WHERE status = 'failed' AND updated_at >= :week_ago) AS failed_week
        FROM transcription_jobs
    """), {"today": today, "week_ago": week_ago})
    row = result.mappings().one()

    completed_week = int(row["completed_week"] or 0)
    failed_week = int(row["failed_week"] or 0)
    total_week = completed_week + failed_week
    success_rate_7d = round(completed_week / max(total_week, 1) * 100, 1)

    # Average processing time (from completed meetings this week)
    avg_result = await db.execute(
        select(func.avg(Meeting.duration_seconds)).where(
            Meeting.status == "completed",
            Meeting.updated_at >= week_ago,
            Meeting.duration_seconds.isnot(None),
        )
    )
    avg_processing = float(avg_result.scalar() or 0)

    return {
        "active_jobs": int(row["active_jobs"] or 0),
        "waiting_jobs": int(row["waiting_jobs"] or 0),
        "completed_today": int(row["completed_today"] or 0),
        "failed_today": int(row["failed_today"] or 0),
        "success_rate_7d": success_rate_7d,
        "avg_processing_seconds": round(avg_processing, 1),
    }


@router.get("/processing/failures")
async def get_processing_failures(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=100),
    _user: UserContext = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Recent failed jobs with error messages."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(text("""
        SELECT
            tj.id AS job_id,
            tj.meeting_id,
            tj.error_message,
            tj.updated_at AS failed_at,
            tj.retry_count,
            m.title AS meeting_title,
            t.name AS team_name
        FROM transcription_jobs tj
        LEFT JOIN meetings m ON m.id = tj.meeting_id
        LEFT JOIN teams t ON t.id = m.team_id
        WHERE tj.status = 'failed'
          AND tj.updated_at >= :since
        ORDER BY tj.updated_at DESC
        LIMIT :limit
    """), {"since": since, "limit": limit})

    rows = result.mappings().all()
    return [
        {
            "job_id": str(r["job_id"]),
            "meeting_id": str(r["meeting_id"]) if r["meeting_id"] else None,
            "team_name": r["team_name"] or "Unknown",
            "meeting_title": r["meeting_title"] or "Untitled",
            "error_message": r["error_message"],
            "failed_at": r["failed_at"].isoformat() if r["failed_at"] else None,
            "retry_count": r["retry_count"] or 0,
        }
        for r in rows
    ]
