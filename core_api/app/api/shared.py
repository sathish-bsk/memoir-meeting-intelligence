"""
Public share endpoints — create/revoke share links and serve public data.
Uses the unified share_links table for both call and intelligence shares.
"""

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.call_intelligence import ShareLink
from app.models.meeting import Meeting
from app.models.team import TeamMember
from app.models.transcription import Transcription
from app.services.memoir_helpers import (
    transform_action_items,
    transform_disclaimer,
    transform_discovery_fields,
    transform_signals,
)
from videoclip_common.auth import UserContext, get_current_user
from videoclip_common.storage import create_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sharing"])


# ─── Schemas ──────────────────────────────────────────────────────


class IntelligenceShareRequest(BaseModel):
    team_id: str
    bucket_type: str
    title: str
    items: list
    total_calls: int


# ─── Authenticated: Call share endpoints ─────────────────────────


@router.post("/api/meetings/{meeting_id}/share")
async def create_share_link(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a public share token for a meeting."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Check for existing share link for this call
    result = await db.execute(
        select(ShareLink).where(
            ShareLink.share_type == "call",
            ShareLink.metadata_["meeting_id"].astext == str(meeting_id),
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        return {
            "share_token": existing.share_token,
            "share_url": f"/shared/{existing.share_token}",
            "views": existing.share_views,
        }

    # Create new share link
    link = ShareLink(
        share_token=secrets.token_urlsafe(32),
        share_type="call",
        team_id=meeting.team_id,
        created_by=uuid.UUID(user.user_id),
        title=meeting.title or "Untitled Call",
        metadata_={"meeting_id": str(meeting_id)},
        snapshot_data={},
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)

    return {
        "share_token": link.share_token,
        "share_url": f"/shared/{link.share_token}",
        "views": link.share_views,
    }


@router.delete("/api/meetings/{meeting_id}/share")
async def revoke_share_link(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a public share link for a meeting."""
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    result = await db.execute(
        select(ShareLink).where(
            ShareLink.share_type == "call",
            ShareLink.metadata_["meeting_id"].astext == str(meeting_id),
        )
    )
    link = result.scalar_one_or_none()

    if link:
        await db.delete(link)
        await db.commit()

    return {"status": "revoked", "meeting_id": str(meeting_id)}


# ─── Authenticated: Intelligence share endpoints ─────────────────


@router.post("/api/intelligence/share")
async def create_intelligence_share(
    body: IntelligenceShareRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a public share link for an intelligence deep dive."""
    # Verify team membership
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == body.team_id,
            TeamMember.user_id == user.user_id,
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    token = secrets.token_urlsafe(32)
    link = ShareLink(
        share_token=token,
        share_type="intelligence",
        team_id=uuid.UUID(body.team_id),
        created_by=uuid.UUID(user.user_id),
        title=body.title,
        metadata_={"bucket_type": body.bucket_type, "total_calls": body.total_calls},
        snapshot_data=body.items,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)

    return {
        "share_token": link.share_token,
        "share_url": f"/shared/intelligence/{link.share_token}",
    }


# ─── Public endpoints (no auth) ─────────────────────────────────


@router.get("/api/shared/{token}")
async def get_shared_call(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — returns call data for a shared link. No auth required."""
    result = await db.execute(
        select(ShareLink).where(ShareLink.share_token == token)
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Shared call not found or link has been revoked")

    # If this is an intelligence link accessed via the call route, still 404
    if link.share_type != "call":
        raise HTTPException(404, "Shared call not found or link has been revoked")

    # Increment view count
    link.share_views = (link.share_views or 0) + 1
    await db.commit()

    # Extract meeting_id from metadata
    meeting_id_str = (link.metadata_ or {}).get("meeting_id")
    if not meeting_id_str:
        raise HTTPException(404, "Shared call not found or link has been revoked")

    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == uuid.UUID(meeting_id_str))
    )
    meeting = meeting_result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Shared call not found or link has been revoked")

    # Load analysis.json
    analysis = {}
    storage = create_storage()

    # Resolve latest transcription path
    tx_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting.id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    transcription = tx_result.scalar_one_or_none()

    analysis_path = None
    segments = []
    speaker_map = {}

    if transcription:
        if transcription.segments_path:
            from pathlib import PurePosixPath
            parent = str(PurePosixPath(transcription.segments_path).parent)
            analysis_path = f"{parent}/analysis.json"

        if transcription.segments_path:
            try:
                raw_segments = await storage.read_json(transcription.segments_path)
                if isinstance(raw_segments, list):
                    segments = raw_segments[:500]
            except Exception:
                pass

        if transcription.speaker_map:
            speaker_map = transcription.speaker_map

    if not analysis_path and meeting.analysis_path:
        analysis_path = meeting.analysis_path

    if analysis_path:
        try:
            analysis = await storage.read_json(analysis_path)
        except Exception:
            analysis = {}

    memoir = analysis.get("intelligence") or analysis.get("memoir") or {}

    discovery_fields = transform_discovery_fields(memoir.get("discovery_fields") or {})
    action_items = transform_action_items(memoir.get("action_items") or {})
    signals = transform_signals(memoir.get("signals") or {})
    disclaimer = transform_disclaimer(memoir.get("disclaimer"))

    coaching = analysis.get("coaching") or {}
    coaching_metrics = coaching.get("coaching_metrics")

    return {
        "title": meeting.title or "Untitled Call",
        "date": meeting.created_at.isoformat() if meeting.created_at else None,
        "duration_seconds": meeting.duration_seconds,
        "source": meeting.source,
        "views": link.share_views,
        "narrative_summary": memoir.get("narrative_summary"),
        "call_metadata": memoir.get("call_metadata"),
        "next_steps": memoir.get("next_steps"),
        "action_items": action_items,
        "signals": signals,
        "discovery_fields": discovery_fields,
        "coaching_metrics": coaching_metrics,
        "disclaimer": disclaimer,
        "overall_confidence_score": memoir.get("overall_confidence_score"),
        "transcript": {
            "segments": segments,
            "speaker_map": speaker_map,
        },
    }


@router.get("/api/shared/intelligence/{token}")
async def get_shared_intelligence(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — returns intelligence deep dive snapshot. No auth required."""
    result = await db.execute(
        select(ShareLink).where(
            ShareLink.share_token == token,
            ShareLink.share_type == "intelligence",
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Shared intelligence not found or link has been revoked")

    # Increment view count
    link.share_views = (link.share_views or 0) + 1
    await db.commit()

    metadata = link.metadata_ or {}

    return {
        "title": link.title,
        "bucket_type": metadata.get("bucket_type"),
        "items": link.snapshot_data or [],
        "total_calls": metadata.get("total_calls", 0),
        "views": link.share_views,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


# ─── Helpers ──────────────────────────────────────────────────────


async def _get_meeting_with_access(
    db: AsyncSession, meeting_id: uuid.UUID, user_id: str
) -> Meeting:
    """Load a meeting and verify the caller is a team member."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == meeting.team_id,
            TeamMember.user_id == user_id,
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    return meeting
