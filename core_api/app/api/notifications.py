"""
Notifications API — preferences, digest schedule, activity log, slack config.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.call_intelligence import ScheduledJob
from app.models.notification import NotificationLog, NotificationPreference
from app.models.team import TeamMember
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Known event types and their defaults
EVENT_TYPES = ["transcription_complete", "participant_summary", "intelligence_digest", "faq_digest"]
CHANNELS = ["email", "slack"]
DEFAULTS = {
    "transcription_complete": {"email": True, "slack": False},
    "participant_summary": {"email": True, "slack": False},
    "intelligence_digest": {"email": True, "slack": False},
    "faq_digest": {"email": True, "slack": False},
}


# ---------------------------------------------------------------------------
# Auth helper (reused from intelligence.py pattern)
# ---------------------------------------------------------------------------

async def _verify_team_member(db: AsyncSession, team_id: uuid.UUID, user_id: str) -> TeamMember:
    from sqlalchemy import or_
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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PreferenceUpdate(BaseModel):
    team_id: uuid.UUID
    event_type: str
    channel: str
    enabled: bool


class DigestSettingsUpdateBody(BaseModel):
    team_id: uuid.UUID
    job_type: str = "intelligence_digest"
    frequency: str | None = None
    preferred_time: str | None = None  # "HH:MM"
    preferred_day: int | None = None
    enabled: bool | None = None


class DigestGenerateBody(BaseModel):
    team_id: uuid.UUID
    job_type: str = "intelligence_digest"


class MarkReadBody(BaseModel):
    ids: list[uuid.UUID] | None = None
    all: bool = False


class SlackConfigUpdate(BaseModel):
    team_id: uuid.UUID
    channel_id: str
    channel_name: str


# ---------------------------------------------------------------------------
# Unsubscribe (no auth required)
# ---------------------------------------------------------------------------

@router.get("/unsubscribe")
async def unsubscribe_via_token(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """One-click unsubscribe for external participants. No login required."""
    from app.models.notification import EmailUnsubscribeToken, EmailSuppression

    result = await db.execute(
        select(EmailUnsubscribeToken).where(EmailUnsubscribeToken.token == token)
    )
    unsub = result.scalar_one_or_none()
    if not unsub:
        return HTMLResponse("<html><body><h2>Invalid or expired unsubscribe link.</h2></body></html>", status_code=404)

    # Add to suppression list
    existing = await db.execute(
        select(EmailSuppression).where(
            EmailSuppression.email == unsub.email,
            EmailSuppression.team_id == unsub.team_id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(EmailSuppression(email=unsub.email, team_id=unsub.team_id, reason="unsubscribed"))
        await db.commit()

    return HTMLResponse(
        "<html><body style='font-family: sans-serif; text-align: center; padding: 60px 20px;'>"
        "<h2>You've been unsubscribed</h2>"
        "<p>You will no longer receive meeting summary emails from this team.</p>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Preferences endpoints
# ---------------------------------------------------------------------------

@router.get("/preferences")
async def get_preferences(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get notification preferences for current user + team, grouped by event_type."""
    await _verify_team_member(db, team_id, user.user_id)
    uid = uuid.UUID(user.user_id)

    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == uid,
            NotificationPreference.team_id == team_id,
        )
    )
    rows = result.scalars().all()

    # If no rows exist, auto-seed defaults
    if not rows:
        for event_type, channel_defaults in DEFAULTS.items():
            for channel, enabled in channel_defaults.items():
                pref = NotificationPreference(
                    user_id=uid,
                    team_id=team_id,
                    event_type=event_type,
                    channel=channel,
                    enabled=enabled,
                )
                db.add(pref)
        await db.commit()

        # Re-fetch
        result = await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == uid,
                NotificationPreference.team_id == team_id,
            )
        )
        rows = result.scalars().all()

    # Group by event_type
    prefs: dict = {}
    for row in rows:
        if row.event_type not in prefs:
            prefs[row.event_type] = {}
        prefs[row.event_type][row.channel] = row.enabled

    return prefs


@router.patch("/preferences")
async def update_preference(
    body: PreferenceUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upsert a single preference toggle."""
    await _verify_team_member(db, body.team_id, user.user_id)
    uid = uuid.UUID(user.user_id)

    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, f"Unknown event_type: {body.event_type}")
    if body.channel not in CHANNELS:
        raise HTTPException(400, f"Unknown channel: {body.channel}")

    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == uid,
            NotificationPreference.team_id == body.team_id,
            NotificationPreference.event_type == body.event_type,
            NotificationPreference.channel == body.channel,
        )
    )
    pref = result.scalar_one_or_none()

    if pref:
        pref.enabled = body.enabled
        pref.updated_at = datetime.now(timezone.utc)
    else:
        pref = NotificationPreference(
            user_id=uid,
            team_id=body.team_id,
            event_type=body.event_type,
            channel=body.channel,
            enabled=body.enabled,
        )
        db.add(pref)

    await db.commit()
    return {"status": "ok", "event_type": body.event_type, "channel": body.channel, "enabled": body.enabled}


# ---------------------------------------------------------------------------
# Digest settings (replaces intelligence.py endpoints)
# ---------------------------------------------------------------------------

@router.get("/digest-settings")
async def get_digest_settings(
    team_id: uuid.UUID = Query(...),
    job_type: str = Query("intelligence_digest"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get digest schedule settings for a specific job type."""
    await _verify_team_member(db, team_id, user.user_id)

    result = await db.execute(
        select(ScheduledJob).where(
            ScheduledJob.team_id == team_id,
            ScheduledJob.job_type == job_type,
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        from app.services.job_scheduler import compute_next_run
        from datetime import time

        job = ScheduledJob(
            team_id=team_id,
            job_type=job_type,
            frequency="weekly",
            preferred_day=0,
            enabled=True,
        )
        job.next_run_at = compute_next_run(job.frequency, job.preferred_day, job.preferred_time)
        db.add(job)
        await db.commit()
        await db.refresh(job)

    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "frequency": job.frequency,
        "preferred_time": job.preferred_time.isoformat() if job.preferred_time else None,
        "preferred_day": job.preferred_day,
        "enabled": job.enabled,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status,
    }


@router.put("/digest-settings")
async def update_digest_settings(
    body: DigestSettingsUpdateBody,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update digest schedule settings. Recomputes next_run_at."""
    await _verify_team_member(db, body.team_id, user.user_id)

    result = await db.execute(
        select(ScheduledJob).where(
            ScheduledJob.team_id == body.team_id,
            ScheduledJob.job_type == body.job_type,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Digest job not found — GET digest-settings first to auto-create")

    if body.frequency is not None:
        job.frequency = body.frequency
    if body.preferred_time is not None:
        from datetime import time as dt_time
        parts = body.preferred_time.split(":")
        job.preferred_time = dt_time(int(parts[0]), int(parts[1]))
    if body.preferred_day is not None:
        job.preferred_day = body.preferred_day
    if body.enabled is not None:
        job.enabled = body.enabled

    from app.services.job_scheduler import compute_next_run
    job.next_run_at = compute_next_run(job.frequency, job.preferred_day, job.preferred_time)
    job.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(job)

    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "frequency": job.frequency,
        "preferred_time": job.preferred_time.isoformat() if job.preferred_time else None,
        "preferred_day": job.preferred_day,
        "enabled": job.enabled,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
        "last_status": job.last_status,
    }


@router.post("/digest-settings/generate")
async def generate_digest_now(
    team_id: uuid.UUID = Query(...),
    job_type: str = Query("intelligence_digest"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger digest generation."""
    await _verify_team_member(db, team_id, user.user_id)

    if job_type == "intelligence_digest":
        from app.services.digest_generator import generate_digest
        digest = await generate_digest(db, team_id)
        if not digest:
            raise HTTPException(404, "No external calls found to generate digest from")
        await db.commit()
        return {"status": "generated", "digest_id": str(digest.id), "total_calls": digest.total_calls}

    elif job_type == "faq_digest":
        from app.services.digest_generator import generate_digest
        digest = await generate_digest(db, team_id)
        if not digest:
            raise HTTPException(404, "No external calls found to generate FAQ digest from")
        await db.commit()
        faq_groups = (digest.metrics or {}).get("faq_groups", [])
        return {"status": "generated", "digest_id": str(digest.id), "faq_group_count": len(faq_groups)}

    else:
        raise HTTPException(400, f"Unknown job_type: {job_type}")


# ---------------------------------------------------------------------------
# Activity log endpoints
# ---------------------------------------------------------------------------

@router.get("/log")
async def get_notification_log(
    team_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=0, le=100),
    offset: int = Query(0, ge=0),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated activity log for current user."""
    await _verify_team_member(db, team_id, user.user_id)
    uid = uuid.UUID(user.user_id)

    # Unread count
    unread_result = await db.execute(
        select(func.count(NotificationLog.id)).where(
            NotificationLog.user_id == uid,
            NotificationLog.team_id == team_id,
            NotificationLog.read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar() or 0

    # Total
    total_result = await db.execute(
        select(func.count(NotificationLog.id)).where(
            NotificationLog.user_id == uid,
            NotificationLog.team_id == team_id,
        )
    )
    total = total_result.scalar() or 0

    # Items (if limit > 0)
    items = []
    if limit > 0:
        rows_result = await db.execute(
            select(NotificationLog)
            .where(
                NotificationLog.user_id == uid,
                NotificationLog.team_id == team_id,
            )
            .order_by(desc(NotificationLog.created_at))
            .offset(offset)
            .limit(limit)
        )
        items = [
            {
                "id": str(row.id),
                "event_type": row.event_type,
                "title": row.title,
                "body": row.body,
                "link": row.link,
                "channel": row.channel,
                "status": row.status,
                "read": row.read,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows_result.scalars().all()
        ]

    return {"items": items, "unread_count": unread_count, "total": total}


@router.post("/log/mark-read")
async def mark_notifications_read(
    body: MarkReadBody,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark specific notifications or all as read."""
    uid = uuid.UUID(user.user_id)

    if body.all:
        from sqlalchemy import update
        await db.execute(
            update(NotificationLog)
            .where(
                NotificationLog.user_id == uid,
                NotificationLog.read == False,  # noqa: E712
            )
            .values(read=True)
        )
    elif body.ids:
        from sqlalchemy import update
        await db.execute(
            update(NotificationLog)
            .where(
                NotificationLog.user_id == uid,
                NotificationLog.id.in_(body.ids),
            )
            .values(read=True)
        )
    else:
        raise HTTPException(400, "Provide ids or set all=true")

    await db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Slack config (proxied from integration_service data)
# ---------------------------------------------------------------------------

@router.get("/slack-config")
async def get_slack_config(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's Slack notification config (channel, connection status)."""
    await _verify_team_member(db, team_id, user.user_id)

    from app.models.slack import SlackConnection, SlackNotificationSettings

    # Query slack connection + settings
    conn_result = await db.execute(
        select(SlackConnection).where(
            SlackConnection.user_id == uuid.UUID(user.user_id)
        )
    )
    connection = conn_result.scalar_one_or_none()

    if not connection:
        return {"connected": False, "workspace_name": None, "channel_id": None, "channel_name": None}

    settings_result = await db.execute(
        select(SlackNotificationSettings).where(
            SlackNotificationSettings.user_id == uuid.UUID(user.user_id)
        )
    )
    notif_settings = settings_result.scalar_one_or_none()

    return {
        "connected": True,
        "workspace_name": connection.workspace_name,
        "channel_id": notif_settings.channel_id if notif_settings else None,
        "channel_name": notif_settings.channel_name if notif_settings else None,
    }


@router.put("/slack-config")
async def update_slack_config(
    body: SlackConfigUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Slack notification channel for current user."""
    await _verify_team_member(db, body.team_id, user.user_id)

    from app.models.slack import SlackNotificationSettings

    result = await db.execute(
        select(SlackNotificationSettings).where(
            SlackNotificationSettings.user_id == uuid.UUID(user.user_id)
        )
    )
    notif_settings = result.scalar_one_or_none()
    if not notif_settings:
        raise HTTPException(404, "No Slack notification settings found — connect Slack first")

    notif_settings.channel_id = body.channel_id
    notif_settings.channel_name = body.channel_name
    await db.commit()

    return {"status": "ok", "channel_id": body.channel_id, "channel_name": body.channel_name}


@router.post("/slack-config/test")
async def send_slack_test_message(
    team_id: uuid.UUID = Query(...),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a test message to the user's configured Slack channel."""
    await _verify_team_member(db, team_id, user.user_id)

    from app.clients.slack_client import send_slack_message

    success = await send_slack_message(
        user_id=uuid.UUID(user.user_id),
        text="This is a test notification from Memoir. If you see this, Slack notifications are working!",
    )

    if not success:
        raise HTTPException(500, "Failed to send test message — check Slack connection")

    return {"status": "ok"}
