"""
Intelligence API routes — aggregation queries, digests, and scheduled job configuration.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date, String, case, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.call_intelligence import (
    CallCompetitor,
    CallFeatureRequest,
    CallIntelligence,
    CallObjection,
    CallPainPoint,
    CallQuestion,
    IntelligenceDigest,
    ScheduledJob,
    ScheduledJobRun,
)
from app.models.team import TeamMember
from app.schemas.intelligence import (
    CallIntelligenceResponse,
    CompetitorAggregation,
    DealStageCall,
    DealStageCallsResponse,
    DealStagesResponse,
    DealStageSummary,
    DealTimelineEntry,
    DealTimelineResponse,
    DigestListResponse,
    DigestResponse,
    DigestSettingsResponse,
    DigestSettingsUpdate,
    FeatureRequestAggregation,
    IntelligenceOverview,
    JobRunListResponse,
    JobRunResponse,
    MentionDetail,
    ObjectionAggregation,
    PMFTrendPoint,
    PainPointAggregation,
    PmfHistoryEntry,
    PmfHistoryResponse,
    PmfSignalCallsResponse,
    PmfSignalItem,
    PmfSignalsResponse,
    QuestionAggregation,
)
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

async def _verify_team_member(db: AsyncSession, team_id: uuid.UUID, user_id: str) -> TeamMember:
    """Raise 403 if user is not a member of the team. Returns the membership row."""
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this team")
    return member


async def _get_user_filter(db: AsyncSession, team_id: uuid.UUID, user_id: str) -> uuid.UUID | None:
    """Return user_id if member role (filter to own data), None if admin/owner (see all)."""
    member = await _verify_team_member(db, team_id, user_id)
    if member.role == "member":
        return uuid.UUID(user_id)
    return None


def _date_range(days: int) -> datetime:
    """Return the start datetime for a N-day lookback."""
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Per-call intelligence
# ---------------------------------------------------------------------------

@router.get("/call/{meeting_id}", response_model=CallIntelligenceResponse)
async def get_call_intelligence(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get intelligence extraction results for a specific call.

    A meeting can have multiple call_intelligence rows after retranscribe
    (the table has no version column — each new pipeline run inserts a
    fresh row keyed by meeting_id alone). Always return the most-recent
    row so the UI reflects the latest pipeline output. Without this
    .order_by + .first() the previous .scalar_one_or_none() raised
    MultipleResultsFound on every retranscribed meeting, blanking the
    UI's PMF / intelligence card.
    """
    result = await db.execute(
        select(CallIntelligence)
        .where(CallIntelligence.meeting_id == meeting_id)
        .order_by(desc(CallIntelligence.version))
        .limit(1)
    )
    ci = result.scalar_one_or_none()
    if not ci:
        raise HTTPException(404, "No intelligence data for this meeting")

    await _verify_team_member(db, ci.team_id, user.user_id)
    return ci


# ---------------------------------------------------------------------------
# Aggregation endpoints
# ---------------------------------------------------------------------------

@router.get("/overview", response_model=IntelligenceOverview)
async def get_intelligence_overview(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full intelligence overview for the dashboard (single call)."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    since = _date_range(days)

    # Call count
    count_q = select(func.count(CallIntelligence.id)).where(
        CallIntelligence.team_id == team_id,
        CallIntelligence.created_at >= since,
    )
    if created_by:
        count_q = count_q.where(CallIntelligence.created_by == created_by)
    count_result = await db.execute(count_q)
    call_count = count_result.scalar() or 0

    pain_points = await _aggregate_pain_points(db, team_id, since, limit=10, created_by=created_by)
    feature_requests = await _aggregate_feature_requests(db, team_id, since, limit=10, created_by=created_by)
    competitors = await _aggregate_competitors(db, team_id, since, limit=10, created_by=created_by)
    objections = await _aggregate_objections(db, team_id, since, limit=10, created_by=created_by)
    questions = await _aggregate_questions(db, team_id, since, limit=10, created_by=created_by)
    pmf_trend = await _compute_pmf_trend(db, team_id, since, created_by=created_by)

    # Current digest
    digest_result = await db.execute(
        select(IntelligenceDigest)
        .where(IntelligenceDigest.team_id == team_id)
        .order_by(desc(IntelligenceDigest.period_end))
        .limit(1)
    )
    current_digest = digest_result.scalar_one_or_none()

    return IntelligenceOverview(
        call_count=call_count,
        date_range_start=since.date(),
        date_range_end=datetime.now(timezone.utc).date(),
        pain_points=pain_points,
        feature_requests=feature_requests,
        competitors=competitors,
        objections=objections,
        questions=questions,
        pmf_trend=pmf_trend,
        current_digest=DigestResponse.model_validate(current_digest) if current_digest else None,
    )


@router.get("/pain-points", response_model=list[PainPointAggregation])
async def get_pain_points(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated pain points ranked by mention count."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _aggregate_pain_points(db, team_id, _date_range(days), limit, created_by=created_by)


@router.get("/feature-requests", response_model=list[FeatureRequestAggregation])
async def get_feature_requests(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated feature requests ranked by request count."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _aggregate_feature_requests(db, team_id, _date_range(days), limit, created_by=created_by)


@router.get("/competitors", response_model=list[CompetitorAggregation])
async def get_competitors(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated competitor mentions with sentiment breakdown."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _aggregate_competitors(db, team_id, _date_range(days), limit, created_by=created_by)


@router.get("/objections", response_model=list[ObjectionAggregation])
async def get_objections(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated objections with category and resolution rate."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _aggregate_objections(db, team_id, _date_range(days), limit, created_by=created_by)


@router.get("/pmf-signals", response_model=list[PMFTrendPoint])
async def get_pmf_signals(
    team_id: uuid.UUID = Query(...),
    days: int = Query(90, ge=7, le=365),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get PMF signal trend (weekly averages)."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _compute_pmf_trend(db, team_id, _date_range(days), created_by=created_by)


# ---------------------------------------------------------------------------
# Questions endpoint
# ---------------------------------------------------------------------------

@router.get("/questions", response_model=list[QuestionAggregation])
async def get_questions(
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated prospect questions ranked by frequency."""
    created_by = await _get_user_filter(db, team_id, user.user_id)
    return await _aggregate_questions(db, team_id, _date_range(days), limit, created_by=created_by)


@router.get("/faq-groups")
async def get_faq_groups(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the latest FAQ grouping from the most recent digest."""
    await _verify_team_member(db, team_id, user.user_id)
    result = await db.execute(
        select(IntelligenceDigest)
        .where(IntelligenceDigest.team_id == team_id)
        .order_by(desc(IntelligenceDigest.period_end))
        .limit(1)
    )
    digest = result.scalar_one_or_none()
    if not digest or not digest.metrics:
        return {"groups": [], "last_updated": None}
    return {
        "groups": digest.metrics.get("faq_groups", []),
        "last_updated": digest.created_at,
    }


# ---------------------------------------------------------------------------
# Include call in intelligence (opt-in for skipped/internal calls)
# ---------------------------------------------------------------------------

@router.post("/call/{meeting_id}/include", status_code=202)
async def include_call_in_intelligence(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Request intelligence analysis for a call that was previously skipped."""
    from app.models.meeting import Meeting

    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    await _verify_team_member(db, meeting.team_id, user.user_id)

    # TODO: Enqueue a reprocess-intelligence job in TranscriptionBase
    # For now, return accepted status
    return {"status": "queued", "meeting_id": str(meeting_id)}


# ---------------------------------------------------------------------------
# Digest endpoints
# ---------------------------------------------------------------------------

@router.get("/digests", response_model=DigestListResponse)
async def get_digests(
    team_id: uuid.UUID = Query(...),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated list of intelligence digests."""
    await _verify_team_member(db, team_id, user.user_id)

    total_result = await db.execute(
        select(func.count(IntelligenceDigest.id)).where(
            IntelligenceDigest.team_id == team_id,
        )
    )
    total = total_result.scalar() or 0

    rows_result = await db.execute(
        select(IntelligenceDigest)
        .where(IntelligenceDigest.team_id == team_id)
        .order_by(desc(IntelligenceDigest.period_end))
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    digests = [DigestResponse.model_validate(r) for r in rows_result.scalars().all()]

    return DigestListResponse(digests=digests, total=total)


@router.get("/digests/current", response_model=DigestResponse)
async def get_current_digest(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent digest."""
    await _verify_team_member(db, team_id, user.user_id)

    result = await db.execute(
        select(IntelligenceDigest)
        .where(IntelligenceDigest.team_id == team_id)
        .order_by(desc(IntelligenceDigest.period_end))
        .limit(1)
    )
    digest = result.scalar_one_or_none()
    if not digest:
        raise HTTPException(404, "No digests generated yet")
    return digest


@router.get("/digests/{digest_id}", response_model=DigestResponse)
async def get_digest(
    digest_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific digest by ID."""
    result = await db.execute(
        select(IntelligenceDigest).where(IntelligenceDigest.id == digest_id)
    )
    digest = result.scalar_one_or_none()
    if not digest:
        raise HTTPException(404, "Digest not found")

    await _verify_team_member(db, digest.team_id, user.user_id)
    return digest


@router.post("/digests/generate", response_model=DigestResponse)
async def generate_digest_now(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger digest generation for the current period."""
    await _verify_team_member(db, team_id, user.user_id)

    from app.services.digest_generator import generate_digest

    digest = await generate_digest(db, team_id)
    if not digest:
        raise HTTPException(404, "No external calls found to generate digest from")

    await db.commit()
    return digest


# ---------------------------------------------------------------------------
# Digest settings (scheduled job configuration)
# ---------------------------------------------------------------------------

@router.get("/digest-settings", response_model=DigestSettingsResponse)
async def get_digest_settings(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current digest schedule settings. (Deprecated — use /api/notifications/digest-settings)"""
    from app.api.notifications import get_digest_settings as _get
    return await _get(team_id=team_id, job_type="intelligence_digest", user=user, db=db)


@router.put("/digest-settings", response_model=DigestSettingsResponse)
async def update_digest_settings(
    body: DigestSettingsUpdate,
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update digest schedule settings. (Deprecated — use /api/notifications/digest-settings)"""
    from app.api.notifications import DigestSettingsUpdateBody, update_digest_settings as _update
    new_body = DigestSettingsUpdateBody(
        team_id=team_id,
        job_type="intelligence_digest",
        frequency=body.frequency,
        preferred_time=body.preferred_time.isoformat() if body.preferred_time else None,
        preferred_day=body.preferred_day,
        enabled=body.enabled,
    )
    return await _update(body=new_body, user=user, db=db)


@router.get("/digest-history", response_model=JobRunListResponse)
async def get_digest_history(
    team_id: uuid.UUID = Query(...),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated audit log of digest generation runs."""
    await _verify_team_member(db, team_id, user.user_id)

    total_result = await db.execute(
        select(func.count(ScheduledJobRun.id)).where(
            ScheduledJobRun.team_id == team_id,
            ScheduledJobRun.job_type == "intelligence_digest",
        )
    )
    total = total_result.scalar() or 0

    rows_result = await db.execute(
        select(ScheduledJobRun)
        .where(
            ScheduledJobRun.team_id == team_id,
            ScheduledJobRun.job_type == "intelligence_digest",
        )
        .order_by(desc(ScheduledJobRun.started_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    runs = [JobRunResponse.model_validate(r) for r in rows_result.scalars().all()]

    return JobRunListResponse(runs=runs, total=total)


# ---------------------------------------------------------------------------
# Deal Intelligence endpoints
# ---------------------------------------------------------------------------

@router.get("/deal-stages", response_model=DealStagesResponse)
async def get_deal_stages(
    team_id: uuid.UUID = Query(...),
    days: int = Query(90, ge=7, le=365),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get CRM stage counts for deal intelligence view. Requires CRM connection."""
    # P1.2 (2026-05-31): the get_crm_connections() HTTP call to
    # integration_service used to fire while this request's DB session
    # was still open from _get_user_filter(), holding a pool connection
    # for the HTTP round-trip duration (typically 200-1500 ms). Commit
    # to release the connection before the HTTP, then re-acquire for
    # the aggregation phase. Same shape as today's schedule-bot fix.
    # See plan/performance-optimization-audit.md §1.2.
    created_by = await _get_user_filter(db, team_id, user.user_id)
    await db.commit()

    # Check for CRM connections (HTTP — no DB transaction open here)
    try:
        from app.clients.crm_client import get_crm_connections
        connections = await get_crm_connections(str(team_id))
        has_crm = any(c.get("is_active") for c in (connections or []))
    except Exception:
        has_crm = False

    if not has_crm:
        return DealStagesResponse(has_crm=False)

    since = _date_range(days)

    # Group by crm_stage_label
    query = (
        select(
            CallIntelligence.crm_stage_label,
            func.count(CallIntelligence.id).label("call_count"),
        )
        .where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= since,
            CallIntelligence.crm_stage_label.isnot(None),
        )
    )
    if created_by:
        query = query.where(CallIntelligence.created_by == created_by)

    result = await db.execute(
        query.group_by(CallIntelligence.crm_stage_label)
        .order_by(func.count(CallIntelligence.id).desc())
    )
    rows = result.all()

    stages = [
        DealStageSummary(
            stage_label=row.crm_stage_label,
            call_count=row.call_count,
        )
        for row in rows
    ]

    # Count unassigned (no CRM stage)
    unassigned_q = select(func.count(CallIntelligence.id)).where(
        CallIntelligence.team_id == team_id,
        CallIntelligence.created_at >= since,
        CallIntelligence.crm_stage_label.is_(None),
    )
    if created_by:
        unassigned_q = unassigned_q.where(CallIntelligence.created_by == created_by)
    unassigned = (await db.execute(unassigned_q)).scalar() or 0

    return DealStagesResponse(has_crm=True, stages=stages, unassigned_count=unassigned)


@router.get("/deal-stages/{stage_label}/calls", response_model=DealStageCallsResponse)
async def get_deal_stage_calls(
    stage_label: str,
    team_id: uuid.UUID = Query(...),
    days: int = Query(90, ge=7, le=365),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated calls for a specific CRM stage."""
    from app.models.meeting import Meeting

    created_by = await _get_user_filter(db, team_id, user.user_id)
    since = _date_range(days)

    base = (
        select(
            Meeting.id.label("meeting_id"),
            Meeting.title,
            Meeting.created_at.label("date"),
            Meeting.duration_seconds,
            CallIntelligence.pull_score,
            CallIntelligence.deal_momentum,
            CallIntelligence.validated_stage,
            CallIntelligence.crm_stage_label,
            CallIntelligence.call_health,
            CallIntelligence.has_committed_next_step,
        )
        .join(CallIntelligence, CallIntelligence.meeting_id == Meeting.id)
        .where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= since,
        )
    )

    if stage_label == "unassigned":
        base = base.where(CallIntelligence.crm_stage_label.is_(None))
    else:
        base = base.where(CallIntelligence.crm_stage_label == stage_label)

    if created_by:
        base = base.where(CallIntelligence.created_by == created_by)

    # Total count
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0

    # Paginated results
    result = await db.execute(
        base.order_by(desc(Meeting.created_at)).offset(offset).limit(limit)
    )
    calls = [
        DealStageCall(
            meeting_id=row.meeting_id,
            title=row.title,
            date=row.date,
            duration_seconds=row.duration_seconds,
            pull_score=row.pull_score,
            deal_momentum=row.deal_momentum,
            validated_stage=row.validated_stage,
            crm_stage_label=row.crm_stage_label,
            call_health=row.call_health,
            has_committed_next_step=row.has_committed_next_step,
        )
        for row in result.all()
    ]

    return DealStageCallsResponse(calls=calls, total=total, stage_label=stage_label)


# ---------------------------------------------------------------------------
# Deal Timeline endpoint
# ---------------------------------------------------------------------------

@router.get("/deals/{deal_id}/timeline", response_model=DealTimelineResponse)
async def get_deal_timeline(
    deal_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get timeline of all calls in a deal."""
    from app.models.meeting import Meeting
    from app.models.memoir_deal import MemoirDeal

    # Get deal
    deal_result = await db.execute(
        select(MemoirDeal).where(MemoirDeal.id == deal_id)
    )
    deal = deal_result.scalar_one_or_none()
    if not deal:
        raise HTTPException(404, "Deal not found")

    await _verify_team_member(db, deal.team_id, user.user_id)

    # Get all calls in this deal
    result = await db.execute(
        select(
            Meeting.id.label("meeting_id"),
            Meeting.title,
            Meeting.created_at.label("date"),
            Meeting.call_number_in_deal,
            CallIntelligence.validated_stage,
            CallIntelligence.crm_stage_label,
            CallIntelligence.call_health,
            CallIntelligence.deal_momentum,
            CallIntelligence.pull_score,
            CallIntelligence.summary,
        )
        .outerjoin(CallIntelligence, CallIntelligence.meeting_id == Meeting.id)
        .where(Meeting.memoir_deal_id == deal_id)
        .order_by(Meeting.created_at)
    )

    entries = []
    for row in result.all():
        one_line = None
        if row.summary and isinstance(row.summary, dict):
            one_line = row.summary.get("one_line_summary") or (row.summary.get("summary", {}).get("tldr", "")[:200] if isinstance(row.summary.get("summary"), dict) else "")
        entries.append(DealTimelineEntry(
            meeting_id=row.meeting_id,
            title=row.title,
            date=row.date,
            call_number=row.call_number_in_deal,
            validated_stage=row.validated_stage,
            crm_stage_label=row.crm_stage_label,
            call_health=row.call_health,
            deal_momentum=row.deal_momentum,
            pull_score=row.pull_score,
            one_line_summary=one_line,
        ))

    return DealTimelineResponse(
        deal_id=deal.id,
        prospect_company=deal.prospect_company,
        prospect_name=deal.prospect_name,
        status=deal.status,
        current_stage=deal.current_stage,
        total_calls=deal.total_calls,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# PMF Signals endpoints
# ---------------------------------------------------------------------------

@router.get("/pmf/signals", response_model=PmfSignalsResponse)
async def get_pmf_weekly_signals(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get latest PMF weekly digest with 5 dimensions. Admin only."""
    member = await _verify_team_member(db, team_id, user.user_id)
    if member.role == "member":
        raise HTTPException(403, "PMF signals are admin-only")

    from app.models.pmf_weekly_digest import PmfWeeklyDigest

    result = await db.execute(
        select(PmfWeeklyDigest)
        .where(PmfWeeklyDigest.team_id == team_id)
        .order_by(desc(PmfWeeklyDigest.week_start))
        .limit(1)
    )
    digest = result.scalar_one_or_none()
    if not digest:
        return PmfSignalsResponse()

    def _parse_items(data):
        if not data or not isinstance(data, list):
            return []
        return [PmfSignalItem(**item) for item in data if isinstance(item, dict)]

    # Compute top-level stars for each dimension
    feature_items = _parse_items(digest.feature_demand)
    objection_items = _parse_items(digest.objections)
    competitor_items = _parse_items(digest.competitors)

    # Dimension stars = max stars among items, or 0 if empty
    feature_stars = max((i.stars for i in feature_items), default=0) if feature_items else 0
    objection_stars = max((i.stars for i in objection_items), default=0) if objection_items else 0
    competitor_stars = max((i.stars for i in competitor_items), default=0) if competitor_items else 0

    return PmfSignalsResponse(
        week_start=digest.week_start,
        total_calls=digest.total_calls,
        feature_demand=feature_items,
        feature_demand_stars=feature_stars,
        objections=objection_items,
        objections_stars=objection_stars,
        competitors=competitor_items,
        competitors_stars=competitor_stars,
        buy_signal_rate=digest.buy_signal_rate,
        buy_signal_stars=digest.buy_signal_stars or 0,
        buy_signal_prev_rate=digest.buy_signal_prev_rate,
        pain_clarity_rate=digest.pain_clarity_rate,
        pain_clarity_stars=digest.pain_clarity_stars or 0,
        pain_clarity_top_pain=digest.pain_clarity_top_pain,
        pain_clarity_prev_rate=digest.pain_clarity_prev_rate,
        narrative=digest.narrative,
    )


@router.get("/pmf/signals/{signal_type}/{value}/calls", response_model=PmfSignalCallsResponse)
async def get_pmf_signal_calls(
    signal_type: str,
    value: str,
    team_id: uuid.UUID = Query(...),
    days: int = Query(30, ge=7, le=365),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated calls where a specific PMF signal was detected."""
    from app.models.meeting import Meeting

    member = await _verify_team_member(db, team_id, user.user_id)
    if member.role == "member":
        raise HTTPException(403, "PMF signals are admin-only")

    since = _date_range(days)

    # Map signal_type to the appropriate table/query
    if signal_type == "feature":
        meeting_ids_q = (
            select(CallFeatureRequest.meeting_id)
            .where(CallFeatureRequest.team_id == team_id, CallFeatureRequest.call_date >= since.date(), CallFeatureRequest.feature == value)
            .distinct()
        )
    elif signal_type == "objection":
        meeting_ids_q = (
            select(CallObjection.meeting_id)
            .where(CallObjection.team_id == team_id, CallObjection.call_date >= since.date(), CallObjection.objection == value)
            .distinct()
        )
    elif signal_type == "competitor":
        meeting_ids_q = (
            select(CallCompetitor.meeting_id)
            .where(CallCompetitor.team_id == team_id, CallCompetitor.call_date >= since.date(), CallCompetitor.competitor == value)
            .distinct()
        )
    elif signal_type == "pain":
        meeting_ids_q = (
            select(CallPainPoint.meeting_id)
            .where(CallPainPoint.team_id == team_id, CallPainPoint.call_date >= since.date(), CallPainPoint.pain == value)
            .distinct()
        )
    elif signal_type == "buy_signal":
        meeting_ids_q = (
            select(CallIntelligence.meeting_id)
            .where(CallIntelligence.team_id == team_id, CallIntelligence.created_at >= since, CallIntelligence.has_committed_next_step == True)  # noqa: E712
            .distinct()
        )
    else:
        raise HTTPException(400, f"Invalid signal_type: {signal_type}")

    # Count
    count_result = await db.execute(select(func.count()).select_from(meeting_ids_q.subquery()))
    total = count_result.scalar() or 0

    # Get calls
    base = (
        select(
            Meeting.id.label("meeting_id"),
            Meeting.title,
            Meeting.created_at.label("date"),
            Meeting.duration_seconds,
            CallIntelligence.pull_score,
            CallIntelligence.deal_momentum,
            CallIntelligence.validated_stage,
            CallIntelligence.crm_stage_label,
            CallIntelligence.call_health,
            CallIntelligence.has_committed_next_step,
        )
        .join(CallIntelligence, CallIntelligence.meeting_id == Meeting.id)
        .where(Meeting.id.in_(meeting_ids_q))
        .order_by(desc(Meeting.created_at))
        .offset(offset).limit(limit)
    )
    result = await db.execute(base)
    calls = [
        DealStageCall(
            meeting_id=row.meeting_id,
            title=row.title,
            date=row.date,
            duration_seconds=row.duration_seconds,
            pull_score=row.pull_score,
            deal_momentum=row.deal_momentum,
            validated_stage=row.validated_stage,
            crm_stage_label=row.crm_stage_label,
            call_health=row.call_health,
            has_committed_next_step=row.has_committed_next_step,
        )
        for row in result.all()
    ]

    return PmfSignalCallsResponse(calls=calls, total=total, signal_type=signal_type, signal_value=value)


@router.get("/pmf/history", response_model=PmfHistoryResponse)
async def get_pmf_history(
    team_id: uuid.UUID = Query(...),
    weeks: int = Query(8, ge=1, le=52),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get weekly PMF history for trend charts."""
    member = await _verify_team_member(db, team_id, user.user_id)
    if member.role == "member":
        raise HTTPException(403, "PMF signals are admin-only")

    from app.models.pmf_weekly_digest import PmfWeeklyDigest

    result = await db.execute(
        select(PmfWeeklyDigest)
        .where(PmfWeeklyDigest.team_id == team_id)
        .order_by(desc(PmfWeeklyDigest.week_start))
        .limit(weeks)
    )
    digests = result.scalars().all()

    entries = []
    for d in reversed(digests):  # oldest first
        # Compute stars for grouped dimensions
        feat_stars = max((i.get("stars", 1) for i in (d.feature_demand or []) if isinstance(i, dict)), default=0)
        obj_stars = max((i.get("stars", 1) for i in (d.objections or []) if isinstance(i, dict)), default=0)
        comp_stars = max((i.get("stars", 1) for i in (d.competitors or []) if isinstance(i, dict)), default=0)

        entries.append(PmfHistoryEntry(
            week_start=d.week_start,
            total_calls=d.total_calls,
            feature_demand_stars=feat_stars,
            objections_stars=obj_stars,
            competitors_stars=comp_stars,
            buy_signal_stars=d.buy_signal_stars or 0,
            pain_clarity_stars=d.pain_clarity_stars or 0,
        ))

    return PmfHistoryResponse(weeks=entries)


# ---------------------------------------------------------------------------
# Aggregation query helpers
# ---------------------------------------------------------------------------

async def _aggregate_pain_points(
    db: AsyncSession, team_id: uuid.UUID, since: datetime, limit: int = 20,
    created_by: uuid.UUID | None = None,
) -> list[PainPointAggregation]:
    """Group pain points by text, count mentions, collect rich mentions."""
    query = (
        select(
            CallPainPoint.pain,
            func.count(CallPainPoint.id).label("mention_count"),
            func.array_agg(CallPainPoint.exact_quote).label("quotes"),
            func.array_agg(CallPainPoint.meeting_id).label("call_ids"),
            func.json_agg(
                func.json_build_object(
                    'meeting_id', cast(CallPainPoint.meeting_id, String),
                    'exact_quote', CallPainPoint.exact_quote,
                    'speaker_name', CallPainPoint.speaker_name,
                    'prospect_company', CallPainPoint.prospect_company,
                    'timestamp_in_call', CallPainPoint.timestamp_in_call,
                    'call_date', cast(CallPainPoint.call_date, String),
                )
            ).label("mentions_json"),
        )
        .where(
            CallPainPoint.team_id == team_id,
            CallPainPoint.call_date >= since.date(),
        )
    )
    if created_by:
        query = query.where(CallPainPoint.created_by == created_by)
    result = await db.execute(
        query
        .group_by(CallPainPoint.pain)
        .order_by(desc("mention_count"))
        .limit(limit)
    )
    rows = result.all()
    return [
        PainPointAggregation(
            pain=row.pain,
            mention_count=row.mention_count,
            quotes=[q for q in (row.quotes or []) if q],
            mentions=_parse_mentions(row.mentions_json),
            call_ids=list(set(row.call_ids or [])),
        )
        for row in rows
    ]


async def _aggregate_feature_requests(
    db: AsyncSession, team_id: uuid.UUID, since: datetime, limit: int = 20,
    created_by: uuid.UUID | None = None,
) -> list[FeatureRequestAggregation]:
    """Group feature requests by text, count occurrences."""
    query = (
        select(
            CallFeatureRequest.feature,
            func.count(CallFeatureRequest.id).label("request_count"),
            func.array_agg(CallFeatureRequest.urgency).label("urgencies"),
            func.array_agg(CallFeatureRequest.meeting_id).label("call_ids"),
            func.json_agg(
                func.json_build_object(
                    'meeting_id', cast(CallFeatureRequest.meeting_id, String),
                    'exact_quote', CallFeatureRequest.exact_quote,
                    'speaker_name', CallFeatureRequest.speaker_name,
                    'prospect_company', CallFeatureRequest.prospect_company,
                    'timestamp_in_call', CallFeatureRequest.timestamp_in_call,
                    'call_date', cast(CallFeatureRequest.call_date, String),
                )
            ).label("mentions_json"),
        )
        .where(
            CallFeatureRequest.team_id == team_id,
            CallFeatureRequest.call_date >= since.date(),
        )
    )
    if created_by:
        query = query.where(CallFeatureRequest.created_by == created_by)
    result = await db.execute(
        query
        .group_by(CallFeatureRequest.feature)
        .order_by(desc("request_count"))
        .limit(limit)
    )
    rows = result.all()
    return [
        FeatureRequestAggregation(
            feature=row.feature,
            request_count=row.request_count,
            urgency_breakdown=_count_values(row.urgencies),
            mentions=_parse_mentions(row.mentions_json),
            call_ids=list(set(row.call_ids or [])),
        )
        for row in rows
    ]


async def _aggregate_competitors(
    db: AsyncSession, team_id: uuid.UUID, since: datetime, limit: int = 20,
    created_by: uuid.UUID | None = None,
) -> list[CompetitorAggregation]:
    """Group competitor mentions, compute sentiment breakdown."""
    query = (
        select(
            CallCompetitor.competitor,
            func.count(CallCompetitor.id).label("mention_count"),
            func.array_agg(CallCompetitor.sentiment).label("sentiments"),
            func.array_agg(CallCompetitor.what_they_like).label("likes"),
            func.array_agg(CallCompetitor.what_they_dislike).label("dislikes"),
            func.array_agg(CallCompetitor.meeting_id).label("call_ids"),
            func.json_agg(
                func.json_build_object(
                    'meeting_id', cast(CallCompetitor.meeting_id, String),
                    'exact_quote', CallCompetitor.exact_quote,
                    'speaker_name', CallCompetitor.speaker_name,
                    'prospect_company', CallCompetitor.prospect_company,
                    'timestamp_in_call', CallCompetitor.timestamp_in_call,
                    'call_date', cast(CallCompetitor.call_date, String),
                )
            ).label("mentions_json"),
        )
        .where(
            CallCompetitor.team_id == team_id,
            CallCompetitor.call_date >= since.date(),
        )
    )
    if created_by:
        query = query.where(CallCompetitor.created_by == created_by)
    result = await db.execute(
        query
        .group_by(CallCompetitor.competitor)
        .order_by(desc("mention_count"))
        .limit(limit)
    )
    rows = result.all()
    return [
        CompetitorAggregation(
            competitor=row.competitor,
            mention_count=row.mention_count,
            sentiment_breakdown=_count_values(row.sentiments),
            likes=[l for l in (row.likes or []) if l],
            dislikes=[d for d in (row.dislikes or []) if d],
            mentions=_parse_mentions(row.mentions_json),
            call_ids=list(set(row.call_ids or [])),
        )
        for row in rows
    ]


async def _aggregate_objections(
    db: AsyncSession, team_id: uuid.UUID, since: datetime, limit: int = 20,
    created_by: uuid.UUID | None = None,
) -> list[ObjectionAggregation]:
    """Group objections, compute resolution rate."""
    query = (
        select(
            CallObjection.objection,
            func.count(CallObjection.id).label("count"),
            CallObjection.category,
            func.array_agg(CallObjection.how_handled).label("handled_values"),
            func.array_agg(CallObjection.meeting_id).label("call_ids"),
            func.json_agg(
                func.json_build_object(
                    'meeting_id', cast(CallObjection.meeting_id, String),
                    'exact_quote', CallObjection.exact_quote,
                    'speaker_name', CallObjection.speaker_name,
                    'prospect_company', CallObjection.prospect_company,
                    'timestamp_in_call', CallObjection.timestamp_in_call,
                    'call_date', cast(CallObjection.call_date, String),
                )
            ).label("mentions_json"),
        )
        .where(
            CallObjection.team_id == team_id,
            CallObjection.call_date >= since.date(),
        )
    )
    if created_by:
        query = query.where(CallObjection.created_by == created_by)
    result = await db.execute(
        query
        .group_by(CallObjection.objection, CallObjection.category)
        .order_by(desc("count"))
        .limit(limit)
    )
    rows = result.all()
    return [
        ObjectionAggregation(
            objection=row.objection,
            count=row.count,
            category=row.category,
            resolution_rate=_resolution_rate(row.handled_values),
            mentions=_parse_mentions(row.mentions_json),
            call_ids=list(set(row.call_ids or [])),
        )
        for row in rows
    ]


async def _compute_pmf_trend(
    db: AsyncSession, team_id: uuid.UUID, since: datetime,
    created_by: uuid.UUID | None = None,
) -> list[PMFTrendPoint]:
    """Compute weekly PMF signal averages."""
    week_start = func.date_trunc("week", CallIntelligence.created_at).label("week_start")
    query = (
        select(
            week_start,
            func.avg(CallIntelligence.pull_score).label("avg_pull"),
            func.avg(CallIntelligence.pain_fit_score).label("avg_pain_fit"),
            func.avg(CallIntelligence.icp_fit_score).label("avg_icp_fit"),
            func.count(CallIntelligence.id).label("call_count"),
        )
        .where(
            CallIntelligence.team_id == team_id,
            CallIntelligence.created_at >= since,
            CallIntelligence.pull_score.isnot(None),
        )
    )
    if created_by:
        query = query.where(CallIntelligence.created_by == created_by)
    result = await db.execute(
        query
        .group_by(week_start)
        .order_by(week_start)
    )
    rows = result.all()
    return [
        PMFTrendPoint(
            period_start=row.week_start.date() if row.week_start else since.date(),
            period_end=(row.week_start + timedelta(days=6)).date() if row.week_start else since.date(),
            avg_pull_score=round(float(row.avg_pull), 1) if row.avg_pull else None,
            avg_pain_fit=round(float(row.avg_pain_fit), 1) if row.avg_pain_fit else None,
            avg_icp_fit=round(float(row.avg_icp_fit), 1) if row.avg_icp_fit else None,
            call_count=row.call_count,
        )
        for row in rows
    ]


async def _aggregate_questions(
    db: AsyncSession, team_id: uuid.UUID, since: datetime, limit: int = 20,
    created_by: uuid.UUID | None = None,
) -> list[QuestionAggregation]:
    """Group questions by text, count frequency."""
    query = (
        select(
            CallQuestion.question,
            func.count(CallQuestion.id).label("ask_count"),
            func.array_agg(CallQuestion.category).label("categories"),
            func.array_agg(CallQuestion.meeting_id).label("call_ids"),
            func.bool_or(CallQuestion.was_answered == False).label("has_unanswered"),  # noqa: E712
            func.json_agg(
                func.json_build_object(
                    'meeting_id', cast(CallQuestion.meeting_id, String),
                    'exact_quote', CallQuestion.exact_quote,
                    'speaker_name', CallQuestion.speaker_name,
                    'prospect_company', CallQuestion.prospect_company,
                    'timestamp_in_call', CallQuestion.timestamp_in_call,
                    'call_date', cast(CallQuestion.call_date, String),
                )
            ).label("mentions_json"),
        )
        .where(
            CallQuestion.team_id == team_id,
            CallQuestion.call_date >= since.date(),
        )
    )
    if created_by:
        query = query.where(CallQuestion.created_by == created_by)
    result = await db.execute(
        query
        .group_by(CallQuestion.question)
        .order_by(desc("ask_count"))
        .limit(limit)
    )
    rows = result.all()
    return [
        QuestionAggregation(
            question=row.question,
            ask_count=row.ask_count,
            categories=[c for c in (row.categories or []) if c],
            mentions=_parse_mentions(row.mentions_json),
            call_ids=list(set(row.call_ids or [])),
            has_unanswered=bool(row.has_unanswered),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _parse_mentions(mentions_json: list | None) -> list[MentionDetail]:
    """Parse json_agg results into MentionDetail objects."""
    if not mentions_json:
        return []
    result = []
    for m in mentions_json:
        if not isinstance(m, dict):
            continue
        result.append(MentionDetail(
            meeting_id=m.get("meeting_id"),
            exact_quote=m.get("exact_quote"),
            speaker_name=m.get("speaker_name"),
            prospect_company=m.get("prospect_company"),
            timestamp_in_call=m.get("timestamp_in_call"),
            call_date=m.get("call_date"),
        ))
    return result


def _count_values(values: list | None) -> dict[str, int]:
    """Count non-null values in an aggregated array."""
    if not values:
        return {}
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    return counts


def _resolution_rate(handled_values: list | None) -> float | None:
    """Compute resolution rate from how_handled values."""
    if not handled_values:
        return None
    total = len([v for v in handled_values if v])
    if total == 0:
        return None
    resolved = len([v for v in handled_values if v and v.lower() in ("resolved", "addressed", "overcome")])
    return round(resolved / total, 2)
