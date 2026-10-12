"""
CRM integration API router — mounted at /api/crm.
Proxies OAuth to integration_service, manages push results, field/stage mapping.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.service_auth import verify_internal_call
from app.models.crm_push_result import CrmPushResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm", tags=["crm"])

# Service-to-service router. Sits under /api/internal/* so it routes through
# Traefik's chain-webhooks (no ForwardAuth) and reaches us with the
# X-Service-Key header intact, which verify_internal_call validates against
# INTERNAL_SERVICE_KEY. The previous /api/crm/internal/contacts path fell
# through to chain-api (with ForwardAuth) and 401'd before reaching the
# handler — see core_api/docker-compose.yml router priorities.
internal_router = APIRouter(
    prefix="/api/internal/crm",
    tags=["crm-internal"],
    dependencies=[Depends(verify_internal_call)],
)

INTEGRATION_URL = settings.INTEGRATION_SERVICE_URL.rstrip("/")
VALID_PROVIDERS = {"hubspot", "zoho"}


def _validate_provider(provider: str):
    if provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}")


# ─── Connection Management (proxy to integration_service) ────────────

@router.get("/connections")
async def list_crm_connections(
    user: UserContext = Depends(get_current_user),
):
    """List connected CRMs for the user's team."""
    from app.clients.crm_client import get_crm_connections
    connections = await get_crm_connections(user.team_id)
    return {"connections": connections}


@router.get("/{provider}/auth-url")
async def get_crm_auth_url(
    provider: str,
    region: str = Query("us"),
    user: UserContext = Depends(get_current_user),
):
    """Get OAuth URL for a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/auth-url",
                params={"user_id": user.user_id, "team_id": user.team_id, "region": region},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, f"Failed to get {provider} auth URL: {e}")


@router.post("/{provider}/callback")
async def crm_oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    region: str = Query("us"),
    accounts_server: str = Query(""),
    user: UserContext = Depends(get_current_user),
):
    """Complete OAuth callback — proxy to integration_service with team_id."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/connect",
                params={
                    "code": code,
                    "state": state,
                    "user_id": user.user_id,
                    "team_id": user.team_id,
                    "region": region,
                    "accounts_server": accounts_server,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, f"Failed to connect {provider}: {e}")


@router.delete("/{provider}/disconnect")
async def disconnect_crm(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Disconnect a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/disconnect",
                params={"user_id": user.user_id, "team_id": user.team_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, f"Failed to disconnect {provider}: {e}")


@router.get("/{provider}/status")
async def get_crm_status(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Check CRM connection status."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/status",
                params={"team_id": user.team_id},
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"No {provider} connection found")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to check {provider} status: {e}")


class CrmSettingsUpdate(BaseModel):
    auto_push_enabled: bool | None = None


@router.put("/{provider}/settings")
async def update_crm_settings(
    provider: str,
    body: CrmSettingsUpdate,
    user: UserContext = Depends(get_current_user),
):
    """Update CRM connection settings."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/settings",
                params={"team_id": user.team_id},
                json=body.model_dump(exclude_none=True),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, str(e))


# ─── Push Results ────────────────────────────────────────────────────

@router.get("/push-results/{meeting_id}")
async def get_push_results(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get CRM push results for a meeting (all providers)."""
    result = await db.execute(
        select(CrmPushResult).where(CrmPushResult.meeting_id == meeting_id)
    )
    push_results = result.scalars().all()

    return {
        "results": [
            {
                "id": str(pr.id),
                "provider": pr.provider,
                "status": pr.status,
                "crm_contact_id": pr.crm_contact_id,
                "crm_deal_id": pr.crm_deal_id,
                "crm_record_id": pr.crm_record_id,
                "crm_record_url": pr.crm_record_url,
                "pushed_data": pr.pushed_data,
                "edited_data": pr.edited_data,
                "extraction_snapshot": pr.extraction_snapshot,
                "error_message": pr.error_message,
                "retry_count": pr.retry_count,
                "pushed_at": pr.pushed_at.isoformat() if pr.pushed_at else None,
                "created_at": pr.created_at.isoformat() if pr.created_at else None,
            }
            for pr in push_results
        ]
    }


@router.post("/push/{meeting_id}/{provider}")
async def push_to_crm(
    meeting_id: uuid.UUID,
    provider: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manual push / re-push to a specific CRM."""
    _validate_provider(provider)

    from app.services.crm_push_dispatcher import manual_push

    try:
        # Check for edited data
        existing = await db.execute(
            select(CrmPushResult).where(
                CrmPushResult.meeting_id == meeting_id,
                CrmPushResult.provider == provider,
            )
        )
        existing_result = existing.scalar_one_or_none()
        edited_data = existing_result.edited_data if existing_result else None

        push_result = await manual_push(
            db=db,
            meeting_id=str(meeting_id),
            team_id=user.team_id,
            provider=provider,
            pushed_by=user.user_id,
            edited_data=edited_data,
        )

        return {
            "status": push_result.status,
            "crm_contact_id": push_result.crm_contact_id,
            "crm_deal_id": push_result.crm_deal_id,
            "crm_record_id": push_result.crm_record_id,
            "crm_record_url": push_result.crm_record_url,
            "error_message": push_result.error_message,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Manual CRM push failed: {e}")
        raise HTTPException(500, f"Push failed: {str(e)}")


@router.post("/skip/{meeting_id}/{provider}")
async def skip_crm_push(
    meeting_id: uuid.UUID,
    provider: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skip CRM push for a meeting+provider."""
    _validate_provider(provider)

    result = await db.execute(
        select(CrmPushResult).where(
            CrmPushResult.meeting_id == meeting_id,
            CrmPushResult.provider == provider,
        )
    )
    push_result = result.scalar_one_or_none()

    if push_result:
        push_result.status = "skipped"
    else:
        push_result = CrmPushResult(
            meeting_id=meeting_id,
            team_id=uuid.UUID(user.team_id),
            provider=provider,
            status="skipped",
        )
        db.add(push_result)

    await db.commit()
    return {"status": "skipped", "meeting_id": str(meeting_id), "provider": provider}


class EditCrmDataRequest(BaseModel):
    data: dict


@router.put("/push/{meeting_id}/{provider}/edit")
async def edit_crm_push_data(
    meeting_id: uuid.UUID,
    provider: str,
    body: EditCrmDataRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save edited CRM data (before re-push)."""
    _validate_provider(provider)

    result = await db.execute(
        select(CrmPushResult).where(
            CrmPushResult.meeting_id == meeting_id,
            CrmPushResult.provider == provider,
        )
    )
    push_result = result.scalar_one_or_none()

    if not push_result:
        # Create a pending result with edited data
        push_result = CrmPushResult(
            meeting_id=meeting_id,
            team_id=uuid.UUID(user.team_id),
            provider=provider,
            status="pending",
            edited_data=body.data,
        )
        db.add(push_result)
    else:
        push_result.edited_data = body.data

    await db.commit()
    return {"status": "saved", "meeting_id": str(meeting_id), "provider": provider}


# ─── Field / Stage Mapping (proxy to integration_service) ────────────

@router.get("/{provider}/properties")
async def get_crm_properties(
    provider: str,
    object_type: str = Query("contacts"),
    user: UserContext = Depends(get_current_user),
):
    """Fetch CRM field list for mapping UI."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/properties",
                params={"team_id": user.team_id, "object_type": object_type},
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"No {provider} connection found")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch properties: {e}")


@router.get("/{provider}/stages")
async def get_crm_stages(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Fetch CRM pipeline stages for mapping UI."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/stages",
                params={"team_id": user.team_id},
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"No {provider} connection found")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch stages: {e}")


@router.get("/{provider}/pipeline-stages")
async def get_crm_pipeline_stages(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Get CRM pipeline stages (proxied from integration_service)."""
    _validate_provider(provider)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/stages",
                params={"team_id": user.team_id},
                headers={"X-Service-Key": settings.SERVICE_KEY},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to get pipeline stages: {e}")
        raise HTTPException(502, "Failed to fetch CRM pipeline stages")


@router.get("/{provider}/field-mapping")
async def get_field_mapping(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Get current field mapping for a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/status",
                params={"team_id": user.team_id},
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"No {provider} connection found")
            resp.raise_for_status()
            data = resp.json()
            return {"field_mapping": data.get("field_mapping", {})}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e))


class MappingRequest(BaseModel):
    mapping: dict


@router.put("/{provider}/field-mapping")
async def save_field_mapping(
    provider: str,
    body: MappingRequest,
    user: UserContext = Depends(get_current_user),
):
    """Save field mapping for a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/field-mapping",
                params={"team_id": user.team_id},
                json={"mapping": body.mapping},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/{provider}/stage-mapping")
async def get_stage_mapping(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Get current stage mapping for a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/status",
                params={"team_id": user.team_id},
            )
            if resp.status_code == 404:
                raise HTTPException(404, f"No {provider} connection found")
            resp.raise_for_status()
            data = resp.json()
            return {"stage_mapping": data.get("stage_mapping", {})}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e))


@router.put("/{provider}/stage-mapping")
async def save_stage_mapping(
    provider: str,
    body: MappingRequest,
    user: UserContext = Depends(get_current_user),
):
    """Save stage mapping for a CRM provider."""
    _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{INTEGRATION_URL}/integrations/crm/{provider}/stage-mapping",
                params={"team_id": user.team_id},
                json={"mapping": body.mapping},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        raise HTTPException(502, str(e))


# ─── Regenerate CRM Extraction (single call — async job queue) ────────

@router.post("/regenerate/{meeting_id}")
async def regenerate_single_crm_extraction(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue CRM extraction (P2 prompt) for a single meeting. Returns immediately."""
    from app.models.meeting import Meeting
    from app.models.transcription_job import TranscriptionJob

    # Verify the meeting belongs to the user's team
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if str(meeting.team_id) != user.team_id:
        raise HTTPException(403, "Meeting does not belong to your team")

    # Check for already-running job
    existing = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.operation_type == 2,
            TranscriptionJob.status.in_(["waiting", "in_progress"]),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "CRM extraction already in progress for this meeting")

    from app.services.crm_extraction import create_crm_extraction_job

    try:
        job_id = await create_crm_extraction_job(db, meeting_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"status": "queued", "meeting_id": str(meeting_id), "job_id": job_id}


@router.get("/regenerate-status/{meeting_id}")
async def get_crm_regenerate_status(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll the status of the latest CRM extraction job for a meeting."""
    from app.models.meeting import Meeting
    from app.models.transcription_job import TranscriptionJob
    from app.models.call_intelligence import CallIntelligence

    # Verify the meeting belongs to the user's team
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if str(meeting.team_id) != user.team_id:
        raise HTTPException(403, "Meeting does not belong to your team")

    # Find latest CRM extraction job for this meeting
    job_result = await db.execute(
        select(TranscriptionJob)
        .where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.operation_type == 2,
        )
        .order_by(TranscriptionJob.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()

    if not job:
        return {"status": "none", "crm_extraction": None, "error": None}

    if job.status in ("waiting", "in_progress"):
        return {"status": job.status, "crm_extraction": None, "error": None}

    if job.status == "completed":
        # Fetch the stored crm_extraction from call_intelligence — order by
        # latest to handle retranscribed meetings (multiple rows; no version).
        ci_result = await db.execute(
            select(CallIntelligence)
            .where(CallIntelligence.meeting_id == meeting_id)
            .order_by(CallIntelligence.version.desc())
            .limit(1)
        )
        ci = ci_result.scalar_one_or_none()
        crm_data = ci.crm_extraction if ci else None
        return {"status": "completed", "crm_extraction": crm_data, "error": None}

    # failed
    return {"status": "failed", "crm_extraction": None, "error": job.error_message}


# ─── Regenerate CRM Data (batch) ────────────────────────────────────

@router.post("/regenerate")
async def regenerate_crm_data(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run CRM extraction for all calls that lack crm_extraction data.
    Only generates data for connected CRM providers."""
    from app.models.meeting import Meeting
    from app.models.call_intelligence import CallIntelligence
    from app.clients.crm_client import get_crm_connections
    from sqlalchemy import or_

    team_uuid = uuid.UUID(user.team_id)

    # Check which CRMs are connected
    connections = await get_crm_connections(user.team_id)
    active_providers = [c["provider"] for c in connections if c.get("is_active")]
    if not active_providers:
        raise HTTPException(400, "No CRM is connected. Connect a CRM first.")

    # Find meetings with intelligence data but no crm_extraction
    result = await db.execute(
        select(Meeting.id, Meeting.title, CallIntelligence.id.label("ci_id"))
        .join(CallIntelligence, CallIntelligence.meeting_id == Meeting.id)
        .where(
            Meeting.team_id == team_uuid,
            Meeting.status == "completed",
            or_(
                CallIntelligence.crm_extraction.is_(None),
                CallIntelligence.crm_extraction == {},
            ),
        )
        .order_by(Meeting.created_at.desc())
    )
    meetings_without_crm = result.all()

    if not meetings_without_crm:
        return {"status": "no_meetings", "total": 0, "message": "All calls already have CRM data extracted."}

    # For now, mark them for re-processing by creating pending push results.
    # The actual extraction happens via the intelligence pipeline.
    #
    # P1.3 (2026-05-31): killed the nested N+1 SELECT (was one round-trip
    # per meeting × provider, so 50 meetings × 2 providers = 100 round-
    # trips at ~500 ms each = ~50 s under prod load). Batch-fetch all
    # existing (meeting_id, provider) pairs in ONE query, then in-memory
    # set check during the loop. See plan/performance-optimization-audit.md
    # §1.3.
    existing_rows = (await db.execute(
        select(CrmPushResult.meeting_id, CrmPushResult.provider)
        .where(
            CrmPushResult.meeting_id.in_([m.id for m in meetings_without_crm]),
            CrmPushResult.provider.in_(active_providers),
        )
    )).all()
    existing_pairs = {(row.meeting_id, row.provider) for row in existing_rows}

    count = 0
    for m in meetings_without_crm:
        for provider in active_providers:
            if (m.id, provider) in existing_pairs:
                continue
            pr = CrmPushResult(
                meeting_id=m.id,
                team_id=team_uuid,
                provider=provider,
                status="pending",
                pushed_by=uuid.UUID(user.user_id),
            )
            db.add(pr)
            count += 1

    await db.commit()

    return {
        "status": "started",
        "total_meetings": len(meetings_without_crm),
        "total_push_results": count,
        "providers": active_providers,
        "meeting_ids": [str(m.id) for m in meetings_without_crm],
    }


# ─── Batch Push ──────────────────────────────────────────────────────

@router.post("/{provider}/batch-push")
async def start_batch_push(
    provider: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Push historical calls to CRM (after first connect)."""
    _validate_provider(provider)

    # Get all meetings with intelligence data that haven't been pushed to this provider
    from app.models.meeting import Meeting
    from app.models.call_intelligence import CallIntelligence

    team_uuid = uuid.UUID(user.team_id)

    # Get meetings with intelligence but no push result for this provider
    meetings_result = await db.execute(
        select(Meeting.id, Meeting.title, Meeting.created_at, Meeting.duration_seconds)
        .join(CallIntelligence, CallIntelligence.meeting_id == Meeting.id)
        .outerjoin(
            CrmPushResult,
            (CrmPushResult.meeting_id == Meeting.id) & (CrmPushResult.provider == provider),
        )
        .where(
            Meeting.team_id == team_uuid,
            CallIntelligence.crm_extraction.isnot(None),
            CrmPushResult.id.is_(None),
        )
        .order_by(Meeting.created_at.asc())
    )
    meetings = meetings_result.all()

    if not meetings:
        return {"status": "no_meetings", "total": 0}

    # P1.4 (2026-05-31): replaced per-meeting db.add() loop with single
    # multi-row INSERT. Each ORM-add of CrmPushResult flushed as its own
    # INSERT on commit — at ~80 ms/round-trip × 500 historical meetings
    # that was 40 s of wall-clock per fresh batch-push. ON CONFLICT DO
    # NOTHING preserves idempotence under duplicate/concurrent calls
    # (CrmPushResult has a unique constraint on (meeting_id, provider)).
    # See plan/performance-optimization-audit.md §1.4.
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    await db.execute(
        pg_insert(CrmPushResult)
        .values([
            {
                "meeting_id": m.id,
                "team_id": team_uuid,
                "provider": provider,
                "status": "pending",
                "pushed_by": uuid.UUID(user.user_id),
            }
            for m in meetings
        ])
        .on_conflict_do_nothing(index_elements=["meeting_id", "provider"])
    )
    await db.commit()

    # Return count — actual pushes will be processed by a background loop
    # For now, return the task info
    return {
        "status": "started",
        "total": len(meetings),
        "meeting_ids": [str(m.id) for m in meetings],
    }


@router.get("/{provider}/batch-push/status")
async def get_batch_push_status(
    provider: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get batch push progress."""
    _validate_provider(provider)

    team_uuid = uuid.UUID(user.team_id)

    from sqlalchemy import func
    result = await db.execute(
        select(CrmPushResult.status, func.count(CrmPushResult.id))
        .where(
            CrmPushResult.team_id == team_uuid,
            CrmPushResult.provider == provider,
        )
        .group_by(CrmPushResult.status)
    )
    counts = {row[0]: row[1] for row in result.all()}

    total = sum(counts.values())
    completed = counts.get("completed", 0) + counts.get("skipped", 0)

    return {
        "total": total,
        "completed": completed,
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "failed": counts.get("failed", 0),
        "progress": completed / total if total > 0 else 0,
    }


# ─── Internal: CRM contacts for ASR context ──────────────────────────
# Mounted on internal_router, which is included separately at
# /api/internal/crm. Final URL: /api/internal/crm/contacts.

@internal_router.get("/contacts")
async def internal_crm_contacts(
    team_id: str = Query(...),
):
    """Fetch contact names from all connected CRMs for a team.

    Called by TranscriptionBase to get names for Whisper prompt
    conditioning and post-ASR fuzzy name correction.
    Returns empty list if no CRM connected.
    """
    from app.clients.crm_client import list_crm_contacts
    contacts = await list_crm_contacts(team_id)
    return {"contacts": contacts}
