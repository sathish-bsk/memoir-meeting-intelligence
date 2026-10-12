"""
Re-run CRM extraction (P2 prompt) for a single meeting.

Creates a transcription_jobs row with operation_type=2 so the
TranscriptionBase worker picks it up, reads segments from the filesystem,
runs the P2 prompt, and calls back via webhook.
"""

import logging
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.call_intelligence import CallIntelligence
from app.models.meeting import Meeting
from app.models.transcription import Transcription
from app.models.transcription_job import TranscriptionJob

logger = logging.getLogger(__name__)


async def create_crm_extraction_job(
    db: AsyncSession,
    meeting_id: uuid.UUID,
) -> str:
    """Create a CRM extraction job for a single meeting.

    Returns the job_id (as string).
    Raises ValueError on validation failure.
    """
    # 1. Load meeting
    m_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = m_result.scalar_one_or_none()
    if not meeting:
        raise ValueError("Meeting not found")

    # 2. Load latest transcription
    t_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(desc(Transcription.version))
        .limit(1)
    )
    transcription = t_result.scalar_one_or_none()
    if not transcription or not transcription.segments_path:
        raise ValueError("No transcript available for this meeting")

    # 3. Gather context from existing intelligence data
    prospect_name = None
    prospect_company = None
    rep_name = None

    # Retranscribed meetings have multiple call_intelligence rows (no version
    # column). Take the latest by created_at — otherwise scalar_one_or_none
    # raises MultipleResultsFound and CRM extraction queueing silently fails.
    ci_result = await db.execute(
        select(CallIntelligence)
        .where(CallIntelligence.meeting_id == meeting_id)
        .order_by(CallIntelligence.version.desc())
        .limit(1)
    )
    existing_ci = ci_result.scalar_one_or_none()
    if existing_ci and existing_ci.crm_extraction:
        contact = existing_ci.crm_extraction.get("contact", {})
        if isinstance(contact, dict):
            prospect_name = contact.get("name")
            prospect_company = contact.get("company")

    call_date = meeting.started_at.strftime("%Y-%m-%d") if meeting.started_at else None
    speaker_map = transcription.speaker_map or {}

    # 4. Create transcription_jobs row
    job = TranscriptionJob(
        meeting_id=meeting_id,
        operation_type=2,
        status="waiting",
        bot_job_id=f"crm-extract-{meeting_id}",
        params={
            "segments_path": transcription.segments_path,
            "speaker_map": speaker_map,
            "prospect_name": prospect_name,
            "prospect_company": prospect_company,
            "call_date": call_date,
            "rep_name": rep_name,
            "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
            "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        },
    )
    db.add(job)
    await db.commit()

    logger.info("CRM extraction job created for meeting %s (job %s)", meeting_id, job.id)
    return str(job.id)
