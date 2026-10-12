"""
Meeting management API routes.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import uuid
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.calendar import CalendarEvent
from app.models.meeting import Meeting
from app.models.meeting_note import MeetingNote
from app.models.memoir_summary import MemoirSummary
from app.models.speaker import MeetingParticipant, SpeakerProfile
from app.models.team import TeamMember
from app.models.transcription import Transcription
from app.schemas.meeting import (
    LinkUploadRequest,
    MeetingCreate,
    MeetingDetailResponse,
    MeetingListResponse,
    MeetingNotesResponse,
    MeetingNotesUpdate,
    MeetingResponse,
    MeetingStatusResponse,
)
from app.clients.auth_client import batch_get_users
from app.services import meeting_status_cache, team_membership_cache
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.clients import OrchestratorClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

ZOOM_MEETING_ID_PATTERN = re.compile(r"zoom\.us/(?:j|wc)/(\d+)")

_YOUTUBE_DOMAINS = {"youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"}


def _is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video URL."""
    try:
        hostname = urllib.parse.urlparse(url).hostname
        if not hostname:
            return False
        return hostname.lower() in _YOUTUBE_DOMAINS
    except Exception:
        return False


def _normalize_meeting_url(url: Optional[str]) -> Optional[str]:
    """Strip query params. Extract platform-specific meeting ID for dedup.

    Returns None when input is None — caller should skip dedup entirely
    rather than try to compare nulls.
    """
    if not url:
        return None
    url = re.split(r'[?#]', url)[0].rstrip('/')
    zoom = re.search(r'zoom\.us/(?:j|wc)/(\d+)', url)
    if zoom:
        return f"zoom:{zoom.group(1)}"
    meet = re.search(r'meet\.google\.com/([a-z\-]+)', url)
    if meet:
        return f"meet:{meet.group(1)}"
    teams = re.search(r'teams\.(microsoft|live)\.com', url)
    if teams:
        return f"teams:{url}"
    return url


def _user_participated_predicate(user_id: str, user_email: str):
    """Build an OR'd predicate matching meetings where the user participated.

    Two evidence sources are unioned:
      1. meeting_participants → speaker_profiles match by internal_user_id
         or case-insensitive email (post-mapping; populated after Type 5
         polish).
      2. calendar_events.attendees JSONB contains the user's email
         (calendar-imported meetings, even ones the bot didn't transcribe).
         Uses `jsonb_array_elements` so the match is case-insensitive
         regardless of how the calendar provider cased the address.

    Returns a SQL boolean expression suitable for `query.where(predicate)`.
    """
    from sqlalchemy import or_ as sa_or, exists, func as sa_func, text as sa_text

    user_uuid = uuid.UUID(user_id)
    email_lower = (user_email or "").strip().lower()

    by_speaker = exists().where(
        MeetingParticipant.meeting_id == Meeting.id,
        MeetingParticipant.speaker_profile_id == SpeakerProfile.id,
        sa_or(
            SpeakerProfile.internal_user_id == user_uuid,
            sa_func.lower(SpeakerProfile.email) == email_lower,
        ),
    )
    if not email_lower:
        return by_speaker  # no email → fall back to speaker FK only

    # Calendar attendee match — case-insensitive scan of the JSONB array.
    # Bound email is passed via a literal-like text expr so SQLAlchemy
    # parameterises correctly. The lateral jsonb_array_elements expands
    # each attendee object; we lowercase its `email` key for comparison.
    #
    # `jsonb_typeof(...) = 'array'` guards against both SQL NULL and the
    # JSONB scalar `null` shape (the latter would otherwise raise
    # "cannot extract elements from a scalar" inside the LATERAL).
    by_calendar = sa_text(
        """EXISTS (
            SELECT 1 FROM calendar_events ce,
                LATERAL jsonb_array_elements(ce.attendees) AS att
            WHERE ce.linked_meeting_id = meetings.id
              AND jsonb_typeof(ce.attendees) = 'array'
              AND lower(att->>'email') = :__participant_email
        )"""
    ).bindparams(__participant_email=email_lower)
    return sa_or(by_speaker, by_calendar)


async def _verify_team_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: str
) -> TeamMember:
    """Verify user is a member of the team. Returns TeamMember."""
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


async def _get_meeting_with_access(
    db: AsyncSession, meeting_id: uuid.UUID, user_id: str
) -> Meeting:
    """Get a meeting and verify the user has access."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    await _verify_team_membership(db, meeting.team_id, user_id)
    return meeting


@router.post("", response_model=MeetingResponse, status_code=201)
async def create_meeting(
    body: MeetingCreate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new meeting and dispatch a bot to join it."""
    # Verify team membership
    await _verify_team_membership(db, body.team_id, user.user_id)

    # Source-vs-URL contract: web_recorder meetings must NOT carry a
    # meeting_url. The audio is the meeting; there's no external URL.
    # Catches bugs in callers that accidentally pass a URL for this source.
    if body.source == "web_recorder" and body.meeting_url:
        raise HTTPException(
            400,
            "meeting_url must be empty for source=web_recorder",
        )

    # Dedup: only meaningful when the new meeting has a URL AND the
    # capture mode is bot (not client-side recording). Tab-audio and
    # web_recorder always create a fresh row — each session is distinct.
    is_client_capture = body.capture_mode in ("tab_audio", "web_recorder")
    if body.meeting_url and not is_client_capture:
        normalized_url = _normalize_meeting_url(body.meeting_url)
        active_result = await db.execute(
            select(Meeting).where(
                Meeting.team_id == body.team_id,
                Meeting.status.in_(["scheduled", "pending", "joining", "waiting_in_lobby", "recording"]),
            )
        )
        for existing in active_result.scalars().all():
            existing_norm = _normalize_meeting_url(existing.meeting_url)
            if existing_norm is None:
                continue  # active meeting with NULL url (web_recorder/upload) — can't collide
            if existing_norm == normalized_url:
                # Return existing meeting instead of creating duplicate
                dedup_user_map = await batch_get_users([user.user_id])
                dedup_auth_user = dedup_user_map.get(user.user_id)
                dedup_name = dedup_auth_user.get("name") if dedup_auth_user else None
                return MeetingResponse(
                    id=existing.id,
                    team_id=existing.team_id,
                    created_by=existing.created_by,
                    created_by_name=dedup_name,
                    title=existing.title,
                    meeting_url=existing.meeting_url,
                    status=existing.status,
                    source=existing.source,
                    bot_job_id=existing.bot_job_id,
                    started_at=existing.started_at,
                    ended_at=existing.ended_at,
                    duration_seconds=existing.duration_seconds,
                    has_recording=existing.recording_path is not None,
                    scheduled_start_at=existing.scheduled_start_at,
                    created_at=existing.created_at,
                )

    # Check plan limit for calls per month
    from app.core.plan_limits import check_plan_limit
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(func.count())
        .select_from(Meeting)
        .where(
            Meeting.team_id == body.team_id,
            Meeting.created_at >= month_start,
        )
    )
    current_month_calls = count_result.scalar() or 0
    await check_plan_limit("calls_per_month", str(body.team_id), current_month_calls)

    is_scheduled = body.scheduled_start_at is not None
    # Client-side capture (extension tab-audio + new web_recorder) skip
    # bot dispatch and enter status=recording immediately. Same lifecycle
    # path for both; only difference is web_recorder has no meeting_url.
    is_client_capture = body.capture_mode in ("tab_audio", "web_recorder")

    # Client-side recordings start immediately in "recording" state (no bot)
    if is_client_capture:
        initial_status = "recording"
    elif is_scheduled:
        initial_status = "scheduled"
    else:
        initial_status = "pending"

    meeting = Meeting(
        team_id=body.team_id,
        created_by=uuid.UUID(user.user_id),
        title=body.title,
        meeting_url=body.meeting_url,
        source=body.source,
        status=initial_status,
        scheduled_start_at=body.scheduled_start_at,
        language=body.language,
    )

    if is_client_capture:
        meeting.started_at = datetime.now(timezone.utc)

    # Extract Zoom meeting ID from URL for dedup with Zoom cloud recordings
    # (meeting_url is now Optional — guard before searching).
    if body.meeting_url:
        zoom_match = ZOOM_MEETING_ID_PATTERN.search(body.meeting_url)
        if zoom_match:
            meeting.zoom_meeting_id = zoom_match.group(1)

    db.add(meeting)
    await db.flush()

    # Set recording paths at creation time (file doesn't exist yet, but path is known)
    meeting.original_recording_path = f"meetings/{meeting.id}/recording.wav"
    meeting.recording_path = f"meetings/{meeting.id}/recording.wav"

    # Only dispatch bot for immediate bot recordings — skip for scheduled,
    # tab-audio, and web_recorder (all client-side capture modes have no bot).
    if not is_scheduled and not is_client_capture:
        try:
            orchestrator = OrchestratorClient(settings.ORCHESTRATOR_URL)
            result = await orchestrator.create_bot(
                meeting_url=body.meeting_url,
                callback_url=settings.BOT_CALLBACK_URL,
                meeting_id=str(meeting.id),
            )
            meeting.bot_job_id = result.get("jobId") or result.get("job_id")
        except Exception as e:
            logger.error(f"Failed to create bot for meeting {meeting.id}: {e}")
            meeting.status = "failed"

    await db.commit()
    await db.refresh(meeting)

    # Seed the /status Redis cache with the initial row. Without this,
    # the very first poll(s) from the extension would miss the cache
    # and fall back to DB until the first bot webhook lands — which
    # can be 5-8 s after dispatch. Seeding here makes the cache HIT
    # rate ~100% for the entire meeting lifetime.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting.id)

    # Fetch creator name for the response
    create_user_map = await batch_get_users([user.user_id])
    create_auth_user = create_user_map.get(user.user_id)
    create_name = create_auth_user.get("name") if create_auth_user else None

    return MeetingResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=create_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        source=meeting.source,
        bot_job_id=meeting.bot_job_id,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        has_recording=meeting.recording_path is not None,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
    )


ALLOWED_UPLOAD_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".webm", ".mp4", ".mov", ".mkv"}


@router.post("/upload", response_model=MeetingResponse, status_code=201)
async def upload_meeting(
    file: UploadFile,
    team_id: uuid.UUID = Form(...),
    title: str = Form(""),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a pre-recorded audio/video file for transcription."""
    # Verify team membership
    await _verify_team_membership(db, team_id, user.user_id)

    # Check plan limit for calls per month
    from app.core.plan_limits import check_plan_limit

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(func.count())
        .select_from(Meeting)
        .where(
            Meeting.team_id == team_id,
            Meeting.created_at >= month_start,
        )
    )
    current_month_calls = count_result.scalar() or 0
    await check_plan_limit("calls_per_month", str(team_id), current_month_calls)

    # Validate file extension
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )

    # Create meeting record
    meeting = Meeting(
        team_id=team_id,
        created_by=uuid.UUID(user.user_id),
        title=title.strip() or filename,
        meeting_url=None,  # uploads have no meeting URL — migration 040 backfilled legacy "upload://local" rows
        source="upload",
        status="waiting_for_transcription",
    )
    db.add(meeting)
    await db.flush()

    meeting.bot_job_id = f"upload-{meeting.id}"

    # Save uploaded file and extract audio to wav via ffmpeg
    recordings_dir = Path(settings.BOT_RECORDINGS_DIR)
    upload_dir = recordings_dir / "uploads" / str(meeting.id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    output_wav = upload_dir / "recording.wav"

    # Set paths at creation time
    original_filename = f"original{ext}"
    meeting.original_recording_path = f"uploads/{meeting.id}/{original_filename}"
    meeting.recording_path = f"uploads/{meeting.id}/recording.wav"

    tmp_file = None
    try:
        # Write uploaded bytes to temp file
        tmp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=ext, dir=str(upload_dir)
        )
        content = await file.read()
        tmp_file.write(content)
        tmp_file.flush()
        tmp_input = tmp_file.name
        tmp_file.close()

        # Keep original uploaded file (rename temp to permanent path)
        original_path = upload_dir / original_filename
        shutil.copy2(tmp_input, str(original_path))

        # Extract/convert audio to 16kHz mono wav
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tmp_input,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(output_wav),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"ffmpeg failed for meeting {meeting.id}: {stderr.decode()}")
            raise HTTPException(400, "Failed to process audio file. Ensure it is a valid audio/video file.")
    finally:
        # Clean up temp input file
        if tmp_file and os.path.exists(tmp_file.name):
            os.unlink(tmp_file.name)

    # Upload to R2 immediately if configured
    if settings.STORAGE_BACKEND == "s3":
        try:
            from videoclip_common.storage import create_storage
            storage = create_storage()
            await storage.save_file(meeting.recording_path, str(output_wav))
            if original_path.exists():
                await storage.save_file(meeting.original_recording_path, str(original_path))
            logger.info(f"Meeting {meeting.id}: uploaded recording to R2")
        except Exception as e:
            logger.error(f"Meeting {meeting.id}: failed to upload to R2: {e}")

    # Insert transcription job — mirror orchestrator params exactly
    params = {
        "input_audio_path": f"uploads/{meeting.id}/recording.wav",
        "output_base_path": f"meetings/{meeting.id}/v1/",
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "engine": "faster-whisper",
        "model_size": "medium",
        "processing_mode": "path_b",
        "phone_mode": True,
        "multilingual": True,
        "diarization": True,
        "vocab_correction": True,
        "llm_correction": True,
        "enable_intelligence": True,
        "post_processing_type": "memoir",
        "max_retries": 3,
        "meeting_title": meeting.title or "",
        "team_id": str(meeting.team_id),
    }
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": str(meeting.id),
            "bjid": f"upload-{meeting.id}",
            "params": json.dumps(params),
        },
    )

    await db.commit()
    await db.refresh(meeting)

    logger.info(f"Meeting {meeting.id} created from upload, queued for transcription")

    # Fetch creator name for the response
    create_user_map = await batch_get_users([user.user_id])
    create_auth_user = create_user_map.get(user.user_id)
    create_name = create_auth_user.get("name") if create_auth_user else None

    return MeetingResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=create_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        source=meeting.source,
        bot_job_id=meeting.bot_job_id,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        has_recording=meeting.recording_path is not None,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
    )


MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB

MIME_TO_EXT = {
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}


@router.post("/upload-link", response_model=MeetingResponse, status_code=201)
async def upload_meeting_from_link(
    body: LinkUploadRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a media file from a URL and queue it for transcription."""
    # Verify team membership
    await _verify_team_membership(db, body.team_id, user.user_id)

    # Check plan limit for calls per month
    from app.core.plan_limits import check_plan_limit

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(func.count())
        .select_from(Meeting)
        .where(
            Meeting.team_id == body.team_id,
            Meeting.created_at >= month_start,
        )
    )
    current_month_calls = count_result.scalar() or 0
    await check_plan_limit("calls_per_month", str(body.team_id), current_month_calls)

    url = body.url.strip()

    # Validate URL scheme
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "URL must start with http:// or https://")

    is_youtube = _is_youtube_url(url)

    if not is_youtube:
        # Validate media format before creating the meeting record
        url_path = parsed.path.split("?")[0]
        ext = os.path.splitext(url_path)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            ext = ""
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    head_resp = await client.head(url)
                    content_type = head_resp.headers.get("content-type", "").split(";")[0].strip()
                    ext = MIME_TO_EXT.get(content_type, "")
            except Exception:
                pass
            if not ext:
                raise HTTPException(
                    400,
                    f"Unsupported media format. Allowed extensions: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
                )

    # Create meeting record
    meeting = Meeting(
        team_id=body.team_id,
        created_by=uuid.UUID(user.user_id),
        title=body.title.strip() if body.title else ("YouTube upload" if is_youtube else "Link upload"),
        meeting_url=url,
        source="link_upload",
        status="waiting_for_transcription",
    )
    db.add(meeting)
    await db.flush()

    meeting.bot_job_id = f"link-upload-{meeting.id}"
    meeting.original_recording_path = None
    meeting.recording_path = f"uploads/{meeting.id}/recording.wav"

    # Insert transcription job — download + conversion deferred to TranscriptionBase
    params = {
        "input_audio_path": f"uploads/{meeting.id}/recording.wav",
        "output_base_path": f"meetings/{meeting.id}/v1/",
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "engine": "faster-whisper",
        "model_size": "medium",
        "processing_mode": "path_b",
        "phone_mode": True,
        "multilingual": True,
        "diarization": True,
        "vocab_correction": True,
        "llm_correction": True,
        "enable_intelligence": True,
        "post_processing_type": "memoir",
        "recording_source": "link_upload",
        "max_retries": 3,
        "meeting_title": meeting.title or "",
        "team_id": str(meeting.team_id),
    }
    if is_youtube:
        params["youtube_url"] = url
    else:
        params["media_url"] = url
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": str(meeting.id),
            "bjid": f"link-upload-{meeting.id}",
            "params": json.dumps(params),
        },
    )

    await db.commit()
    await db.refresh(meeting)

    logger.info(f"Meeting {meeting.id} created from link upload, queued for transcription")

    create_user_map = await batch_get_users([user.user_id])
    create_auth_user = create_user_map.get(user.user_id)
    create_name = create_auth_user.get("name") if create_auth_user else None

    return MeetingResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=create_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        source=meeting.source,
        bot_job_id=meeting.bot_job_id,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        has_recording=meeting.recording_path is not None,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
    )


@router.get("/activity")
async def meetings_activity(
    team_id: uuid.UUID = Query(...),
    days: int = Query(60, ge=1, le=365),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-day meeting counts for the last `days` days (default 60).

    Powers the activity heatmap on the Meetings v2 page. Counts by
    COALESCE(started_at, created_at)::date so back-dated bot recordings
    land on the day they actually happened, not when the row was inserted.
    Days with zero meetings are returned explicitly so the heatmap has a
    contiguous range without client-side gap-filling.

    Response shape: {"days": [{"date": "YYYY-MM-DD", "count": int}], "total": int}
    """
    await _verify_team_membership(db, team_id, user.user_id)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)

    # GROUP BY day, COUNT(*). Use COALESCE so meetings with started_at NULL
    # (manual upload, pending bot) still count on their created_at day.
    day_expr = func.date(func.coalesce(Meeting.started_at, Meeting.created_at))
    stmt = (
        select(day_expr.label("day"), func.count(Meeting.id).label("count"))
        .where(
            Meeting.team_id == team_id,
            func.coalesce(Meeting.started_at, Meeting.created_at) >= start,
        )
        .group_by(day_expr)
    )
    result = await db.execute(stmt)
    by_day: dict[date_cls, int] = {row.day: row.count for row in result.all()}

    # Build a contiguous list — fill zero days explicitly.
    days_out = []
    for i in range(days):
        d = start + timedelta(days=i)
        days_out.append({"date": d.isoformat(), "count": int(by_day.get(d, 0))})
    return {"days": days_out, "total": sum(d["count"] for d in days_out)}


@router.get("/stats")
async def meetings_stats(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Team-wide meeting stats — total count, total minutes, this-week count.

    Powers the static stat header on the Meetings page. Intentionally
    ignores the platform/time/scope filters from the list endpoint so the
    header stays a stable reference point as the user narrows the list.

    Members see only their own meetings (consistent with the list
    endpoint's RBAC).
    """
    member = await _verify_team_membership(db, team_id, user.user_id)

    from sqlalchemy import or_ as sa_or

    base_where = [Meeting.team_id == team_id]
    if member.role == "member":
        # Members see only meetings they hosted OR participated in —
        # matches the list endpoint's per-scope access rules so the
        # header total tracks what's actually visible below it.
        base_where.append(
            sa_or(
                Meeting.created_by == uuid.UUID(user.user_id),
                _user_participated_predicate(user.user_id, user.email),
            )
        )

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    stmt = select(
        func.count(Meeting.id),
        func.coalesce(func.sum(Meeting.duration_seconds), 0),
        func.count(Meeting.id).filter(
            func.coalesce(Meeting.started_at, Meeting.created_at) >= week_ago
        ),
    ).where(*base_where)
    total, total_seconds, this_week = (await db.execute(stmt)).one()
    return {
        "total": int(total or 0),
        "total_seconds": int(total_seconds or 0),
        "this_week": int(this_week or 0),
    }


@router.get("/week-activity")
async def meetings_week_activity(
    team_id: uuid.UUID = Query(...),
    scope: Optional[str] = Query(None, pattern="^(hosted|shared|all)$"),
    range_start: datetime = Query(...),
    range_end: datetime = Query(...),
    tz_offset_minutes: int = Query(
        0,
        ge=-720,
        le=840,
        description=(
            "Minutes east of UTC for the user's local timezone (positive for "
            "east, e.g., IST=330). Shifts the activity-date GROUP BY so the "
            "dots align with what the user sees on the DateStrip."
        ),
    ),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Per-day meeting counts over a date range, for the v3 DateStrip.

    Plan: plan/meetings-events-v3-list.md §6.1. Returns
    `{ "2026-05-24": 4, "2026-05-25": 0, ... }` — keys are ISO dates
    in UTC, values are meeting counts. Applies the same scope/role
    rules as `list_meetings`.
    """
    member = await _verify_team_membership(db, team_id, user.user_id)

    if scope == "all" and member.role not in ("owner", "admin"):
        raise HTTPException(
            403, "scope=all is restricted to team owners and admins"
        )

    # Activity basis: started_at if present, otherwise scheduled_start_at,
    # otherwise created_at. Mirrors formatMeetingTime() in the frontend.
    activity_ts = func.coalesce(
        Meeting.started_at, Meeting.scheduled_start_at, Meeting.created_at
    )
    # Shift by the user's TZ offset before extracting the date — see the
    # equivalent comment in calendar_events_week_activity.
    shifted_ts = activity_ts + func.make_interval(
        0, 0, 0, 0, 0, tz_offset_minutes, 0
    )
    activity_date = func.date(shifted_ts).label("activity_date")

    base = select(activity_date, func.count().label("c")).where(
        Meeting.team_id == team_id,
        activity_ts >= range_start,
        activity_ts <= range_end,
    )

    if scope == "hosted":
        base = base.where(Meeting.created_by == uuid.UUID(user.user_id))
    elif scope == "shared":
        base = base.where(
            Meeting.created_by != uuid.UUID(user.user_id),
            _user_participated_predicate(user.user_id, user.email),
        )
    elif scope == "all":
        pass  # role already validated; no extra predicate
    else:
        # Legacy no-scope path: role-gated for members.
        if member.role == "member":
            base = base.where(Meeting.created_by == uuid.UUID(user.user_id))

    base = base.group_by(activity_date)
    result = await db.execute(base)
    return {row.activity_date.isoformat(): row.c for row in result.all()}


@router.get("", response_model=MeetingListResponse)
async def list_meetings(
    team_id: uuid.UUID = Query(...),
    status: Optional[str] = Query(None),
    has_recording: Optional[bool] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    source: Optional[str] = Query(None),
    crm_push_status: Optional[str] = Query(None),
    confidence_min: Optional[int] = Query(None, ge=0, le=100),
    confidence_max: Optional[int] = Query(None, ge=0, le=100),
    filter_ids: Optional[str] = Query(None, description="Comma-separated meeting IDs to filter by"),
    scope: Optional[str] = Query(None, regex="^(hosted|shared|all)$", description="hosted = I created; shared = I participated; all = every team meeting (admin/owner only)"),
    platform: Optional[str] = Query(None, regex="^(meet|zoom|teams)$", description="Meeting platform by URL pattern"),
    date_basis: str = Query(
        "created_at",
        pattern="^(created_at|activity)$",
        description=(
            "Which timestamp to apply start_date/end_date against. "
            "'created_at' (default, v2 behavior) — filters on when the row "
            "was inserted, which can disagree with when the meeting actually "
            "happened for synced calendar events. 'activity' (v3) — filters "
            "on coalesce(started_at, scheduled_start_at, created_at), the "
            "same basis the /week-activity endpoint groups by, so the "
            "day-detail list agrees with the strip dots."
        ),
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List meetings for a team with server-side filtering and status counts."""
    member = await _verify_team_membership(db, team_id, user.user_id)

    # Latest memoir summary per meeting (DISTINCT ON meeting_id, ordered by version DESC)
    latest_memoir = (
        select(
            MemoirSummary.meeting_id,
            MemoirSummary.crm_push_status,
            MemoirSummary.overall_confidence_score,
            MemoirSummary.summary_excerpt,
            MemoirSummary.primary_participant_name,
            MemoirSummary.primary_participant_company,
        )
        .distinct(MemoirSummary.meeting_id)
        .order_by(MemoirSummary.meeting_id, MemoirSummary.version.desc())
        .subquery("latest_memoir")
    )

    # Base query: meeting + left join latest memoir
    base_query = (
        select(
            Meeting,
            latest_memoir.c.crm_push_status,
            latest_memoir.c.overall_confidence_score,
            latest_memoir.c.summary_excerpt,
            latest_memoir.c.primary_participant_name,
            latest_memoir.c.primary_participant_company,
        )
        .outerjoin(latest_memoir, Meeting.id == latest_memoir.c.meeting_id)
        .where(Meeting.team_id == team_id)
    )

    # Role gate for the legacy no-scope path. The scoped paths below
    # apply their own access rules (hosted = mine, shared = participated,
    # all = admin/owner only) so we only fall back to the strict member
    # guard when no scope was requested.
    if scope is None and member.role == "member":
        base_query = base_query.where(Meeting.created_by == uuid.UUID(user.user_id))

    # Apply common filters (date range, source, crm, confidence) — shared by status counts + main query.
    # date_basis controls which timestamp the start_date/end_date filter applies to:
    #   created_at — v2 behavior (preserved as default)
    #   activity — coalesce(started_at, scheduled_start_at, created_at);
    #              same basis as /week-activity so the v3 strip + day list agree.
    date_filter_col = (
        func.coalesce(Meeting.started_at, Meeting.scheduled_start_at, Meeting.created_at)
        if date_basis == "activity"
        else Meeting.created_at
    )
    if start_date:
        base_query = base_query.where(date_filter_col >= start_date)
    if end_date:
        base_query = base_query.where(date_filter_col <= end_date)
    if source:
        base_query = base_query.where(Meeting.source == source)
    if crm_push_status:
        base_query = base_query.where(latest_memoir.c.crm_push_status == crm_push_status)
    if confidence_min is not None:
        base_query = base_query.where(latest_memoir.c.overall_confidence_score >= confidence_min)
    if confidence_max is not None:
        base_query = base_query.where(latest_memoir.c.overall_confidence_score <= confidence_max)
    if filter_ids:
        try:
            ids = [uuid.UUID(x.strip()) for x in filter_ids.split(",") if x.strip()]
            if ids:
                base_query = base_query.where(Meeting.id.in_(ids))
        except (ValueError, AttributeError):
            pass  # Invalid UUIDs — ignore filter

    # Scope semantics:
    #   hosted = meetings I created (any role)
    #   shared = meetings I participated in but did not create — combines
    #            speaker-mapping evidence with calendar attendee match,
    #            so members can see meetings they were invited to / spoke
    #            in even when they didn't initiate the recording.
    #   all    = every meeting in the team. Admin/owner only — members
    #            are denied here to keep the principle that they only
    #            see what they're part of.
    if scope == "hosted":
        base_query = base_query.where(Meeting.created_by == uuid.UUID(user.user_id))
    elif scope == "shared":
        base_query = base_query.where(
            Meeting.created_by != uuid.UUID(user.user_id),
            _user_participated_predicate(user.user_id, user.email),
        )
    elif scope == "all":
        if member.role not in ("admin", "owner"):
            raise HTTPException(403, "Only admins and owners can view all meetings")
        # No additional predicate — see every meeting in the team.

    # Platform: hybrid signal. Prefer the dedicated ID column when set
    # (set by zoom_cloud / teams_cloud recording flows); fall through to
    # URL pattern matching for everything else (manual, calendar, ext).
    # Keep patterns aligned with the frontend's platformFromUrl() so
    # nothing classified by the UI gets filtered out by the backend.
    from sqlalchemy import or_ as sa_or
    if platform == "zoom":
        base_query = base_query.where(
            sa_or(
                Meeting.zoom_meeting_id.isnot(None),
                Meeting.meeting_url.ilike("%zoom%"),
            )
        )
    elif platform == "teams":
        base_query = base_query.where(
            sa_or(
                Meeting.teams_meeting_id.isnot(None),
                Meeting.meeting_url.ilike("%teams.microsoft%"),
            )
        )
    elif platform == "meet":
        # Meet has no ID column. Match on URL AND require Zoom/Teams
        # IDs are absent so a stray Meet-URL annotation on a Zoom row
        # (theoretical) doesn't double-classify.
        base_query = base_query.where(
            Meeting.zoom_meeting_id.is_(None),
            Meeting.teams_meeting_id.is_(None),
            Meeting.meeting_url.ilike("%meet.google.com%"),
        )

    # --- Status counts (before applying status filter) ---
    # The base_query always carries the LEFT OUTER JOIN to latest_memoir
    # because the row-fetch path uses memoir columns in its SELECT. For
    # the COUNT path, the JOIN is dead weight unless a memoir filter is
    # actually active. Building a parallel join-free count_query knocks
    # the worst case from 28 s (prod 2026-05-29) to sub-second on the
    # common no-memoir-filter path.
    _memoir_filters_active = (
        crm_push_status is not None
        or confidence_min is not None
        or confidence_max is not None
    )
    if _memoir_filters_active:
        # Need the JOIN — reuse the existing base_query as the count
        # source. Slow but correct.
        count_base = base_query
    else:
        # Mirror the same Meeting-side filters onto a join-free query.
        count_base = select(Meeting).where(Meeting.team_id == team_id)
        if scope is None and member.role == "member":
            count_base = count_base.where(Meeting.created_by == uuid.UUID(user.user_id))
        if start_date:
            count_base = count_base.where(date_filter_col >= start_date)
        if end_date:
            count_base = count_base.where(date_filter_col <= end_date)
        if source:
            count_base = count_base.where(Meeting.source == source)
        if filter_ids:
            try:
                ids = [uuid.UUID(x.strip()) for x in filter_ids.split(",") if x.strip()]
                if ids:
                    count_base = count_base.where(Meeting.id.in_(ids))
            except (ValueError, AttributeError):
                pass
        if scope == "hosted":
            count_base = count_base.where(Meeting.created_by == uuid.UUID(user.user_id))
        elif scope == "shared":
            count_base = count_base.where(
                Meeting.created_by != uuid.UUID(user.user_id),
                _user_participated_predicate(user.user_id, user.email),
            )
        if platform == "zoom":
            count_base = count_base.where(
                sa_or(Meeting.zoom_meeting_id.isnot(None), Meeting.meeting_url.ilike("%zoom%"))
            )
        elif platform == "teams":
            count_base = count_base.where(
                sa_or(Meeting.teams_meeting_id.isnot(None), Meeting.meeting_url.ilike("%teams.microsoft%"))
            )
        elif platform == "meet":
            count_base = count_base.where(
                Meeting.zoom_meeting_id.is_(None),
                Meeting.teams_meeting_id.is_(None),
                Meeting.meeting_url.ilike("%meet.google.com%"),
            )

    count_sub = count_base.with_only_columns(Meeting.status).subquery("filtered_statuses")
    count_result = await db.execute(
        select(count_sub.c.status, func.count())
        .group_by(count_sub.c.status)
    )
    raw_counts = {row[0]: row[1] for row in count_result.all()}

    # Build counts dict with friendly keys
    counts = {
        "all": sum(raw_counts.values()),
        "completed": raw_counts.get("completed", 0),
        "recording": raw_counts.get("recording", 0),
        "processing": raw_counts.get("processing", 0) + raw_counts.get("waiting_for_transcription", 0),
        "failed": raw_counts.get("failed", 0),
        "recorded": 0,  # computed separately below
    }

    # "recorded" count: meetings with a recording_path (uses same join-free
    # base when no memoir filters are active).
    if has_recording is not None or True:  # always compute for tabs
        recorded_sub = count_base.with_only_columns(func.count()).where(Meeting.recording_path.isnot(None))
        recorded_result = await db.execute(recorded_sub)
        counts["recorded"] = recorded_result.scalar() or 0

    # --- Apply status / has_recording filter for actual results ---
    query = base_query
    if status:
        if status == "processing":
            query = query.where(Meeting.status.in_(["processing", "waiting_for_transcription"]))
        else:
            query = query.where(Meeting.status == status)
    if has_recording is True:
        query = query.where(Meeting.recording_path.isnot(None))

    # Count total (with all filters including status)
    total_sub = query.with_only_columns(func.count())
    total_result = await db.execute(total_sub)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(Meeting.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    rows = result.all()

    # Batch fetch creator names from auth service
    creator_ids = list({str(row[0].created_by) for row in rows})
    user_map = await batch_get_users(creator_ids) if creator_ids else {}

    # Build responses — memoir data comes from the JOIN, no N+1
    meeting_responses = []
    for row in rows:
        m = row[0]  # Meeting object
        m_crm_push_status = row[1]
        m_confidence_score = row[2]
        m_summary_excerpt = row[3]
        m_participant_name = row[4]
        m_participant_company = row[5]

        auth_user = user_map.get(str(m.created_by))
        created_by_name = auth_user.get("name") if auth_user else None

        meeting_responses.append(
            MeetingResponse(
                id=m.id,
                team_id=m.team_id,
                created_by=m.created_by,
                created_by_name=created_by_name,
                title=m.title,
                meeting_url=m.meeting_url,
                status=m.status,
                intelligence_status=m.intelligence_status,
                source=m.source,
                bot_job_id=m.bot_job_id,
                started_at=m.started_at,
                ended_at=m.ended_at,
                duration_seconds=m.duration_seconds,
                confidence_score=m_confidence_score,
                crm_push_status=m_crm_push_status,
                has_recording=m.recording_path is not None,
                scheduled_start_at=m.scheduled_start_at,
                created_at=m.created_at,
                summary_excerpt=m_summary_excerpt,
                primary_participant_name=m_participant_name,
                primary_participant_company=m_participant_company,
            )
        )

    return MeetingListResponse(
        meetings=meeting_responses,
        total=total,
        page=page,
        per_page=per_page,
        counts=counts,
        current_user_role=member.role,
    )


@router.get("/{meeting_id}", response_model=MeetingDetailResponse)
async def get_meeting(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get meeting details with content availability flags."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Check what content exists (any version)
    transcript_result = await db.execute(
        select(Transcription.id).where(Transcription.meeting_id == meeting_id).limit(1)
    )
    has_transcript = transcript_result.scalar_one_or_none() is not None

    # Get latest memoir summary for CRM status
    memoir_result = await db.execute(
        select(MemoirSummary)
        .where(MemoirSummary.meeting_id == meeting_id)
        .order_by(MemoirSummary.version.desc())
        .limit(1)
    )
    memoir = memoir_result.scalar_one_or_none()

    has_analysis = meeting.analysis_path is not None
    has_memoir = memoir is not None

    crm_push_status = memoir.crm_push_status if memoir else None
    confidence_score = memoir.overall_confidence_score if memoir else None
    external_share_approved = memoir.external_share_approved if memoir else False

    # Count external participants (those with speaker_profile but no internal_user_id)
    from app.models.speaker import MeetingParticipant, SpeakerProfile
    ext_count_result = await db.execute(
        select(func.count(MeetingParticipant.id))
        .join(SpeakerProfile, MeetingParticipant.speaker_profile_id == SpeakerProfile.id)
        .where(
            MeetingParticipant.meeting_id == meeting_id,
            SpeakerProfile.internal_user_id.is_(None),
            SpeakerProfile.email.isnot(None),
        )
    )
    external_participants_count = ext_count_result.scalar() or 0

    # Fetch creator name
    detail_user_map = await batch_get_users([str(meeting.created_by)])
    detail_auth_user = detail_user_map.get(str(meeting.created_by))
    detail_created_by_name = detail_auth_user.get("name") if detail_auth_user else None

    return MeetingDetailResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=detail_created_by_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        intelligence_status=meeting.intelligence_status,
        source=meeting.source,
        bot_job_id=meeting.bot_job_id,
        started_at=meeting.started_at,
        recording_started_at=meeting.recording_started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        confidence_score=confidence_score,
        crm_push_status=crm_push_status,
        has_recording=meeting.recording_path is not None,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
        has_transcript=has_transcript,
        has_analysis=has_analysis,
        has_memoir=has_memoir,
        memoir_deal_id=meeting.memoir_deal_id,
        external_share_approved=external_share_approved,
        external_participants_count=external_participants_count,
    )


@router.post("/{meeting_id}/admit-detected")
async def admit_detected(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extension fast-path: host's click-on-Admit was detected in the Meet UI.

    Phase 2 (2026-05-14): bot's own admission detection takes 1-8 s
    (sink-input polling + DOM check + WR cooldown). The host's CLICK is
    available locally in the host's browser within ~50 ms — the extension's
    content script already detects it for the optimistic "Starting to
    record..." UI flip. This endpoint extends that fast-path to the
    backend so meeting.status and recording_started_at are committed
    immediately, not 1-8 s later when the bot's own webhook lands.

    With Phase 1's pre-started FFmpeg, the bot is already capturing the
    moment we mark recording_started_at — so the button flip to "Stop
    Recording" is honest (audio IS being captured).

    Idempotent: both this endpoint and the bot's joined_meeting webhook
    will fire (the bot's eventually fires regardless). Only the first
    to land actually commits; the second is a no-op via the existing
    forward-only status guard.

    Failure fallback: if this POST fails or the extension never fires
    it (auto-admit, keyboard shortcut, click matcher misses), the
    bot's joined_meeting webhook arrives later and runs the same code
    path. Worst case = today's behavior.
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Forward-only guard — same pattern as _handle_joined_meeting via
    # webhooks.py atomic UPDATE. Only commit if status is currently in
    # the pre-recording set; otherwise the bot's webhook already won.
    _PRE_RECORDING_STATUSES = ("pending", "scheduled", "joining", "waiting_in_lobby")
    if meeting.status not in _PRE_RECORDING_STATUSES:
        return {"status": "noop", "reason": f"already {meeting.status}"}

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.status.in_(_PRE_RECORDING_STATUSES),
        )
        .values(
            status="recording",
            started_at=now,
            recording_started_at=now,
        )
    )
    await db.commit()
    if result.rowcount > 0:
        logger.info(f"Meeting {meeting_id} status -> recording (via extension fast-path)")
        # Cascade to /status cache. Without this, the cache lags at
        # status="joining" until the bot's own joined_meeting webhook
        # lands (1-8 s later) — completely neutralising this fast path
        # for any poll that hits the cache HIT branch.
        await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)
        return {"status": "recording"}
    return {"status": "noop", "reason": "race with bot webhook"}


@router.get("/{meeting_id}/status", response_model=MeetingStatusResponse)
async def get_meeting_status(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight polling endpoint for the Chrome extension.

    Returns ONLY (status, intelligence_status, recording_started_at) — the
    three fields the floating-button polling loop needs to decide whether
    to flip Joining... -> Starting to record... -> Stop Recording.

    Read path (in order):
      1. Try Redis hash `meeting_status:{id}` — populated by the meeting
         CREATE handler and refreshed by every status-mutating webhook.
         Hit path = one Redis HGETALL + one small TeamMember auth lookup.
      2. On miss, fall through to a single SELECT that joins Meeting
         and TeamMember (the historical hot path). Backfill the cache
         on the way out so subsequent polls land on the fast path.

    Background — observed 2026-05-14: extension polling /api/meetings/:id
    every 1.5 s caused the connection pool to saturate (15-18 s connection
    hold times in prod logs) which in turn stalled webhook handlers like
    recording_started for 14 s. The status endpoint already trimmed the
    DB fan-out from 5-6 SELECTs to one; the Redis cache eliminates the
    Meeting row hit entirely on cache hits, leaving only the cheap
    TeamMember membership check on the DB.
    """
    user_uuid = uuid.UUID(user.user_id)

    # ── Fast path: Redis HIT ────────────────────────────────────────
    # Module-level pooled client — no per-call TCP handshake. The pool
    # keeps a small set of warm connections alive for the worker's
    # lifetime, so HGETALL stays ms-level even at sustained polling.
    cached = await meeting_status_cache.read(
        meeting_status_cache.get_shared_redis(), meeting_id
    )

    if cached is not None:
        # Auth: verify the user is in the cached team. P3.3 (2026-05-31):
        # delegates to the Redis-backed team_membership_cache so this
        # check skips the DB on the common case (~5/sec/user polling).
        if not await team_membership_cache.is_team_member(
            db, uuid.UUID(cached["team_id"]), user_uuid
        ):
            raise HTTPException(404, "Meeting not found")
        return MeetingStatusResponse(
            status=cached["status"],
            intelligence_status=cached["intelligence_status"],
            recording_started_at=cached["recording_started_at"],
        )

    # ── Slow path: cache miss → DB + backfill ───────────────────────
    result = await db.execute(
        select(
            Meeting.team_id,
            Meeting.status,
            Meeting.intelligence_status,
            Meeting.recording_started_at,
        )
        .join(TeamMember, TeamMember.team_id == Meeting.team_id)
        .where(
            Meeting.id == meeting_id,
            TeamMember.user_id == user_uuid,
            or_(
                TeamMember.invite_status == "accepted",
                TeamMember.invite_status.is_(None),
            ),
        )
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Meeting not found")

    # Backfill the cache so subsequent polls hit the fast path.
    # publish() will DEL instead of writing if the status is terminal,
    # which is correct: a terminal cache entry would just churn.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    return MeetingStatusResponse(
        status=row.status,
        intelligence_status=row.intelligence_status,
        recording_started_at=row.recording_started_at,
    )


@router.post("/{meeting_id}/approve-external-share")
async def approve_external_share(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve sharing meeting summary emails with external participants.

    Only the recorder (meeting creator) can approve. Once approved, external
    participant emails are dispatched immediately.
    """
    import secrets as _secrets

    from sqlalchemy.orm import selectinload

    from app.clients.notification_client import send_meeting_summary_email
    from app.models.notification import EmailSuppression, EmailUnsubscribeToken
    from app.models.speaker import MeetingParticipant, SpeakerProfile
    from app.models.team import Team
    from app.services.speaker_mapping import is_internal_participant

    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Only recorder can approve
    if str(meeting.created_by) != user.user_id:
        raise HTTPException(403, "Only the meeting recorder can approve external sharing")

    # Get latest MemoirSummary
    memoir_result = await db.execute(
        select(MemoirSummary)
        .where(MemoirSummary.meeting_id == meeting_id)
        .order_by(MemoirSummary.version.desc())
        .limit(1)
    )
    memoir = memoir_result.scalar_one_or_none()
    if not memoir:
        raise HTTPException(404, "No summary found for this meeting")

    # Mark as approved
    memoir.external_share_approved = True
    memoir.external_share_approved_at = datetime.now(timezone.utc)
    memoir.external_share_approved_by = uuid.UUID(user.user_id)
    await db.commit()

    # Load team for domain matching
    team_result = await db.execute(select(Team).where(Team.id == meeting.team_id))
    team = team_result.scalar_one_or_none()

    # Send external participant emails immediately
    recipients_count = 0
    if team:
        summary_data = memoir.summary_json or {}

        participants_result = await db.execute(
            select(MeetingParticipant)
            .where(MeetingParticipant.meeting_id == meeting_id)
            .options(selectinload(MeetingParticipant.speaker_profile))
        )
        participants = participants_result.scalars().all()

        platform = None
        if meeting.meeting_url:
            try:
                platform = meeting.meeting_url.split("/")[2]
            except (IndexError, AttributeError):
                pass

        meeting_date = meeting.started_at.isoformat() if meeting.started_at else None
        created_by_str = str(meeting.created_by)

        # P2.1 (2026-05-31): batch the EmailSuppression check. The
        # previous version fired one SELECT per participant inside the
        # send loop — at ~500 ms/round-trip × 5-8 external participants
        # per meeting that was 2-4 s of pool hold per share. Single
        # WHERE IN ( ) over the candidate emails up front; in-memory
        # set check inside the loop. See plan/performance-optimization-
        # audit.md §2.1.
        _candidate_emails: list[str] = []
        for _p in participants:
            _sp = _p.speaker_profile
            if not _sp or not _sp.email:
                continue
            if is_internal_participant(_sp, team):
                continue
            _candidate_emails.append(_sp.email.lower())

        _suppressed: set[str] = set()
        if _candidate_emails:
            _supp_result = await db.execute(
                select(EmailSuppression.email).where(
                    EmailSuppression.email.in_(_candidate_emails),
                    EmailSuppression.team_id == meeting.team_id,
                )
            )
            _suppressed = {row.email for row in _supp_result.all()}

        for participant in participants:
            try:
                sp = participant.speaker_profile
                if not sp or not sp.email:
                    continue

                if is_internal_participant(sp, team):
                    continue

                email = sp.email

                if email.lower() in _suppressed:
                    continue

                # Generate unsubscribe token
                token = _secrets.token_urlsafe(32)
                unsub_token = EmailUnsubscribeToken(
                    token=token,
                    email=email.lower(),
                    team_id=meeting.team_id,
                    event_type="external_summary",
                )
                db.add(unsub_token)
                await db.flush()

                name = sp.display_name or email.split("@")[0]
                unsubscribe_url = f"{settings.FRONTEND_URL}/unsubscribe?token={token}"

                ok = await send_meeting_summary_email(
                    to_email=email,
                    recipient_type="external",
                    recipient_name=name,
                    meeting_id=str(meeting_id),
                    meeting_title=meeting.title or "Untitled Call",
                    meeting_date=meeting_date,
                    duration_seconds=meeting.duration_seconds,
                    platform=platform,
                    summary_data=summary_data,
                    created_by=created_by_str,
                    recipient_email=email,
                    unsubscribe_url=unsubscribe_url,
                    team_id=str(meeting.team_id),
                )
                if ok:
                    recipients_count += 1
                else:
                    logger.warning(
                        f"Meeting {meeting_id}: external-share email to {email} "
                        "not accepted by notification service"
                    )
            except Exception as e:
                logger.error(f"Failed to send external email to participant {participant.id}: {e}")
                continue

        if recipients_count:
            await db.commit()

    return {"status": "shared", "recipients_count": recipients_count}


async def _stop_bot_background(bot_job_id: str, meeting_id: uuid.UUID) -> None:
    """Fire orchestrator.stop_bot in the background after stop_meeting has
    already returned 200 to the client. Errors are swallowed (logged) — the
    bot will time out and clean up via the orchestrator's grace/sigkill
    timer even if this call fails entirely (BOT_STOP_GRACE_MS in
    orchestrator/src/api/router.ts).
    """
    try:
        orchestrator = OrchestratorClient(settings.ORCHESTRATOR_URL)
        await orchestrator.stop_bot(bot_job_id)
    except Exception as e:
        logger.warning(f"Background orchestrator.stop_bot failed for meeting {meeting_id}: {e}")


@router.post("/{meeting_id}/stop")
async def stop_meeting(
    meeting_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stop recording a meeting — schedule the bot to leave.

    Deliberately does NOT commit meeting.status here. The status flip
    to "processing" happens via the bot's MEETING_ENDED webhook
    (_handle_meeting_ended in webhooks.py) AFTER the bot has actually
    ejected from the meeting via page.close(). This is the user-visible
    signal the extension polls for to flip the floating button to
    "Record with Memoir" — gating that on the real ejection (rather
    than the "stop request accepted" moment) keeps the UI honest:
    the button stays on "Stop Recording" while the bot is still
    visible in the participant panel, and only flips once it's gone.

    Returns immediately. The orchestrator -> bot /stop chain runs in
    a background task; the bot's MEETING_ENDED webhook flips status
    when ejection is confirmed.
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # "waiting_in_lobby" is included because concurrent webhook handlers
    # can race the forward-only guard (witnessed 2026-05-14): a late
    # waiting_room_entered retry from the orchestrator can overwrite
    # status="recording" with status="waiting_in_lobby" if the late
    # handler's session was opened before the joined_meeting commit was
    # visible. Until that race is fixed at the source (atomic UPDATE
    # with status guard in WHERE clause), accept the lobby state for
    # stop too — the bot is still running and needs to be torn down
    # regardless of what status the late-event race left behind.
    if meeting.status not in ("pending", "joining", "waiting_in_lobby", "recording", "waiting_for_bot"):
        raise HTTPException(400, f"Cannot stop meeting in {meeting.status} status")

    # Fire orchestrator.stop_bot in background — extension's response
    # doesn't depend on this completing. The bot will fire MEETING_ENDED
    # after page.close() returns, which the existing webhook handler
    # uses to commit status="processing".
    bot_stop_scheduled = False
    if meeting.bot_job_id:
        background_tasks.add_task(_stop_bot_background, meeting.bot_job_id, meeting_id)
        bot_stop_scheduled = True

    return {
        "status": "stop_requested",
        "meeting_id": str(meeting_id),
        "bot_stop_scheduled": bot_stop_scheduled,
    }


@router.post("/{meeting_id}/retranscribe")
async def retranscribe_meeting(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-queue transcription for a completed meeting.

    Creates a new versioned transcription (v2, v3, etc.) without deleting
    previous versions. The new transcription output goes into a versioned
    subdirectory: meetings/{id}/v{N}/
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    if meeting.status not in ("completed", "failed"):
        raise HTTPException(400, f"Cannot retranscribe a meeting in {meeting.status} status")

    # Find the most recent transcription job for bot_job_id (any operation_type).
    # We need this for the bot_job_id used in the new row's FK.
    last_row = await db.execute(
        sa_text(
            "SELECT bot_job_id FROM transcription_jobs "
            "WHERE meeting_id = :mid ORDER BY created_at DESC LIMIT 1"
        ),
        {"mid": str(meeting_id)},
    )
    last_job = last_row.mappings().first()
    if not last_job:
        raise HTTPException(400, "No previous transcription job found for this meeting")

    # Pull params from the FIRST Type 1 (preprocess) job — that's the only
    # operation that carries `input_audio_path`. Downstream jobs (Type 2 CRM,
    # Type 3 intelligence, Type 5 polish, etc.) inherit selectively and drop
    # the audio path. Picking the most-recent row would give us a tail-stage
    # params blob with empty input_audio_path → IsADirectoryError in Type 1.
    type1_row = await db.execute(
        sa_text(
            "SELECT params FROM transcription_jobs "
            "WHERE meeting_id = :mid AND operation_type = 1 "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"mid": str(meeting_id)},
    )
    type1_job = type1_row.mappings().first()
    if not type1_job:
        raise HTTPException(400, "No Type 1 preprocess job found for this meeting — cannot retranscribe")

    prev_params = type1_job["params"]
    if isinstance(prev_params, str):
        prev_params = json.loads(prev_params)
    prev_job = {"bot_job_id": last_job["bot_job_id"]}

    # Inject meeting context for ASR accuracy
    prev_params["meeting_title"] = meeting.title or ""
    # Pull participant names from latest transcription's speaker_map
    t_row = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    _transcription = t_row.scalar_one_or_none()
    if _transcription and _transcription.speaker_map:
        prev_params["participant_names"] = [
            v["name"] for v in _transcription.speaker_map.values()
            if isinstance(v, dict) and v.get("name")
        ]

    # Determine next version number
    max_ver_result = await db.execute(
        select(func.max(Transcription.version)).where(
            Transcription.meeting_id == meeting_id
        )
    )
    max_version = max_ver_result.scalar() or 0
    new_version = max_version + 1

    # Set versioned output path and ensure all pipeline features are enabled
    prev_params["output_base_path"] = f"meetings/{meeting_id}/v{new_version}/"
    prev_params.setdefault("webhook_url", settings.TRANSCRIPTION_WEBHOOK_URL)
    prev_params.setdefault("webhook_headers", {"X-Service-Key": settings.INTERNAL_SERVICE_KEY})
    prev_params.setdefault("engine", "faster-whisper")
    prev_params.setdefault("model_size", "medium")
    prev_params.setdefault("processing_mode", "path_b")
    prev_params.setdefault("phone_mode", True)
    prev_params.setdefault("multilingual", True)
    prev_params.setdefault("diarization", True)
    prev_params.setdefault("vocab_correction", True)
    prev_params.setdefault("llm_correction", True)
    prev_params["enable_intelligence"] = True
    prev_params["post_processing_type"] = "memoir"
    prev_params.setdefault("max_retries", 3)

    # Insert new transcription job with versioned output path
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": str(meeting_id),
            "bjid": prev_job["bot_job_id"],
            "params": json.dumps(prev_params),
        },
    )

    # Clear the Redis progress hash so the loader UI shows the NEW run's
    # stages from 0%, not stale 100% from the previous version. Without
    # this, /processing-status reports preprocess=100, enhance=100, etc.
    # from v(N-1) while only v(N)'s preprocess is actually running →
    # user sees a misleading "everything done except this stage" view.
    try:
        await meeting_status_cache.get_shared_redis().delete(
            f"meeting_progress:{meeting_id}"
        )
    except Exception as e:
        logger.warning(f"Meeting {meeting_id} retranscribe: failed to clear progress hash: {e}")

    # Reset meeting status (keep old records intact)
    meeting.status = "waiting_for_transcription"
    # Clear the email-sent gate so the new pipeline run can send a fresh
    # summary email (FR-17). Without this, summary_email_sent_at from the
    # previous run would suppress both fast and polished email paths.
    meeting.summary_email_sent_at = None
    # Reset intelligence_status so the frontend's allDone gate in
    # processing-status polling doesn't trip. If we leave it at "completed"
    # from v1, the processing-status endpoint reports intelligence=completed
    # + crm_extraction=completed (stale Type 2 row) → frontend's allDone
    # check fires → polling interval is cleared → loader bar freezes at
    # whatever stage was visible at the moment retranscribe was queued.
    meeting.intelligence_status = "pending"
    await db.commit()

    # Cascade to /status + /processing-status caches — retranscribe resets
    # status to "waiting_for_transcription" and intelligence_status to
    # "pending", but neither is set by a webhook so without this the cache
    # serves the prior terminal values until the next pipeline webhook
    # lands (could be 30+ s in).
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    logger.info(f"Meeting {meeting_id} queued for retranscription as v{new_version}")
    return {"status": "queued", "meeting_id": str(meeting_id), "version": new_version}


@router.post("/{meeting_id}/rerun-intelligence")
async def rerun_intelligence(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-queue intelligence pipeline for a completed meeting."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    if meeting.status != "completed":
        raise HTTPException(400, f"Meeting must be completed (current: {meeting.status})")

    t_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    transcription = t_result.scalar_one_or_none()
    if not transcription or not transcription.segments_path:
        raise HTTPException(400, "No transcript available for this meeting")

    speaker_map = transcription.speaker_map or {}
    output_base_path = transcription.segments_path.rsplit("/", 1)[0] + "/"

    intel_params = {
        "segments_path": transcription.segments_path,
        "speaker_map": speaker_map,
        "participant_names": [
            v["name"] for v in speaker_map.values()
            if isinstance(v, dict) and v.get("name")
        ],
        "team_id": str(meeting.team_id),
        "call_date": (meeting.started_at or meeting.created_at).strftime("%Y-%m-%d") if (meeting.started_at or meeting.created_at) else None,
        "output_base_path": output_base_path,
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
    }

    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 3, 'waiting', :params)"
        ),
        {
            "mid": str(meeting_id),
            "bjid": f"intel-rerun-{meeting_id}",
            "params": json.dumps(intel_params),
        },
    )

    meeting.intelligence_status = "processing"
    await db.commit()

    # Cascade — same reasoning as retranscribe. Without this, /processing-
    # status would still report intelligence="completed" until the next
    # _handle_intelligence_complete webhook lands.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    logger.info(f"Meeting {meeting_id} queued for intelligence rerun")
    return {"status": "queued", "meeting_id": str(meeting_id)}


_PROGRESS_STAGES = (
    "preprocess", "enhance", "fast_summary", "diarization",
    "transcribe", "ai_analysis", "crm_sync",
)


async def _read_meeting_progress(redis_url: str, meeting_id) -> tuple[str, dict]:
    """Read the per-stage progress hash from Redis.

    Returns (active_stage, stages) — empty defaults on miss / error.
    The TranscriptionBase pipeline writes this hash as each stage runs;
    its TTL self-cleans expired entries. Failures are non-fatal — the
    /processing-status endpoint serves empty stages and the top-level
    status fields rather than 500ing.
    """
    stages: dict = {}
    active_stage = ""
    try:
        data = await meeting_status_cache.get_shared_redis().hgetall(
            f"meeting_progress:{meeting_id}"
        )
        if data:
            active_stage = data.get("active_stage", "")
            for stage in _PROGRESS_STAGES:
                stages[stage] = {
                    "percent": int(data.get(f"{stage}_percent", 0)),
                    "label": data.get(f"{stage}_label", ""),
                }
    except Exception:
        pass
    return active_stage, stages


@router.get("/{meeting_id}/processing-status")
async def get_processing_status(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Granular processing status for the v3 progress strip + polling.

    Read path (in order):
      1. Try the shared meeting_status:{id} Redis hash — populated by
         the same writers as /status. Hit path: HGETALL + small
         TeamMember auth lookup + HGETALL meeting_progress.
      2. On miss, fall through to the historical DB-driven derivation
         (Meeting + latest T2 transcription_job). cascade_from_db()
         backfills the cache on the way out so subsequent polls hit
         the fast path.

    Read-time inference (same logic in both paths): the top-level
    crm_extraction field is upgraded from "pending" → "processing"
    when meeting_progress.crm_sync.percent > 0. T2 doesn't fire a
    webhook on pending→processing, so the cache can't see that
    transition on its own.
    """
    user_uuid = uuid.UUID(user.user_id)

    # ── Fast path: Redis HIT ────────────────────────────────────────
    # Module-level pooled client — warm connection, no per-call setup.
    cached = await meeting_status_cache.read(
        meeting_status_cache.get_shared_redis(), meeting_id
    )

    if cached is not None:
        # Auth — same TeamMember predicate as /status hit path. P3.3
        # (2026-05-31): routes through Redis-backed team_membership_cache
        # to skip the DB on the common case.
        if not await team_membership_cache.is_team_member(
            db, uuid.UUID(cached["team_id"]), user_uuid
        ):
            raise HTTPException(404, "Meeting not found")

        active_stage, stages = await _read_meeting_progress(
            settings.REDIS_URL, meeting_id
        )

        transcription_done = cached["status"] == "completed"
        crm_status = meeting_status_cache.crm_status_with_processing_inference(
            cached["crm_extraction_status"], stages
        )
        return {
            "transcription": "completed" if transcription_done else cached["status"],
            "summary": "completed" if transcription_done else "pending",
            "intelligence": cached["intelligence_status"] or "pending",
            "crm_extraction": crm_status,
            "active_stage": active_stage,
            "stages": stages,
        }

    # ── Slow path: cache miss → DB derivation + backfill ────────────
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    crm_status_raw = await meeting_status_cache._derive_crm_extraction_status(
        db, meeting_id, meeting.intelligence_status
    )

    active_stage, stages = await _read_meeting_progress(
        settings.REDIS_URL, meeting_id
    )

    transcription_done = meeting.status == "completed"
    intel_status = meeting.intelligence_status or "pending"
    crm_status = meeting_status_cache.crm_status_with_processing_inference(crm_status_raw, stages)

    # Backfill the cache so subsequent polls hit the fast path. The
    # cascade re-derives crm_status (cheap) and writes the full hash.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    return {
        "transcription": "completed" if transcription_done else meeting.status,
        "summary": "completed" if transcription_done else "pending",
        "intelligence": intel_status,
        "crm_extraction": crm_status,
        "active_stage": active_stage,
        "stages": stages,
    }


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a meeting. Requires admin or owner role on the team."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    # Verify admin or owner
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == meeting.team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = member_result.scalar_one_or_none()
    if not member or member.role not in ("admin", "owner"):
        raise HTTPException(403, "Requires admin or owner role")

    await db.delete(meeting)
    await db.commit()


@router.put("/{meeting_id}/notes", response_model=MeetingNotesResponse)
async def upsert_meeting_notes(
    meeting_id: uuid.UUID,
    body: MeetingNotesUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upsert quick notes for the current user on a meeting."""
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    result = await db.execute(
        select(MeetingNote).where(
            MeetingNote.meeting_id == meeting_id,
            MeetingNote.user_id == uuid.UUID(user.user_id),
        )
    )
    note = result.scalar_one_or_none()

    if note:
        note.content = body.content
        note.updated_at = datetime.now(timezone.utc)
    else:
        note = MeetingNote(
            meeting_id=meeting_id,
            user_id=uuid.UUID(user.user_id),
            content=body.content,
        )
        db.add(note)

    await db.commit()
    await db.refresh(note)

    return MeetingNotesResponse(
        meeting_id=note.meeting_id,
        user_id=note.user_id,
        content=note.content,
        updated_at=note.updated_at,
    )


@router.get("/{meeting_id}/notes", response_model=MeetingNotesResponse)
async def get_meeting_notes(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get quick notes for the current user on a meeting."""
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    result = await db.execute(
        select(MeetingNote).where(
            MeetingNote.meeting_id == meeting_id,
            MeetingNote.user_id == uuid.UUID(user.user_id),
        )
    )
    note = result.scalar_one_or_none()

    if not note:
        return MeetingNotesResponse(
            meeting_id=meeting_id,
            user_id=uuid.UUID(user.user_id),
            content="",
            updated_at=None,
        )

    return MeetingNotesResponse(
        meeting_id=note.meeting_id,
        user_id=note.user_id,
        content=note.content,
        updated_at=note.updated_at,
    )


# ─── Email from Call Detail Page ───────────────────────────────────────────────


async def _load_meeting_summary(db: AsyncSession, meeting_id: uuid.UUID):
    """Load P5 summary data from R2 for a meeting.

    Resolution order:
      1. transcriptions.segments_path → summary.json   (T5 polished P5)
      2. transcriptions.segments_path → summary_fast.json (T7 fast P5)
      3. meetings/{id}/v1/summary.json     (T5, no transcriptions row yet)
      4. meetings/{id}/v1/summary_fast.json (T7, no transcriptions row yet)

    The transcriptions row is written only after T5 completes — so during
    the T7→T5 window we have to derive the base dir from the canonical
    meeting layout. Also summary_fast.json is the only file that exists
    until T5 either runs polished P5 or copies fast → summary.json, so
    we always need summary_fast.json as a fallback.

    Returns the structure that email renderers expect:
    {"summary": {"tldr": ..., "action_items": [...], ...}, "meeting": {...}}
    """
    from videoclip_common.storage import create_storage
    storage = create_storage()

    async def _try(key: str) -> dict | None:
        try:
            if await storage.exists(key):
                raw = await storage.read_json(key)
                if isinstance(raw, dict):
                    return raw.get("summary", raw)
        except Exception:
            pass
        return None

    candidate_dirs: list[str] = []
    tx_result = await db.execute(
        sa_text(
            "SELECT segments_path FROM transcriptions "
            "WHERE meeting_id = :mid ORDER BY version DESC LIMIT 1"
        ),
        {"mid": str(meeting_id)},
    )
    tx = tx_result.mappings().first()
    if tx and tx["segments_path"]:
        candidate_dirs.append(tx["segments_path"].rsplit("/", 1)[0])
    # Canonical default layout — covers the pre-T5 window where no
    # transcriptions row exists yet.
    canonical = f"meetings/{meeting_id}/v1"
    if canonical not in candidate_dirs:
        candidate_dirs.append(canonical)

    for base_dir in candidate_dirs:
        for filename in ("summary.json", "summary_fast.json"):
            data = await _try(f"{base_dir}/{filename}")
            if data:
                return data
    return {}


async def _stage_percent(meeting_id, stage: str) -> int:
    """Read `{stage}_percent` from the meeting_progress Redis hash.

    Cheap Redis HMGET — used as a precondition gate on read endpoints that
    would otherwise do a slow R2 HEAD chain only to 404. Three states:

    * Hash exists, `{stage}_percent` set → return its int value.
    * Hash exists, `{stage}_percent` missing → stage hasn't started yet
      (earlier stages have written `active_stage`) → return 0 so callers
      gate to 404.
    * Hash absent entirely → out of the progress-tracking window (TTL on
      `meeting_progress:*` is 1 h). Return 100 so callers fall through to
      the slow R2 path — the file may exist for a completed meeting whose
      progress hash has long since expired.
    """
    try:
        pct, active = await meeting_status_cache.get_shared_redis().hmget(
            f"meeting_progress:{meeting_id}",
            [f"{stage}_percent", "active_stage"],
        )
        if pct is not None:
            return int(pct)
        if active is None:
            return 100
        return 0
    except Exception:
        # Redis unreachable: serve the slow path rather than 5xx — same
        # contract as before the precondition was added.
        return 100


@router.get("/{meeting_id}/summary")
async def get_meeting_summary(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the P5 summary written by the pipeline to summary.json.

    Available from Type 7 (fast-email) onwards — does not depend on
    Type 3 intelligence completion.

    The on-disk shape is ``{"meeting": {...}, "summary": {tldr, ...}}``
    (matches the SUMMARY_USER schema). This endpoint flattens it to a
    single envelope so the UI doesn't need to know about the double
    nesting:

        {
          "tldr": ...,
          "decisions": [...],
          "open_questions": [...],
          "action_items": [...],
          "discussion_threads": [...],
          "meeting": {participants: [...], ...},
        }
    """
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Precondition: short-circuit if T7 (fast P5) hasn't reached 100% yet.
    # _load_meeting_summary loops 4 candidate R2 paths with storage.exists()
    # for each — ~400-500 ms per call when none exist. During the T1→T7
    # window the file is guaranteed not to exist, so we'd be doing 4 R2
    # HEADs per poll tick for no reason. Drop them with a single Redis
    # HGET. fast_summary_percent is written to meeting_progress by
    # TranscriptionBase as T7 runs — when it reaches 100, summary_fast.json
    # is on R2 and the slow path runs as before.
    if await _stage_percent(meeting_id, "fast_summary") < 100:
        raise HTTPException(404, "No summary available for this meeting")

    raw = await _load_meeting_summary(db, meeting_id)
    if not raw:
        raise HTTPException(404, "No summary available for this meeting")

    inner = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
    meeting_meta = raw.get("meeting") if isinstance(raw.get("meeting"), dict) else None

    return {
        "tldr": inner.get("tldr"),
        "decisions": inner.get("decisions") or [],
        "open_questions": inner.get("open_questions") or [],
        "action_items": inner.get("action_items") or [],
        "discussion_threads": inner.get("discussion_threads") or [],
        "meeting": meeting_meta,
    }


@router.get("/{meeting_id}/next-steps")
async def get_meeting_next_steps(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return user-editable Next Steps overrides (milestone + next meeting).

    Lives next to the pipeline-extracted next-step fields on CallIntelligence
    but is a manual override — UI surfaces stored values when present, falls
    back to pipeline-derived values otherwise.
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)
    data = meeting.next_steps_data if isinstance(meeting.next_steps_data, dict) else {}
    return {
        "milestone": data.get("milestone") or "",
        "next_meeting_date": data.get("next_meeting_date") or "",
        "updated_at": data.get("updated_at"),
        "updated_by": data.get("updated_by"),
    }


@router.put("/{meeting_id}/next-steps")
async def update_meeting_next_steps(
    meeting_id: uuid.UUID,
    body: dict,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist user edits to milestone + next_meeting_date.

    Body: {"milestone": str | null, "next_meeting_date": str | null}.
    Either field may be omitted; missing fields are left untouched.
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)
    existing = meeting.next_steps_data if isinstance(meeting.next_steps_data, dict) else {}
    updated = dict(existing)

    if "milestone" in body:
        ms = body.get("milestone")
        updated["milestone"] = ms.strip() if isinstance(ms, str) else None
    if "next_meeting_date" in body:
        nd = body.get("next_meeting_date")
        updated["next_meeting_date"] = nd.strip() if isinstance(nd, str) else None

    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    updated["updated_by"] = str(user.user_id)
    meeting.next_steps_data = updated
    # SQLAlchemy doesn't auto-detect in-place JSONB edits unless we reassign.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(meeting, "next_steps_data")
    await db.commit()
    return {
        "milestone": updated.get("milestone") or "",
        "next_meeting_date": updated.get("next_meeting_date") or "",
        "updated_at": updated.get("updated_at"),
        "updated_by": updated.get("updated_by"),
    }


@router.get("/{meeting_id}/email-preview")
async def email_preview(
    meeting_id: uuid.UUID,
    template: str = "external",
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a rendered HTML preview of the meeting summary email.

    template: "recorder" (full summary), "internal" (team member), "external" (minimal branding)
    """
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)
    summary_data = await _load_meeting_summary(db, meeting_id)

    if not summary_data:
        raise HTTPException(404, "No summary available for this meeting")

    from app.services.email_renderer import build_recorder_email, build_internal_email, build_external_email

    meeting_date = (meeting.started_at or meeting.created_at).isoformat() if (meeting.started_at or meeting.created_at) else None
    platform = None
    if meeting.meeting_url:
        try:
            platform = meeting.meeting_url.split("/")[2]
        except (IndexError, AttributeError):
            pass

    meeting_info = {
        "title": meeting.title or "Untitled Meeting",
        "date": meeting_date,
        "duration_seconds": meeting.duration_seconds,
        "platform": platform,
        "recorded_by": "",
        "participant_count": 0,
        "org_name": "",
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else meeting_date,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
    }
    call_url = f"{settings.FRONTEND_URL}/meetings/{meeting_id}"
    unsubscribe_url = f"{settings.FRONTEND_URL}/settings/notifications"

    if template == "recorder":
        subject, html_body = build_recorder_email(
            meeting=meeting_info, summary_data=summary_data,
            call_url=call_url, edit_url=f"{call_url}/edit",
            transcript_url=f"{call_url}/transcript", unsubscribe_url=unsubscribe_url,
        )
    elif template == "internal":
        subject, html_body = build_internal_email(
            meeting=meeting_info, summary_data=summary_data,
            recipient_email=None, recipient_name="Recipient",
            call_url=call_url, unsubscribe_url=unsubscribe_url,
        )
    else:
        subject, html_body = build_external_email(
            meeting=meeting_info, summary_data=summary_data,
            recipient_email=None, recipient_name="Recipient",
            call_url=call_url, unsubscribe_url=unsubscribe_url,
        )

    return {"subject": subject, "html_body": html_body, "template": template}


@router.post("/{meeting_id}/send-email")
async def send_email_from_meeting(
    meeting_id: uuid.UUID,
    body: dict,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a meeting summary email to a specific recipient."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    to_email = body.get("to_email", "").strip()
    recipient_name = body.get("recipient_name", "").strip() or to_email.split("@")[0]
    template = body.get("template", "external")

    if not to_email or "@" not in to_email:
        raise HTTPException(400, "Valid email address required")

    summary_data = await _load_meeting_summary(db, meeting_id)
    if not summary_data:
        raise HTTPException(404, "No summary available to send")

    from app.clients.notification_client import send_meeting_summary_email
    from app.models.email import EmailLog

    # Map template name to recipient_type
    recipient_type_map = {"recorder": "recorder", "internal": "internal", "external": "external"}
    recipient_type = recipient_type_map.get(template, "external")

    meeting_date = (meeting.started_at or meeting.created_at).isoformat() if (meeting.started_at or meeting.created_at) else None
    platform = None
    if meeting.meeting_url:
        try:
            platform = meeting.meeting_url.split("/")[2]
        except (IndexError, AttributeError):
            pass

    # Processing-time footer: cap at the original auto-email send time so a
    # manual send a week after the meeting still shows the actual pipeline
    # duration (~5 min) instead of "Processed in 10080 minutes". Falls back
    # to (now - started_at) only when no auto-email has fired yet.
    from app.api.meetings_internal import (
        _compute_delivery_minutes,
        _get_type1_started_at,
    )
    started_at = await _get_type1_started_at(db, meeting_id)
    delivery_minutes = _compute_delivery_minutes(
        started_at, end_at=meeting.summary_email_sent_at
    )

    sent_ok = False
    error_message: Optional[str] = None
    try:
        sent_ok = await send_meeting_summary_email(
            to_email=to_email,
            recipient_type=recipient_type,
            recipient_name=recipient_name,
            meeting_id=str(meeting_id),
            meeting_title=meeting.title or "Untitled Call",
            meeting_date=meeting_date,
            duration_seconds=meeting.duration_seconds,
            platform=platform,
            summary_data=summary_data,
            created_by=user.user_id,
            recipient_email=to_email,
            unsubscribe_url=f"{settings.FRONTEND_URL}/settings/notifications",
            delivery_minutes=delivery_minutes,
        )
        if not sent_ok:
            # send_email returns False on 4xx/5xx/network; never raises.
            # Record this as a failure rather than reporting "sent".
            error_message = "Notification service did not accept the email"
    except Exception as e:
        # Defensive — current send path doesn't raise, but a future change
        # might. Treat as a failure and surface the cause.
        error_message = str(e)

    log_entry = EmailLog(
        meeting_id=meeting_id,
        to_email=to_email,
        template_name="meeting_summary_manual",
        trigger_type="user",
        triggered_by=uuid.UUID(user.user_id),
        status="sent" if sent_ok else "failed",
        error_message=error_message,
    )
    db.add(log_entry)
    await db.commit()

    if not sent_ok:
        raise HTTPException(502, f"Failed to send email: {error_message}")

    return {"status": "sent", "to_email": to_email}


@router.get("/{meeting_id}/email-log")
async def get_email_log(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get email send history for a meeting."""
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    from app.models.email import EmailLog

    result = await db.execute(
        select(EmailLog)
        .where(EmailLog.meeting_id == meeting_id)
        .order_by(EmailLog.created_at.desc())
    )
    logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "to_email": log.to_email,
            "status": log.status,
            "trigger_type": log.trigger_type,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
