"""
Zoom webhook receiver — handles recording.completed events from Zoom.
Public endpoint (no JWT) — verified via Zoom's HMAC-SHA256 signature.
"""

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.calendar import CalendarConnection, CalendarEvent
from app.models.meeting import Meeting
from app.models.transcription import Transcription
from app.models.transcription_job import TranscriptionJob
from app.services.zoom_recording import download_with_oauth_fallback
from app.utils.meeting_url import extract_zoom_meeting_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zoom", tags=["zoom"])

ZOOM_MEETING_ID_PATTERN = re.compile(r"zoom\.us/(?:j|wc)/(\d+)")


def _handle_url_validation(payload: dict) -> dict:
    """Respond to Zoom's endpoint.url_validation challenge."""
    plain_token = payload["payload"]["plainToken"]
    encrypted_token = hmac.new(
        settings.ZOOM_WEBHOOK_SECRET_TOKEN.encode(),
        plain_token.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted_token}


def _verify_zoom_signature(
    request_headers, raw_body: bytes, secret: str
) -> None:
    """Verify Zoom webhook signature (HMAC-SHA256)."""
    timestamp = request_headers.get("x-zm-request-timestamp", "")
    signature = request_headers.get("x-zm-signature", "")

    if not timestamp or not signature:
        raise HTTPException(403, "Missing Zoom signature headers")

    # Check timestamp is within 5 minutes
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(403, "Invalid timestamp")

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts) > 300:
        raise HTTPException(403, "Webhook timestamp too old")

    # Compute expected signature
    message = f"v0:{timestamp}:{raw_body.decode()}"
    expected = "v0=" + hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise HTTPException(403, "Invalid webhook signature")


def _select_best_audio_file(recording_files: list[dict]) -> dict | None:
    """Select best audio file from Zoom recording files.

    Priority:
    1. audio_only (M4A) — smallest, fastest to download, sufficient for transcription
    2. Any completed MP4/M4A file — fallback
    """
    audio_only = [
        f
        for f in recording_files
        if f.get("recording_type") == "audio_only"
        and f.get("status") == "completed"
    ]
    if audio_only:
        return audio_only[0]

    video_files = [
        f
        for f in recording_files
        if f.get("file_type") in ("MP4", "M4A")
        and f.get("status") == "completed"
    ]
    return video_files[0] if video_files else None


def _get_file_extension(audio_file: dict) -> str:
    """Determine file extension from the recording file metadata."""
    file_type = audio_file.get("file_type", "M4A").upper()
    return f".{file_type.lower()}"


def _parse_datetime(dt_str: str) -> datetime:
    """Parse Zoom's ISO datetime string to timezone-aware datetime."""
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)


async def _find_zoom_connection_by_email(db: AsyncSession, email: str) -> dict | None:
    """Look up recording_connections row by provider_email for provider='zoom'."""
    result = await db.execute(
        sa_text(
            "SELECT id, user_id, team_id, encrypted_tokens, provider_user_id, provider_email, "
            "auto_import, recording_preference "
            "FROM recording_connections "
            "WHERE provider = 'zoom' AND provider_email = :email LIMIT 1"
        ),
        {"email": email},
    )
    row = result.first()
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "team_id": row[2],
        "encrypted_tokens": row[3],
        "zoom_user_id": row[4],
        "zoom_email": row[5],
        "auto_import": row[6],
        "recording_preference": row[7],
    }


async def _find_meeting_by_zoom_id(
    db: AsyncSession, zoom_meeting_id: str, team_id, start_time: datetime
) -> Meeting | None:
    """Find existing meeting with same Zoom meeting ID within 12-hour time window."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.zoom_meeting_id == zoom_meeting_id,
            Meeting.team_id == team_id,
            Meeting.created_at >= start_time - timedelta(hours=12),
            Meeting.created_at <= start_time + timedelta(hours=12),
        )
    )
    return result.scalar_one_or_none()


async def _create_meeting_from_zoom_recording(
    db: AsyncSession,
    zoom_conn: dict,
    audio_file: dict,
    download_token: str,
    zoom_numeric_id: str,
    zoom_uuid: str,
    topic: str,
    start_time: datetime,
    duration_minutes: int,
) -> None:
    """No existing meeting — create fresh meeting from Zoom cloud recording."""
    meeting = Meeting(
        team_id=zoom_conn["team_id"],
        created_by=zoom_conn["user_id"],
        title=topic,
        meeting_url=f"zoom://cloud/{zoom_uuid}",
        source="zoom_cloud",
        zoom_meeting_id=zoom_numeric_id,
        status="waiting_for_transcription",
        started_at=start_time,
        duration_seconds=duration_minutes * 60 if duration_minutes else None,
    )
    db.add(meeting)
    await db.flush()

    meeting_id_str = str(meeting.id)
    file_ext = _get_file_extension(audio_file)

    # Set paths at creation time
    meeting.bot_job_id = f"zoom-cloud-{meeting_id_str}"
    meeting.original_recording_path = f"zoom_cloud/{meeting_id_str}/original{file_ext}"
    meeting.recording_path = f"zoom_cloud/{meeting_id_str}/original{file_ext}"

    # Download the recording
    local_download_path = await download_with_oauth_fallback(
        download_url=audio_file["download_url"],
        download_token=download_token,
        encrypted_tokens=zoom_conn["encrypted_tokens"],
        meeting_id=meeting_id_str,
        file_extension=file_ext,
    )

    # Upload to R2 immediately after download
    if settings.STORAGE_BACKEND == "s3":
        try:
            from videoclip_common.storage import create_storage
            storage = create_storage()
            await storage.save_file(meeting.recording_path, str(local_download_path))
            logger.info(f"Zoom recording uploaded to R2: {meeting.recording_path}")
        except Exception as e:
            logger.error(f"Failed to upload Zoom recording to R2: {e}")

    # Create transcription job
    params = {
        "input_audio_path": f"zoom_cloud/{meeting_id_str}/original{file_ext}",
        "output_base_path": f"meetings/{meeting_id_str}/v1/",
        "recording_source": "zoom_cloud",
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
    }
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": f"zoom-cloud-{meeting_id_str}",
            "params": json.dumps(params),
        },
    )

    await db.commit()
    logger.info(f"Created meeting {meeting.id} from Zoom cloud recording ({topic})")


async def _add_zoom_version_to_existing_meeting(
    db: AsyncSession,
    meeting: Meeting,
    audio_file: dict,
    download_token: str,
    zoom_conn: dict,
    zoom_uuid: str,
) -> None:
    """Bot already recorded this meeting — add Zoom cloud as next version."""
    meeting_id_str = str(meeting.id)

    # Get next version number
    max_ver_result = await db.execute(
        select(func.max(Transcription.version)).where(
            Transcription.meeting_id == meeting.id
        )
    )
    max_version = max_ver_result.scalar() or 0

    # Also count pending transcription jobs that haven't produced a Transcription yet
    pending_jobs = await db.execute(
        select(func.count())
        .select_from(TranscriptionJob)
        .where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.status.in_(["waiting", "in_progress"]),
        )
    )
    pending_count = pending_jobs.scalar() or 0
    next_version = max_version + pending_count + 1

    file_ext = _get_file_extension(audio_file)

    # Download the recording
    local_download_path = await download_with_oauth_fallback(
        download_url=audio_file["download_url"],
        download_token=download_token,
        encrypted_tokens=zoom_conn["encrypted_tokens"],
        meeting_id=meeting_id_str,
        file_extension=file_ext,
    )

    # Upload to R2 immediately after download
    if settings.STORAGE_BACKEND == "s3":
        try:
            from videoclip_common.storage import create_storage
            storage = create_storage()
            recording_key = f"zoom_cloud/{meeting_id_str}/original{file_ext}"
            await storage.save_file(recording_key, str(local_download_path))
            logger.info(f"Zoom recording (version) uploaded to R2: {recording_key}")
        except Exception as e:
            logger.error(f"Failed to upload Zoom recording to R2: {e}")

    # Create transcription job as next version
    params = {
        "input_audio_path": f"zoom_cloud/{meeting_id_str}/original{file_ext}",
        "output_base_path": f"meetings/{meeting_id_str}/v{next_version}/",
        "recording_source": "zoom_cloud",
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
    }
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": f"zoom-cloud-{meeting_id_str}-v{next_version}",
            "params": json.dumps(params),
        },
    )

    await db.commit()
    logger.info(
        f"Added Zoom cloud recording as v{next_version} to existing meeting {meeting.id}"
    )


async def _handle_meeting_started(db: AsyncSession, payload: dict) -> None:
    """Process a meeting.started event from Zoom — dispatch bot for early join."""
    obj = payload["payload"]["object"]
    zoom_id = str(obj["id"])

    logger.info(f"Zoom meeting.started received for meeting ID {zoom_id}")

    # Find calendar events with a matching Zoom meeting ID in meeting_url.
    # Window: now to now + 2 hours, bot_scheduled=False, auto_record=True.
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)

    result = await db.execute(
        select(CalendarEvent, CalendarConnection)
        .join(CalendarConnection, CalendarEvent.connection_id == CalendarConnection.id)
        .where(
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.bot_scheduled == False,  # noqa: E712
            CalendarEvent.start_time >= now - timedelta(minutes=10),
            CalendarEvent.start_time <= window_end,
            CalendarConnection.auto_record == True,  # noqa: E712
        )
    )
    rows = result.all()

    if not rows:
        logger.debug(f"No matching calendar events for Zoom meeting {zoom_id}")
        return

    from app.services.early_join_dispatcher import dispatch_bot_for_calendar_event

    for cal_event, connection in rows:
        event_zoom_id = extract_zoom_meeting_id(cal_event.meeting_url or "")
        if event_zoom_id == zoom_id:
            logger.info(
                f"Zoom meeting.started matched event {cal_event.id} "
                f"('{cal_event.title}'), dispatching bot"
            )
            await dispatch_bot_for_calendar_event(
                db, cal_event, connection, trigger="zoom_webhook"
            )
            return

    logger.debug(f"No calendar event matched Zoom meeting ID {zoom_id}")


async def _handle_recording_completed(db: AsyncSession, payload: dict) -> None:
    """Process a recording.completed event from Zoom."""
    obj = payload["payload"]["object"]
    host_email = obj["host_email"]
    zoom_numeric_id = str(obj["id"])
    zoom_uuid = obj["uuid"]
    topic = obj.get("topic", "Zoom Meeting")
    start_time = _parse_datetime(obj["start_time"])
    duration_minutes = obj.get("duration", 0)
    download_token = payload.get("download_token", "")

    # 1. Find user by zoom_email
    zoom_conn = await _find_zoom_connection_by_email(db, host_email)
    if not zoom_conn:
        logger.info(f"No Zoom connection for {host_email}, skipping")
        return

    # 2. Check preferences
    if not zoom_conn["auto_import"]:
        logger.info(f"Auto-import disabled for {host_email}, skipping")
        return

    if zoom_conn["recording_preference"] == "bot":
        logger.info(f"User {host_email} prefers bot recording, skipping Zoom cloud import")
        return

    # 3. Select best audio file from recording_files
    recording_files = obj.get("recording_files", [])
    audio_file = _select_best_audio_file(recording_files)
    if not audio_file:
        logger.warning(f"No audio file in recording for meeting {zoom_numeric_id}")
        return

    # 4. Check for existing meeting (dedup)
    existing_meeting = await _find_meeting_by_zoom_id(
        db, zoom_numeric_id, zoom_conn["team_id"], start_time
    )

    if existing_meeting:
        # Bot already recorded this meeting → add as next version
        await _add_zoom_version_to_existing_meeting(
            db, existing_meeting, audio_file, download_token, zoom_conn, zoom_uuid
        )
    else:
        # No bot recording → create new meeting from Zoom cloud
        await _create_meeting_from_zoom_recording(
            db,
            zoom_conn,
            audio_file,
            download_token,
            zoom_numeric_id,
            zoom_uuid,
            topic,
            start_time,
            duration_minutes,
        )


@router.post("/webhooks")
async def zoom_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive webhooks from Zoom.

    Handles:
    - endpoint.url_validation: Zoom's webhook verification challenge
    - recording.completed: Cloud recording is ready for download
    """
    body = await request.body()
    payload = json.loads(body)
    event = payload.get("event")

    # 1. URL Validation Challenge (no signature check needed)
    if event == "endpoint.url_validation":
        return _handle_url_validation(payload)

    # 2. Verify webhook signature
    if not settings.ZOOM_WEBHOOK_SECRET_TOKEN:
        raise HTTPException(503, "Zoom webhook verification not configured")

    _verify_zoom_signature(
        request_headers=request.headers,
        raw_body=body,
        secret=settings.ZOOM_WEBHOOK_SECRET_TOKEN,
    )

    # 3. Route by event type
    if event == "meeting.started":
        try:
            await _handle_meeting_started(db, payload)
        except Exception as e:
            logger.error(f"Failed to process Zoom meeting.started webhook: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
        return {"status": "ok"}

    if event == "recording.completed":
        try:
            await _handle_recording_completed(db, payload)
        except Exception as e:
            logger.error(f"Failed to process Zoom recording webhook: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
        return {"status": "ok"}

    logger.debug(f"Ignoring Zoom event: {event}")
    return {"status": "ignored", "event": event}
