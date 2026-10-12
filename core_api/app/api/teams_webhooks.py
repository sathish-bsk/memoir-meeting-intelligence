"""
Microsoft Teams Cloud Recording webhook receiver.
Receives Graph API change notifications for callRecords subscriptions.
Public endpoint (no JWT) — verified via clientState secret.

Flow:
  Teams meeting ends → Graph creates callRecord → notification sent here →
  fetch callRecord details → find organizer connection → download recording →
  create Meeting + TranscriptionJob → pipeline runs → webhook → done
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text as sa_text

from app.core.config import settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.transcription import Transcription
from app.models.transcription_job import TranscriptionJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams-cloud", tags=["teams-cloud"])

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ─── MS Graph helpers ─────────────────────────────────────────────────────────

async def _get_app_token(tenant_id: str) -> str:
    """Get app-level access token for tenant via client credentials."""
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": settings.OUTLOOK_CLIENT_ID,
                "client_secret": settings.OUTLOOK_CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _fetch_call_record(app_token: str, call_id: str) -> dict:
    """Fetch full callRecord from MS Graph with sessions expanded."""
    url = f"{GRAPH_BASE}/communications/callRecords/{call_id}"
    params = {"$expand": "sessions($expand=segments)"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {app_token}"}
        )
        resp.raise_for_status()
        return resp.json()


async def _find_online_meeting(
    app_token: str, organizer_id: str, join_web_url: str
) -> Optional[str]:
    """Find online meeting ID for a given joinWebUrl.

    Returns online meeting ID or None.
    """
    try:
        import urllib.parse
        filter_expr = f"joinWebUrl eq '{join_web_url}'"
        url = f"{GRAPH_BASE}/users/{organizer_id}/onlineMeetings"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                params={"$filter": filter_expr, "$select": "id"},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            meetings = data.get("value", [])
            return meetings[0]["id"] if meetings else None
    except Exception as e:
        logger.warning(f"Could not find online meeting for {organizer_id}: {e}")
        return None


async def _get_recordings(
    app_token: str, organizer_id: str, online_meeting_id: str
) -> list[dict]:
    """List recordings for a Teams online meeting."""
    url = f"{GRAPH_BASE}/users/{organizer_id}/onlineMeetings/{online_meeting_id}/recordings"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {app_token}"})
        resp.raise_for_status()
        return resp.json().get("value", [])


async def _download_teams_recording(
    app_token: str,
    organizer_id: str,
    online_meeting_id: str,
    recording_id: str,
    dest_path: str,
) -> None:
    """Download Teams recording content to dest_path."""
    import os
    from pathlib import Path

    url = (
        f"{GRAPH_BASE}/users/{organizer_id}/onlineMeetings/"
        f"{online_meeting_id}/recordings/{recording_id}/content"
    )
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
        async with client.stream(
            "GET", url, headers={"Authorization": f"Bearer {app_token}"}
        ) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def _find_teams_connection_by_ms_user_id(
    db: AsyncSession, ms_user_id: str, tenant_id: str
) -> Optional[dict]:
    """Look up recording_connections for a Teams user by MS user ID and tenant."""
    result = await db.execute(
        sa_text(
            "SELECT id, user_id, team_id, tenant_id, encrypted_tokens, "
            "provider_user_id, provider_email, auto_import, recording_preference "
            "FROM recording_connections "
            "WHERE provider = 'teams' "
            "  AND provider_user_id = :ms_user_id "
            "  AND tenant_id = :tenant_id "
            "LIMIT 1"
        ),
        {"ms_user_id": ms_user_id, "tenant_id": tenant_id},
    )
    row = result.first()
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "team_id": row[2],
        "tenant_id": row[3],
        "encrypted_tokens": row[4],
        "provider_user_id": row[5],
        "provider_email": row[6],
        "auto_import": row[7],
        "recording_preference": row[8],
    }


async def _find_meeting_by_teams_id(
    db: AsyncSession, teams_meeting_id: str, team_id, start_time: datetime
) -> Optional[Meeting]:
    """Find existing meeting with same Teams meeting ID within ±12-hour window."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.teams_meeting_id == teams_meeting_id,
            Meeting.team_id == team_id,
            Meeting.created_at >= start_time - timedelta(hours=12),
            Meeting.created_at <= start_time + timedelta(hours=12),
        )
    )
    return result.scalar_one_or_none()


# ─── Recording processing ─────────────────────────────────────────────────────

async def _create_meeting_from_teams_recording(
    db: AsyncSession,
    conn: dict,
    call_record: dict,
    recording_path: str,
    teams_meeting_id: str,
) -> None:
    """Create a new meeting from a Teams cloud recording."""
    title = call_record.get("subject") or call_record.get("joinWebUrl", "Teams Meeting")
    start_dt_str = call_record.get("startDateTime", "")
    end_dt_str = call_record.get("endDateTime", "")

    try:
        start_time = datetime.fromisoformat(start_dt_str.replace("Z", "+00:00"))
    except Exception:
        start_time = datetime.now(timezone.utc)

    duration_seconds = None
    try:
        end_time = datetime.fromisoformat(end_dt_str.replace("Z", "+00:00"))
        duration_seconds = int((end_time - start_time).total_seconds())
    except Exception:
        pass

    meeting = Meeting(
        team_id=conn["team_id"],
        created_by=conn["user_id"],
        title=title,
        meeting_url=call_record.get("joinWebUrl", ""),
        source="teams_cloud",
        teams_meeting_id=teams_meeting_id,
        status="waiting_for_transcription",
        started_at=start_time,
        duration_seconds=duration_seconds,
    )
    db.add(meeting)
    await db.flush()

    meeting_id_str = str(meeting.id)
    meeting.bot_job_id = f"teams-cloud-{meeting_id_str}"
    meeting.recording_path = recording_path

    params = {
        "input_audio_path": recording_path,
        "output_base_path": f"meetings/{meeting_id_str}/v1/",
        "recording_source": "teams_cloud",
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
            "bjid": f"teams-cloud-{meeting_id_str}",
            "params": json.dumps(params),
        },
    )

    await db.commit()
    logger.info(f"Created meeting {meeting.id} from Teams cloud recording ({title})")


async def _add_teams_version_to_existing_meeting(
    db: AsyncSession,
    meeting: Meeting,
    recording_path: str,
) -> None:
    """Bot already recorded this meeting — add Teams cloud as next version."""
    meeting_id_str = str(meeting.id)

    max_ver = await db.execute(
        select(func.max(Transcription.version)).where(
            Transcription.meeting_id == meeting.id
        )
    )
    max_version = max_ver.scalar() or 0

    pending_jobs = await db.execute(
        select(func.count())
        .select_from(TranscriptionJob)
        .where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.status.in_(["waiting", "in_progress"]),
        )
    )
    next_version = max_version + (pending_jobs.scalar() or 0) + 1

    params = {
        "input_audio_path": recording_path,
        "output_base_path": f"meetings/{meeting_id_str}/v{next_version}/",
        "recording_source": "teams_cloud",
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
            "bjid": f"teams-cloud-{meeting_id_str}-v{next_version}",
            "params": json.dumps(params),
        },
    )
    await db.commit()
    logger.info(
        f"Added Teams cloud recording as v{next_version} to existing meeting {meeting.id}"
    )


async def _process_call_record_notification(
    call_id: str, tenant_id: str
) -> None:
    """Background task: fetch callRecord, download recording, create meeting."""
    from app.core.database import async_session

    logger.info(f"Processing Teams callRecord notification: callId={call_id} tenant={tenant_id}")

    try:
        app_token = await _get_app_token(tenant_id)
    except Exception as e:
        logger.error(f"Failed to get app token for tenant {tenant_id}: {e}")
        return

    # Fetch the callRecord
    try:
        call_record = await _fetch_call_record(app_token, call_id)
    except Exception as e:
        logger.error(f"Failed to fetch callRecord {call_id}: {e}")
        return

    # Extract organizer info
    organizer = (call_record.get("organizer_v2") or {}).get("user", {})
    ms_organizer_id = organizer.get("id", "")
    join_web_url = call_record.get("joinWebUrl", "")

    if not ms_organizer_id:
        logger.warning(f"No organizer found in callRecord {call_id}, skipping")
        return

    # Find connection for this organizer
    async with async_session() as db:
        conn = await _find_teams_connection_by_ms_user_id(db, ms_organizer_id, tenant_id)
        if not conn:
            logger.info(
                f"No Teams connection for MS user {ms_organizer_id} in tenant {tenant_id}, skipping"
            )
            return

        if not conn["auto_import"]:
            logger.info(f"Auto-import disabled for user {conn['provider_email']}, skipping")
            return

        if conn["recording_preference"] == "bot":
            logger.info(
                f"User {conn['provider_email']} prefers bot recording, skipping Teams cloud import"
            )
            return

        # Find the online meeting to get recording URL
        if not join_web_url:
            logger.warning(f"No joinWebUrl in callRecord {call_id}, cannot find recording")
            return

        online_meeting_id = await _find_online_meeting(app_token, ms_organizer_id, join_web_url)
        if not online_meeting_id:
            logger.warning(f"Could not find online meeting for callRecord {call_id}")
            return

        # List recordings
        try:
            recordings = await _get_recordings(app_token, ms_organizer_id, online_meeting_id)
        except Exception as e:
            logger.warning(f"Could not list recordings for meeting {online_meeting_id}: {e}")
            return

        if not recordings:
            logger.info(f"No recordings found for callRecord {call_id} yet")
            return

        recording = recordings[0]
        recording_id = recording.get("id", "")
        if not recording_id:
            logger.warning(f"Recording has no ID for callRecord {call_id}")
            return

        # Build local path for the downloaded recording
        safe_meeting_id = str(uuid.uuid4())
        recording_path = f"teams_cloud/{safe_meeting_id}/original.mp4"
        local_path = f"/tmp/memoir/recordings/{recording_path}"

        # Download the recording
        try:
            await _download_teams_recording(
                app_token, ms_organizer_id, online_meeting_id, recording_id, local_path
            )
        except Exception as e:
            logger.error(f"Failed to download Teams recording for callRecord {call_id}: {e}")
            return

        # Upload to R2 immediately after download
        if settings.STORAGE_BACKEND == "s3":
            try:
                from videoclip_common.storage import create_storage
                storage = create_storage()
                await storage.save_file(recording_path, local_path)
                logger.info(f"Teams recording uploaded to R2: {recording_path}")
            except Exception as e:
                logger.error(f"Failed to upload Teams recording to R2: {e}")

        # Dedup check
        start_dt_str = call_record.get("startDateTime", "")
        try:
            start_time = datetime.fromisoformat(start_dt_str.replace("Z", "+00:00"))
        except Exception:
            start_time = datetime.now(timezone.utc)

        existing = await _find_meeting_by_teams_id(
            db, call_id, conn["team_id"], start_time
        )

        if existing:
            await _add_teams_version_to_existing_meeting(db, existing, recording_path)
        else:
            await _create_meeting_from_teams_recording(
                db, conn, call_record, recording_path, call_id
            )


# ─── Webhook endpoint ─────────────────────────────────────────────────────────

@router.get("/webhooks")
async def teams_webhook_validation(
    validationToken: Optional[str] = Query(None),
):
    """Handle Microsoft Graph subscription validation challenge.

    Graph sends a GET with ?validationToken=... — return it as plain text.
    """
    if validationToken:
        return Response(content=validationToken, media_type="text/plain")
    return Response(status_code=200)


@router.post("/webhooks")
async def teams_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Receive Graph API change notification for callRecords.

    Immediately acknowledges with 202, processes in background.
    """
    try:
        body = await request.body()
        payload = json.loads(body)
    except Exception as e:
        logger.error(f"Failed to parse Teams webhook body: {e}")
        raise HTTPException(400, "Invalid request body")

    notifications = payload.get("value", [])
    if not notifications:
        return Response(status_code=202)

    # Validate clientState on first notification
    expected_client_state = settings.TEAMS_CLOUD_CLIENT_STATE_SECRET
    if expected_client_state:
        client_state = notifications[0].get("clientState", "")
        if client_state != expected_client_state:
            logger.warning(f"Teams webhook clientState mismatch: {client_state!r}")
            raise HTTPException(403, "Invalid clientState")

    # Queue background processing for each callRecord notification
    for notification in notifications:
        resource = notification.get("resource", "")
        # resource format: "communications/callRecords/{callId}"
        if "/callRecords/" not in resource:
            continue

        call_id = resource.split("/callRecords/")[-1]
        tenant_id = notification.get("tenantId", "")

        if not call_id or not tenant_id:
            logger.warning(f"Teams notification missing callId or tenantId: {notification}")
            continue

        background_tasks.add_task(
            _process_call_record_notification, call_id, tenant_id
        )
        logger.info(f"Queued Teams callRecord processing: {call_id} tenant={tenant_id}")

    # Return 202 Accepted to acknowledge receipt
    return Response(status_code=202)
