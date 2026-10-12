"""
Internal (service-to-service) endpoints for meetings.

Mounted under /api/internal/meetings so requests route through Traefik's
chain-webhooks (no ForwardAuth) and reach us with the X-Service-Key header
intact, which verify_internal_call validates against INTERNAL_SERVICE_KEY.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.transcription_job import TranscriptionJob
from videoclip_common.service_auth import verify_internal_call
from videoclip_common.storage import create_storage

logger = logging.getLogger(__name__)


internal_router = APIRouter(
    prefix="/api/internal/meetings",
    tags=["meetings-internal"],
    dependencies=[Depends(verify_internal_call)],
)


# ── Helpers (shared with webhooks.py) ─────────────────────────────────────────


async def _get_type1_started_at(
    db: AsyncSession, meeting_id: uuid.UUID
) -> Optional[datetime]:
    """Return the started_at of the LATEST Type 1 (preprocess) job for a meeting.

    Uses DESC ordering so retranscribed meetings report the current run's
    processing time, not the original pipeline run from days/weeks earlier.
    Falls back to the row's created_at when started_at is NULL. Returns None
    when no Type 1 row exists yet (shouldn't happen by the time we email).
    """
    row = await db.execute(
        sa_text(
            "SELECT started_at, created_at FROM transcription_jobs "
            "WHERE meeting_id = :mid AND operation_type = 1 "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"mid": str(meeting_id)},
    )
    record = row.mappings().first()
    if not record:
        return None
    return record.get("started_at") or record.get("created_at")


def _compute_delivery_minutes(
    started_at: Optional[datetime],
    end_at: Optional[datetime] = None,
) -> Optional[int]:
    """Return wall-clock minutes between started_at and end_at (≥ 1), or None.

    end_at defaults to now (UTC). Manual-send paths pass
    meeting.summary_email_sent_at as end_at so the footer shows the original
    processing duration instead of "minutes since pipeline started" — which
    grows unbounded for sends made days after the meeting.
    """
    if started_at is None:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if end_at is None:
        end_at = datetime.now(timezone.utc)
    elif end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    delta = end_at - started_at
    return max(1, int(delta.total_seconds() / 60))


async def _claim_summary_email_gate(
    db: AsyncSession, meeting_id: uuid.UUID
) -> bool:
    """Atomically claim the auto-summary email send for a meeting.

    Returns True if this caller got the claim and should proceed with sending.
    Returns False if another request already claimed it (skip — duplicate).

    Implementation: a single conditional UPDATE that only matches when the gate
    is NULL. PostgreSQL's RETURNING + the WHERE filter make the check-and-set
    atomic at the row level, eliminating the read-then-write race that two
    concurrent fast/polished email triggers would otherwise hit.

    On send failure, the caller MUST invoke _release_summary_email_gate so a
    later retry path can claim the gate fresh (matches FR-6 contract: recorder
    send failure → polished path retries email).
    """
    result = await db.execute(
        sa_text(
            "UPDATE meetings SET summary_email_sent_at = NOW() "
            "WHERE id = :mid AND summary_email_sent_at IS NULL "
            "RETURNING id"
        ),
        {"mid": str(meeting_id)},
    )
    claimed = result.scalar_one_or_none() is not None
    await db.commit()
    return claimed


async def _release_summary_email_gate(
    db: AsyncSession, meeting_id: uuid.UUID
) -> None:
    """Reset the gate to NULL so a later send attempt can re-claim it.

    Called after a failed recorder send. Matches today's FR-6 behavior: the
    polished path retries the email at end-of-pipeline when the fast path
    couldn't deliver.
    """
    await db.execute(
        sa_text(
            "UPDATE meetings SET summary_email_sent_at = NULL WHERE id = :mid"
        ),
        {"mid": str(meeting_id)},
    )
    await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────


@internal_router.post("/{meeting_id}/email-fast-summary")
async def email_fast_summary(
    meeting_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Send the fast-track summary email after Type 7 (fast P5) completes.

    Idempotency: gated by meetings.summary_email_sent_at. If set, returns
    `already_sent` without re-sending. The gate is set ONLY after the
    recorder's email returns True from the notification service — if the
    recorder send fails, the gate stays NULL so the polished path retries
    the email at end-of-pipeline (FR-6).

    Internal-team fan-out is best-effort; per-recipient failures are logged
    but do NOT trigger a polished-path retry (matches today's behavior).
    """
    from app.api.webhooks import _send_participant_emails
    from app.clients.auth_client import batch_get_users
    from app.clients.notification_client import send_meeting_summary_email

    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(404, "meeting not found")

    # Load fast P5 summary from R2 — verify there's something to send before
    # we burn the idempotency claim. Two concurrent triggers must both be
    # able to bail out if the file doesn't exist yet.
    storage = create_storage()
    fast_summary_key = f"meetings/{meeting_id}/v1/summary_fast.json"
    if not await storage.exists(fast_summary_key):
        logger.info(f"Meeting {meeting_id}: summary_fast.json not present yet")
        return {"status": "not_ready"}

    try:
        raw_summary = await storage.read_json(fast_summary_key)
    except Exception as e:
        logger.error(f"Meeting {meeting_id}: failed to read summary_fast.json: {e}")
        return {"status": "read_failed"}

    summary_data = (
        raw_summary.get("summary", {}) if isinstance(raw_summary, dict) else {}
    )

    # Defensive: short-transcript producer-side guard already prevents writing
    # summary_fast.json when run_summary_only returns None, so this branch is
    # secondary (matches webhooks.py:866 behavior).
    if not summary_data:
        logger.info(
            f"Meeting {meeting_id}: skipping fast email — empty summary "
            "(transcript too short)"
        )
        return {"status": "no_summary"}

    # Compute processing time from the latest Type 1 job's started_at
    started_at = await _get_type1_started_at(db, meeting_id)
    delivery_minutes = _compute_delivery_minutes(started_at)

    # Resolve recorder before claiming the gate — if there's no recorder
    # email we're not going to send anything, and we want the polished path
    # (which has its own resolution) to retry rather than be blocked by our
    # claim. So we only claim once we know we'll attempt a send.
    created_by_str = str(meeting.created_by)
    users = await batch_get_users([created_by_str])
    owner = users.get(created_by_str)
    if not owner or not owner.get("email"):
        logger.warning(
            f"Meeting {meeting_id}: recorder has no email; skipping fast send "
            "(polished path will retry)"
        )
        return {"status": "no_recorder_email"}

    # ── Atomically claim the gate (race-safe) ────────────────────────────
    claimed = await _claim_summary_email_gate(db, meeting_id)
    if not claimed:
        logger.info(
            f"Meeting {meeting_id}: gate already claimed by another path "
            "(duplicate fast trigger or polished retry); skipping fast email"
        )
        return {"status": "already_sent"}

    meeting_id_str = str(meeting_id)
    platform = None
    if meeting.meeting_url:
        try:
            platform = meeting.meeting_url.split("/")[2]
        except (IndexError, AttributeError):
            pass

    # Pull recording_source from any existing job row (best-effort)
    recording_source = "bot"
    job_row = await db.execute(
        sa_text(
            "SELECT params FROM transcription_jobs "
            "WHERE meeting_id = :mid ORDER BY created_at ASC LIMIT 1"
        ),
        {"mid": meeting_id_str},
    )
    row = job_row.mappings().first()
    if row and row.get("params"):
        params = row["params"]
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        if isinstance(params, dict):
            recording_source = params.get("recording_source", "bot")

    # ── Send recorder email — release gate on failure so retries can run ──
    recorder_sent_ok = False
    try:
        recorder_sent_ok = await send_meeting_summary_email(
            to_email=owner["email"],
            recipient_type="recorder",
            recipient_name=owner.get("name") or "there",
            meeting_id=meeting_id_str,
            meeting_title=meeting.title or "Untitled Call",
            meeting_date=(meeting.started_at or meeting.created_at).isoformat()
            if (meeting.started_at or meeting.created_at)
            else None,
            duration_seconds=meeting.duration_seconds,
            platform=platform,
            summary_data=summary_data,
            created_by=created_by_str,
            owner_name=owner.get("name"),
            recording_source=recording_source,
            meeting_ended_at=meeting.ended_at.isoformat()
            if meeting.ended_at
            else None,
            meeting_created_at=meeting.created_at.isoformat()
            if meeting.created_at
            else None,
            unsubscribe_url=f"{settings.FRONTEND_URL}/settings/notifications",
            delivery_minutes=delivery_minutes,
        )
    finally:
        if not recorder_sent_ok:
            # Reset the gate so the polished path can retry per FR-6.
            await _release_summary_email_gate(db, meeting_id)
            logger.error(
                f"Meeting {meeting_id}: recorder fast email failed; gate "
                "released, polished path will retry"
            )

    if not recorder_sent_ok:
        return {"status": "recorder_failed"}

    logger.info(
        f"Meeting {meeting_id}: fast summary email sent to recorder "
        f"({delivery_minutes}m); gate held"
    )

    # Internal team fan-out (best-effort). External participants are skipped
    # naturally because MemoirSummary doesn't exist yet at fast-email time.
    try:
        await _send_participant_emails(
            db,
            meeting,
            summary_data=summary_data,
            memoir_summary=None,
            settings_obj=settings,
            delivery_minutes=delivery_minutes,
        )
    except Exception as e:
        logger.error(
            f"Meeting {meeting_id}: internal participant fan-out failed "
            f"(non-fatal): {e}"
        )

    return {"status": "sent", "delivery_minutes": delivery_minutes}
