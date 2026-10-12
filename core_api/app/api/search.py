"""
Full-text search endpoint using PostgreSQL tsvector + title ILIKE.

Returns grouped results: call title matches and transcript content matches,
each with counts for the frontend Cmd+K modal.
"""

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.memoir_summary import MemoirSummary
from app.models.team import TeamMember
from app.models.transcription import Transcription
from app.schemas.intelligence import EnhancedSearchResponse, EnhancedSearchResult
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.storage import create_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_memoir_subquery():
    """DISTINCT ON to get latest memoir summary per meeting."""
    return (
        select(
            MemoirSummary.meeting_id,
            MemoirSummary.summary_excerpt,
            MemoirSummary.primary_participant_name,
            MemoirSummary.primary_participant_company,
        )
        .distinct(MemoirSummary.meeting_id)
        .order_by(MemoirSummary.meeting_id, MemoirSummary.version.desc())
        .subquery("latest_memoir")
    )


def _latest_tx_subquery():
    """Max version per meeting for transcription joins."""
    return (
        select(
            Transcription.meeting_id,
            func.max(Transcription.version).label("max_version"),
        )
        .group_by(Transcription.meeting_id)
        .subquery("latest_tx")
    )


async def _read_full_text(storage, full_text_path: str) -> str | None:
    """Pure R2 read. No DB. Used by the caller to fan out parallel reads
    BEFORE we hold the DB session for ts_headline calls. Returns None on
    failure so the caller can decide whether to fall back to '...' or skip."""
    if not full_text_path:
        return None
    try:
        return await storage.read_text(full_text_path)
    except Exception as e:
        logger.debug(f"R2 full_text read failed for {full_text_path}: {e}")
        return None


async def _read_segments(storage, segments_path: str | None) -> list | None:
    """Pure R2 read for segments JSON. No DB. None on failure."""
    if not segments_path:
        return None
    try:
        return await storage.read_json(segments_path)
    except Exception as e:
        logger.debug(f"R2 segments read failed for {segments_path}: {e}")
        return None


async def _ts_headline(db: AsyncSession, full_text: str | None, q: str) -> str:
    """Pure DB call. ts_headline runs on Postgres against the provided
    text. Caller already read the text from R2 — no R2 hold during the
    DB call. Falls back to a Python substring snippet if ts_headline
    returns nothing or the text is missing."""
    if not full_text:
        return "..."
    try:
        result = await db.execute(
            text(
                "SELECT ts_headline('english', :doc, plainto_tsquery('english', :query), "
                "'StartSel=<b>, StopSel=</b>, MaxWords=35, MinWords=15')"
            ),
            {"doc": full_text, "query": q},
        )
        headline = result.scalar()
        if headline:
            return headline
    except Exception as e:
        logger.debug(f"ts_headline failed: {e}")
    # Python fallback — no extra R2 read needed because caller already
    # passed in the full_text.
    q_lower = q.lower()
    pos = full_text.lower().find(q_lower)
    if pos >= 0:
        start = max(0, pos - 80)
        end = min(len(full_text), pos + len(q) + 80)
        return ("..." if start > 0 else "") + full_text[start:end] + ("..." if end < len(full_text) else "")
    return (full_text[:150] + "...") if len(full_text) > 150 else full_text


def _find_timestamp_in_segments(segments: list | None, snippet: str) -> int | None:
    """Pure in-memory anchor match. No I/O. Caller already loaded segments
    from R2 in parallel."""
    if not segments or not snippet:
        return None
    clean = re.sub(r"<[^>]+>", "", snippet).strip().lower()
    words = clean.split()
    if len(words) < 3:
        return None
    anchor = " ".join(words[:5])
    for seg in segments:
        seg_text = seg.get("text", "").lower()
        if anchor in seg_text:
            return int(seg.get("start", 0))
    anchor_short = " ".join(words[:3])
    for seg in segments:
        seg_text = seg.get("text", "").lower()
        if anchor_short in seg_text:
            return int(seg.get("start", 0))
    return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/search", response_model=EnhancedSearchResponse)
async def search_meetings(
    q: str = Query(..., min_length=1, description="Search query"),
    team_id: uuid.UUID = Query(..., description="Team to search within"),
    result_type: str = Query("all", description="Filter: all, call, or transcript"),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search across meeting titles and transcript content.

    Returns grouped results with counts for the Cmd+K search modal.
    """
    # Verify team membership
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this team")

    is_member = member.role == "member"
    user_uuid = uuid.UUID(user.user_id)

    # Escape LIKE wildcards in user input
    q_escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    latest_memoir = _latest_memoir_subquery()

    # ── Query builder helpers ──────────────────────────────────────

    async def _call_search():
        """Title ILIKE search — returns (results, total_count)."""
        if result_type == "transcript":
            return [], 0

        base = (
            select(
                Meeting.id.label("meeting_id"),
                Meeting.title,
                Meeting.created_at,
                latest_memoir.c.summary_excerpt,
                latest_memoir.c.primary_participant_name,
                latest_memoir.c.primary_participant_company,
            )
            .outerjoin(latest_memoir, Meeting.id == latest_memoir.c.meeting_id)
            .where(
                Meeting.team_id == team_id,
                Meeting.title.ilike(f"%{q_escaped}%"),
            )
        )
        if is_member:
            base = base.where(Meeting.created_by == user_uuid)

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        count_result = await db.execute(count_q)
        total = count_result.scalar() or 0

        # Results
        results_q = base.order_by(Meeting.created_at.desc())
        results_q = results_q.offset((page - 1) * per_page).limit(per_page)
        result = await db.execute(results_q)
        rows = result.all()

        items = []
        for row in rows:
            snippet = row.summary_excerpt or row.title or "..."
            items.append(
                EnhancedSearchResult(
                    meeting_id=row.meeting_id,
                    title=row.title,
                    prospect_name=row.primary_participant_name,
                    prospect_company=row.primary_participant_company,
                    snippet=snippet,
                    score=1.0,
                    date=row.created_at,
                    result_type="call",
                    timestamp_seconds=None,
                )
            )
        return items, total

    async def _transcript_search():
        """Full-text tsvector search — returns (results, total_count)."""
        if result_type == "call":
            return [], 0

        ts_query = func.plainto_tsquery("english", q)
        rank = func.ts_rank(Transcription.search_vector, ts_query).label("rank")
        latest_tx = _latest_tx_subquery()

        base = (
            select(
                Meeting.id.label("meeting_id"),
                Meeting.title,
                Meeting.created_at,
                rank,
                Transcription.full_text_path,
                Transcription.segments_path,
                latest_memoir.c.primary_participant_name,
                latest_memoir.c.primary_participant_company,
            )
            .join(Transcription, Transcription.meeting_id == Meeting.id)
            .join(
                latest_tx,
                (latest_tx.c.meeting_id == Transcription.meeting_id)
                & (latest_tx.c.max_version == Transcription.version),
            )
            .outerjoin(latest_memoir, Meeting.id == latest_memoir.c.meeting_id)
            .where(
                Meeting.team_id == team_id,
                Transcription.search_vector.op("@@")(ts_query),
            )
        )
        if is_member:
            base = base.where(Meeting.created_by == user_uuid)

        # Count
        count_q = select(func.count()).select_from(base.subquery())
        count_result = await db.execute(count_q)
        total = count_result.scalar() or 0

        # Results
        results_q = base.order_by(rank.desc())
        results_q = results_q.offset((page - 1) * per_page).limit(per_page)
        result = await db.execute(results_q)
        rows = result.all()

        # Generate snippets + timestamps.
        # Previously this loop ran serially: read full_text from R2,
        # call ts_headline on DB, read segments from R2, scan in memory
        # — N rows × (1 R2 read + 1 DB call + 1 R2 read) all in series
        # while holding the request's DB session through the whole chain.
        # On a 5-result page that was 10 sequential R2 round-trips inside
        # the DB transaction.
        # New shape: fan out ALL R2 reads in parallel first (no DB hold),
        # then serial ts_headline calls (DB-bound but ~5 ms each), then
        # pure in-memory timestamp anchor-match against the already-
        # loaded segments.
        storage = create_storage()
        full_text_tasks = [_read_full_text(storage, row.full_text_path) for row in rows]
        segments_tasks = [_read_segments(storage, row.segments_path) for row in rows]
        full_texts, all_segments = await asyncio.gather(
            asyncio.gather(*full_text_tasks),
            asyncio.gather(*segments_tasks),
        )

        snippets: list[str] = []
        for ft in full_texts:
            snippets.append(await _ts_headline(db, ft, q))

        items = []
        for row, snippet, segs in zip(rows, snippets, all_segments):
            ts = _find_timestamp_in_segments(segs, snippet)
            items.append(
                EnhancedSearchResult(
                    meeting_id=row.meeting_id,
                    title=row.title,
                    prospect_name=row.primary_participant_name,
                    prospect_company=row.primary_participant_company,
                    snippet=snippet,
                    score=float(row.rank) if row.rank else 0.0,
                    date=row.created_at,
                    result_type="transcript",
                    timestamp_seconds=ts,
                )
            )
        return items, total

    # ── Run queries (sequential — shared db session) ──────────────
    call_results, call_total = await _call_search()
    transcript_results, transcript_total = await _transcript_search()

    return EnhancedSearchResponse(
        call_results=call_results,
        transcript_results=transcript_results,
        call_total=call_total,
        transcript_total=transcript_total,
        query=q,
    )
