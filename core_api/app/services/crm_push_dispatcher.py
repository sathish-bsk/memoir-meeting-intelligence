"""
CRM push dispatcher — checks connected CRMs and dispatches push for each.
Called from webhooks.py after intelligence data is stored.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.crm_client import push_to_crm, get_crm_connections
from app.core.config import settings
from app.models.crm_push_audit import CrmPushAudit
from app.models.crm_push_result import CrmPushResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [60, 300, 900]  # 1min, 5min, 15min


async def dispatch_crm_auto_push(db: AsyncSession, meeting, intelligence_data: dict) -> None:
    """Check connected CRMs for this team and dispatch push to each.

    Args:
        db: Database session
        meeting: Meeting ORM object (must have .id, .team_id, .title, .duration_seconds, .created_by)
        intelligence_data: Full intelligence data dict with crm_extraction
    """
    team_id = str(meeting.team_id)
    meeting_id = str(meeting.id)

    # Get connected CRMs that have auto_push_enabled
    connections = await get_crm_connections(team_id)
    active_connections = [c for c in connections if c.get("is_active") and c.get("auto_push_enabled")]

    if not active_connections:
        logger.debug(f"Meeting {meeting_id}: no active CRM connections for auto-push")
        return

    # Extract CRM data from intelligence pipeline
    crm_extraction = intelligence_data.get("crm", intelligence_data.get("crm_extraction", {}))
    if not crm_extraction:
        logger.debug(f"Meeting {meeting_id}: no CRM extraction data for push")
        return

    # Build call metadata
    call_metadata = {
        "title": meeting.title or "Untitled Call",
        "duration": meeting.duration_seconds or 0,
        "url": f"{settings.FRONTEND_URL}/meetings/{meeting_id}",
        "timestamp": meeting.created_at.isoformat() if meeting.created_at else datetime.now(timezone.utc).isoformat(),
    }

    # Dispatch push for each connected CRM
    for conn in active_connections:
        provider = conn["provider"]
        try:
            await _push_single_provider(db, meeting_id, team_id, provider, crm_extraction, call_metadata)
        except Exception as e:
            logger.error(f"CRM auto-push dispatch failed for {provider} on meeting {meeting_id}: {e}")


async def _push_single_provider(
    db: AsyncSession,
    meeting_id: str,
    team_id: str,
    provider: str,
    crm_extraction: dict,
    call_metadata: dict,
    pushed_by: str | None = None,
    edited_data: dict | None = None,
    push_type: str = "auto",
) -> CrmPushResult:
    """Push to a single CRM provider and update the push result record."""
    import uuid as uuid_mod

    meeting_uuid = uuid_mod.UUID(meeting_id)
    team_uuid = uuid_mod.UUID(team_id)

    # Find or create push result row
    result = await db.execute(
        select(CrmPushResult).where(
            CrmPushResult.meeting_id == meeting_uuid,
            CrmPushResult.provider == provider,
        )
    )
    push_result = result.scalar_one_or_none()

    if not push_result:
        push_result = CrmPushResult(
            meeting_id=meeting_uuid,
            team_id=team_uuid,
            provider=provider,
            status="processing",
            extraction_snapshot=crm_extraction,
            pushed_by=uuid_mod.UUID(pushed_by) if pushed_by else None,
        )
        db.add(push_result)
        await db.flush()
    else:
        push_result.status = "processing"
        push_result.error_message = None

    await db.commit()

    # Call integration service
    push_response = await push_to_crm(
        team_id=team_id,
        meeting_id=meeting_id,
        provider=provider,
        crm_extraction=crm_extraction,
        call_metadata=call_metadata,
        edited_data=edited_data,
    )

    # Update push result
    push_result.status = push_response.get("status", "failed")
    push_result.crm_contact_id = push_response.get("crm_contact_id")
    push_result.crm_deal_id = push_response.get("crm_deal_id")
    push_result.crm_record_id = push_response.get("crm_record_id")
    push_result.crm_record_url = push_response.get("crm_record_url")
    push_result.pushed_data = push_response.get("pushed_data", {})
    push_result.error_message = push_response.get("error_message")
    push_result.pushed_at = datetime.now(timezone.utc)
    push_result.updated_at = datetime.now(timezone.utc)

    if push_result.status == "failed":
        push_result.retry_count = (push_result.retry_count or 0) + 1

    await db.commit()

    # Update aggregate status on memoir_summaries
    await _update_memoir_summary_status(db, meeting_uuid)

    # Update speaker CRM links if contact was found/created
    if push_result.status == "completed" and push_result.crm_contact_id:
        await _update_speaker_crm_links(db, meeting_uuid, team_uuid, provider, push_result.crm_contact_id, crm_extraction)

    # After successful push, create audit entry
    if push_result.status == "completed":
        try:
            audit = CrmPushAudit(
                meeting_id=meeting_uuid,
                team_id=team_uuid,
                push_result_id=push_result.id,
                provider=provider,
                push_type=push_type,
                previous_values=push_response.get("previous_values"),
                new_values=push_response.get("pushed_data"),
                pushed_by=uuid_mod.UUID(pushed_by) if pushed_by else None,
            )
            db.add(audit)
            await db.commit()
            logger.info(f"CRM push audit created for meeting {meeting_id}, provider {provider}, type {push_type}")
        except Exception as e:
            logger.error(f"Failed to create CRM push audit: {e}")

    return push_result


async def _update_memoir_summary_status(db: AsyncSession, meeting_id) -> None:
    """Update the aggregate crm_push_status on memoir_summaries."""
    try:
        from app.models.memoir_summary import MemoirSummary

        # Get all push results for this meeting
        result = await db.execute(
            select(CrmPushResult).where(CrmPushResult.meeting_id == meeting_id)
        )
        push_results = result.scalars().all()

        if not push_results:
            return

        # Determine aggregate status
        statuses = {pr.status for pr in push_results}
        if "completed" in statuses:
            agg_status = "pushed"
        elif "processing" in statuses:
            agg_status = "processing"
        elif "failed" in statuses:
            agg_status = "failed"
        else:
            agg_status = "pending"

        # Update memoir_summary
        from sqlalchemy import desc
        ms_result = await db.execute(
            select(MemoirSummary)
            .where(MemoirSummary.meeting_id == meeting_id)
            .order_by(desc(MemoirSummary.version))
            .limit(1)
        )
        memoir_summary = ms_result.scalar_one_or_none()
        if memoir_summary:
            memoir_summary.crm_push_status = agg_status
            if agg_status == "pushed":
                memoir_summary.crm_pushed_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to update memoir_summary CRM status: {e}")


async def _update_speaker_crm_links(
    db: AsyncSession,
    meeting_id,
    team_id,
    provider: str,
    crm_contact_id: str,
    crm_extraction: dict,
) -> None:
    """Update speaker_profiles.crm_links after successful push."""
    try:
        from app.models.speaker import SpeakerProfile, MeetingParticipant

        # Get the contact info from extraction
        crm = crm_extraction.get("crm", crm_extraction)
        contact = crm.get("contact", crm.get("prospect", {}))
        if isinstance(contact, str):
            contact = {"name": contact}

        email = contact.get("email", "")
        name = contact.get("name", contact.get("full_name", ""))

        if not email and not name:
            return

        # Find matching speaker profile
        if email:
            result = await db.execute(
                select(SpeakerProfile).where(
                    SpeakerProfile.team_id == team_id,
                    SpeakerProfile.email == email,
                )
            )
        else:
            result = await db.execute(
                select(SpeakerProfile).where(
                    SpeakerProfile.team_id == team_id,
                    SpeakerProfile.display_name == name,
                )
            )

        speaker = result.scalar_one_or_none()
        if speaker:
            links = dict(speaker.crm_links or {})
            links[provider] = crm_contact_id
            speaker.crm_links = links
            await db.commit()
            logger.info(f"Updated speaker {speaker.id} CRM links: {provider}={crm_contact_id}")

    except Exception as e:
        logger.error(f"Failed to update speaker CRM links: {e}")


async def manual_push(
    db: AsyncSession,
    meeting_id: str,
    team_id: str,
    provider: str,
    pushed_by: str,
    edited_data: dict | None = None,
) -> CrmPushResult:
    """Manual push triggered by user from CRM Update tab."""
    import uuid as uuid_mod
    from app.models.call_intelligence import CallIntelligence
    from app.core.config import settings

    meeting_uuid = uuid_mod.UUID(meeting_id)

    # Get CRM extraction from call_intelligence — order by latest to handle
    # retranscribed meetings (multiple rows; no version column on the table).
    ci_result = await db.execute(
        select(CallIntelligence)
        .where(CallIntelligence.meeting_id == meeting_uuid)
        .order_by(CallIntelligence.version.desc())
        .limit(1)
    )
    ci = ci_result.scalar_one_or_none()
    crm_extraction = ci.crm_extraction if ci else {}

    if not crm_extraction and not edited_data:
        raise ValueError("No CRM extraction data available")

    # Get meeting for metadata
    from app.models.meeting import Meeting
    m_result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = m_result.scalar_one_or_none()
    if not meeting:
        raise ValueError("Meeting not found")

    call_metadata = {
        "title": meeting.title or "Untitled Call",
        "duration": meeting.duration_seconds or 0,
        "url": f"{settings.FRONTEND_URL}/meetings/{meeting_id}",
        "timestamp": meeting.created_at.isoformat() if meeting.created_at else None,
    }

    return await _push_single_provider(
        db, meeting_id, team_id, provider, crm_extraction, call_metadata,
        pushed_by=pushed_by, edited_data=edited_data, push_type="manual",
    )
