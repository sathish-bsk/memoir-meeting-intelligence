"""
Read and edit endpoints for meeting content: transcription, coaching, intelligence, memoir.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.memoir_summary import MemoirSummary
from app.models.speaker import MeetingParticipant, SpeakerProfile
from app.models.team import TeamMember
from app.models.transcription import Transcription
from app.schemas.intelligence import (
    CoachingResponse,
    FieldEditRequest,
    IntelligenceResponse,
    MemoirEditRequest,
    MemoirResponse,
)
from app.schemas.speaker import (
    MeetingSpeakersResponse,
    ParticipantOut,
    SpeakerAssignRequest,
    SpeakerProfileOut,
)
from app.schemas.transcript import (
    TranscriptEditRequest,
    TranscriptResponse,
    TranscriptSegment,
)
from videoclip_common.auth import UserContext, get_current_user, decode_jwt
from videoclip_common.events import RedisEventPublisher
from videoclip_common.storage import create_storage
from app.services.memoir_helpers import (
    transform_discovery_fields,
    transform_action_items,
    transform_signals,
    transform_disclaimer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meeting-content"])


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
            TeamMember.user_id == uuid.UUID(user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    return meeting


async def _load_analysis(meeting: Meeting, analysis_path: str | None = None) -> dict[str, Any]:
    """Read and return the analysis.json for a meeting.

    If analysis_path is provided (versioned), use it. Otherwise fall back
    to meeting.analysis_path (legacy).
    """
    path = analysis_path or meeting.analysis_path
    if not path:
        raise HTTPException(404, "No analysis available for this meeting")

    storage = create_storage()
    try:
        return await storage.read_json(path)
    except FileNotFoundError:
        raise HTTPException(404, "Analysis file not found")
    except Exception as e:
        logger.error(f"Failed to read analysis for meeting {meeting.id}: {e}")
        raise HTTPException(500, "Failed to read analysis data")


# --- Transcription endpoints ---


@router.get("/{meeting_id}/transcription", response_model=TranscriptResponse)
async def get_transcription(
    meeting_id: uuid.UUID,
    version: Optional[int] = Query(None, description="Transcription version (default: latest)"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read transcript segments from storage. Defaults to latest version."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Build query — specific version or latest
    query = select(Transcription).where(Transcription.meeting_id == meeting_id)
    if version is not None:
        query = query.where(Transcription.version == version)
    else:
        query = query.order_by(Transcription.version.desc()).limit(1)

    tx_result = await db.execute(query)
    transcription = tx_result.scalar_one_or_none()

    storage = create_storage()

    # Fast-P5 fallback: if the polished Transcription row doesn't exist yet
    # (Type 5 hasn't completed), serve the Type 7 fast transcript so the UI
    # has something to render between T+7min and T+20min. Initial run only —
    # for retranscribed meetings the previous row keeps serving until v(N)
    # polish lands.
    if not transcription or not transcription.segments_path:
        if version is None:
            fast_key = f"meetings/{meeting_id}/v1/transcript_segments_fast.json"
            try:
                if await storage.exists(fast_key):
                    fast_segments = await storage.read_json(fast_key)
                    speakers = {
                        s.get("speaker") for s in fast_segments
                        if isinstance(s, dict) and s.get("speaker")
                    }
                    word_count = sum(
                        len((s.get("text") or "").split()) for s in fast_segments
                    )
                    return TranscriptResponse(
                        meeting_id=meeting_id,
                        segments=[TranscriptSegment(**seg) for seg in fast_segments],
                        speaker_count=len(speakers),
                        word_count=word_count,
                        language="en",
                        speaker_map=None,
                        is_edited=False,
                    )
            except Exception as e:
                logger.warning(f"Meeting {meeting_id}: fast transcript fallback failed: {e}")
        raise HTTPException(404, "No transcription available for this meeting")

    async def _try_recover_from_original() -> list | None:
        """Read the *_original.json backup and return its parsed contents.

        Used when transcript_segments.json fails to parse — most commonly
        because a buggy edit handler clobbered it with plain text (when
        full_text_path == segments_path). Returns None if the backup
        doesn't exist or is itself unreadable.
        """
        backup = transcription.segments_path.replace(
            "transcript_segments.json", "transcript_segments_original.json"
        )
        try:
            if not await storage.exists(backup):
                return None
            return await storage.read_json(backup)
        except Exception:
            return None

    try:
        segments_data = await storage.read_json(transcription.segments_path)
    except FileNotFoundError:
        # Try the original backup before giving up.
        recovered = await _try_recover_from_original()
        if recovered is not None:
            logger.warning(
                f"Meeting {meeting_id}: segments file missing — serving "
                f"original backup."
            )
            segments_data = recovered
        else:
            raise HTTPException(404, "Transcript segments file not found")
    except Exception as e:
        # JSON parse error or other read failure. Auto-recover from the
        # original backup if present (covers the path-collision bug
        # where the segments file got overwritten with plain text).
        recovered = await _try_recover_from_original()
        if recovered is not None:
            logger.warning(
                f"Meeting {meeting_id}: segments file unreadable ({e}); "
                f"serving original backup."
            )
            segments_data = recovered
        else:
            logger.error(f"Failed to read transcript for meeting {meeting_id}: {e}")
            raise HTTPException(500, "Failed to read transcript")

    segments = [TranscriptSegment(**seg) for seg in segments_data]

    # Check if transcript has been edited (backup file exists)
    original_path = transcription.segments_path.replace(
        "transcript_segments.json", "transcript_segments_original.json"
    )
    is_edited = await storage.exists(original_path)

    return TranscriptResponse(
        meeting_id=meeting_id,
        segments=segments,
        speaker_count=transcription.speaker_count or 0,
        word_count=transcription.word_count or 0,
        language=transcription.language or "en",
        speaker_map=transcription.speaker_map,
        is_edited=is_edited,
    )


@router.patch("/{meeting_id}/transcription")
async def edit_transcription(
    meeting_id: uuid.UUID,
    body: TranscriptEditRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a single transcript segment by index (always edits latest version).

    Connection-pool friendliness: previously this held the request's DB
    session OPEN through 3-5 sequential R2 ops (read segments + optional
    backup write + segments write + optional full-text write). Under
    concurrent v3 detail page traffic that translated to 1-2 s pool holds
    per save. Now: take a snapshot read of the transcription row,
    commit-to-release the connection, do all R2 work outside any DB
    transaction (with the segments + backup writes in parallel), then
    open a fresh transaction for the final word_count + search_vector
    update.
    """
    # Phase 1: read DB — auth + load latest transcription row, snapshot
    # the paths and version onto local vars so the ORM object can be
    # safely re-attached later.
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    tx_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    transcription = tx_result.scalar_one_or_none()
    if not transcription or not transcription.segments_path:
        raise HTTPException(404, "No transcription available for this meeting")

    segments_path = transcription.segments_path
    full_text_path = transcription.full_text_path
    transcription_id = transcription.id
    # Commit to release the read transaction → pool connection becomes
    # available for other requests while we do R2 I/O.
    await db.commit()

    # Phase 2: R2 work with NO DB transaction open.
    storage = create_storage()
    try:
        segments_data = await storage.read_json(segments_path)
    except FileNotFoundError:
        raise HTTPException(404, "Transcript segments file not found")

    if body.segment_index < 0 or body.segment_index >= len(segments_data):
        raise HTTPException(400, f"Segment index {body.segment_index} out of range (0-{len(segments_data) - 1})")

    # Backup the pristine pre-edit data on first edit. We snapshot
    # BEFORE mutating segments_data so the backup captures the original
    # text. exists()+save are serial within this coroutine but run in
    # parallel with the other writes via asyncio.gather below.
    original_path = segments_path.replace(
        "transcript_segments.json", "transcript_segments_original.json"
    )
    original_snapshot = [dict(s) for s in segments_data]  # shallow copies

    # Now mutate segments_data in memory.
    segments_data[body.segment_index]["text"] = body.text
    full_text = " ".join(seg.get("text", "") for seg in segments_data)

    async def _maybe_backup_original():
        if await storage.exists(original_path):
            return
        await storage.save_json(original_path, original_snapshot)

    write_tasks: list = [
        storage.save_json(segments_path, segments_data),
        _maybe_backup_original(),
    ]

    if full_text_path and full_text_path != segments_path:
        write_tasks.append(storage.save_text(full_text_path, full_text))
    elif full_text_path == segments_path:
        logger.warning(
            f"Meeting {meeting_id}: full_text_path equals segments_path "
            f"({segments_path}) — skipping full-text write to "
            f"avoid clobbering the segments JSON. Update the DB row."
        )

    await asyncio.gather(*write_tasks)

    # Phase 3: re-open DB transaction for the final stats update.
    from sqlalchemy import func as sa_func

    await db.execute(
        text(
            "UPDATE transcriptions "
            "SET word_count = :wc, "
            "    search_vector = to_tsvector('english', :ft) "
            "WHERE id = :tid"
        ),
        {"wc": len(full_text.split()), "ft": full_text, "tid": str(transcription_id)},
    )
    await db.commit()

    return {"status": "updated", "segment_index": body.segment_index}


@router.post("/{meeting_id}/transcript/reset")
async def reset_transcript(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset transcript to original AI-generated version."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    tx_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    transcription = tx_result.scalar_one_or_none()
    if not transcription or not transcription.segments_path:
        raise HTTPException(404, "No transcription available for this meeting")

    storage = create_storage()
    original_path = transcription.segments_path.replace(
        "transcript_segments.json", "transcript_segments_original.json"
    )

    if not await storage.exists(original_path):
        raise HTTPException(404, "No original backup exists — transcript has not been edited")

    original_data = await storage.read_json(original_path)

    # Restore original
    await storage.save_json(transcription.segments_path, original_data)

    # Delete backup so is_edited returns False
    await storage.delete(original_path)

    # Rebuild full text and search vector
    full_text = " ".join(seg.get("text", "") for seg in original_data)
    # Same path-collision guard as the edit handler — never overwrite
    # the segments JSON we just restored with the plain-text join.
    if (
        transcription.full_text_path
        and transcription.full_text_path != transcription.segments_path
    ):
        await storage.save_text(transcription.full_text_path, full_text)
    elif transcription.full_text_path == transcription.segments_path:
        logger.warning(
            f"Meeting {meeting_id}: full_text_path equals segments_path "
            f"on reset — skipping full-text write to avoid clobbering "
            f"the freshly restored JSON."
        )
    transcription.word_count = len(full_text.split())

    from sqlalchemy import func as sa_func
    transcription.search_vector = sa_func.to_tsvector("english", full_text)

    await db.commit()

    return {"status": "reset", "meeting_id": str(meeting_id)}


# --- Speaker endpoints ---


@router.get("/{meeting_id}/speakers", response_model=MeetingSpeakersResponse)
async def get_meeting_speakers(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all participants for a meeting with their speaker profiles."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    result = await db.execute(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting_id)
        .order_by(MeetingParticipant.diarization_label)
    )
    participants = result.scalars().all()

    # Batch-fetch every referenced SpeakerProfile in ONE query and look
    # up by id in memory inside the loop. The previous version fired a
    # SELECT per participant (N+1: 1 + 8-12 queries per meeting page
    # load), each running indexed but serialized, holding the DB
    # connection for the full chain. With a typical 8-speaker meeting
    # that was ~9 round-trips on the same session. One batch query +
    # dict lookup keeps the same behavior with one DB hit.
    profile_ids = {p.speaker_profile_id for p in participants if p.speaker_profile_id}
    profiles_by_id: dict[uuid.UUID, SpeakerProfile] = {}
    if profile_ids:
        prof_result = await db.execute(
            select(SpeakerProfile).where(SpeakerProfile.id.in_(profile_ids))
        )
        profiles_by_id = {prof.id: prof for prof in prof_result.scalars().all()}

    # Dedupe by speaker_profile_id — when the user maps two diarization
    # labels to the same profile via PATCH /speakers, we end up with two
    # MeetingParticipant rows pointing at the same SpeakerProfile. The
    # Speakers right-rail should render that as ONE speaker, with summed
    # talk-time / segment_count / word_count. Rows without a profile_id
    # are kept distinct (each unmapped SPEAKER_XX is its own anonymous
    # speaker until manually merged).
    items: list[ParticipantOut] = []
    grouped: dict[uuid.UUID, ParticipantOut] = {}
    for p in participants:
        profile = profiles_by_id.get(p.speaker_profile_id) if p.speaker_profile_id else None

        crm_links = (profile.crm_links if profile and profile.crm_links else None) or None
        out = ParticipantOut(
            id=p.id,
            diarization_label=p.diarization_label,
            display_name=profile.display_name if profile else p.platform_display_name,
            speaker_profile_id=p.speaker_profile_id,
            email=profile.email if profile else None,
            company=profile.company if profile else None,
            title=profile.title if profile else None,
            crm_links=crm_links,
            mapping_source=p.mapping_source,
            mapping_confidence=p.mapping_confidence,
            talk_time_seconds=p.talk_time_seconds,
            segment_count=p.segment_count,
            word_count=p.word_count,
        )
        if p.speaker_profile_id and p.speaker_profile_id in grouped:
            agg = grouped[p.speaker_profile_id]
            agg.talk_time_seconds = (agg.talk_time_seconds or 0) + (out.talk_time_seconds or 0)
            agg.segment_count = (agg.segment_count or 0) + (out.segment_count or 0)
            agg.word_count = (agg.word_count or 0) + (out.word_count or 0)
            # Prefer the row with higher mapping_confidence as the canonical id/label.
            if (out.mapping_confidence or 0) > (agg.mapping_confidence or 0):
                agg.id = out.id
                agg.diarization_label = out.diarization_label
                agg.mapping_source = out.mapping_source
                agg.mapping_confidence = out.mapping_confidence
        elif p.speaker_profile_id:
            grouped[p.speaker_profile_id] = out
            items.append(out)
        else:
            items.append(out)

    return MeetingSpeakersResponse(meeting_id=meeting_id, participants=items)


@router.patch("/{meeting_id}/speakers")
async def assign_speaker(
    meeting_id: uuid.UUID,
    body: SpeakerAssignRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign a display name to a diarization label. Creates/updates speaker profile."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)
    team_id = meeting.team_id

    # 1. Resolve or create speaker profile
    profile = None
    if body.speaker_profile_id:
        prof_result = await db.execute(
            select(SpeakerProfile).where(
                SpeakerProfile.id == body.speaker_profile_id,
                SpeakerProfile.team_id == team_id,
            )
        )
        profile = prof_result.scalar_one_or_none()
        if not profile:
            raise HTTPException(404, "Speaker profile not found")
    elif body.email:
        # Try to find by email first
        prof_result = await db.execute(
            select(SpeakerProfile).where(
                SpeakerProfile.team_id == team_id,
                SpeakerProfile.email == body.email,
            )
        )
        profile = prof_result.scalar_one_or_none()

    if not profile:
        # Create new profile
        profile = SpeakerProfile(
            team_id=team_id,
            display_name=body.speaker_name,
            email=body.email,
            company=body.company,
            title=body.title,
            source="manual",
        )
        db.add(profile)
        await db.flush()
    else:
        # Update display name if it changed
        profile.display_name = body.speaker_name
        if body.email and not profile.email:
            profile.email = body.email
        if body.company:
            profile.company = body.company
        if body.title:
            profile.title = body.title

    # 2. Find or create meeting participant for this label
    # Delete all existing rows for this label, then create one clean record
    existing_parts = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.diarization_label == body.diarization_label,
        )
    )
    for old_part in existing_parts.scalars().all():
        await db.delete(old_part)

    # Also remove bot_capture rows without a label that match the old name
    unlabeled_parts = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.diarization_label.is_(None),
        )
    )
    for unlabeled in unlabeled_parts.scalars().all():
        await db.delete(unlabeled)

    await db.flush()

    participant = MeetingParticipant(
        meeting_id=meeting_id,
        diarization_label=body.diarization_label,
        speaker_profile_id=profile.id,
        platform_display_name=body.speaker_name,
        mapping_source="manual",
        mapping_confidence=1.0,
    )
    db.add(participant)

    # 3. Update transcript segments — rename speaker label to display name
    tx_result = await db.execute(
        select(Transcription)
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.desc())
        .limit(1)
    )
    transcription = tx_result.scalar_one_or_none()

    segments_updated = 0
    segments_data = None
    # Storage object is cheap to construct (no I/O) and is referenced
    # by the parallel R2 coroutines below regardless of whether this
    # branch executes — lift it out so closures always resolve it.
    storage = create_storage()
    if transcription and transcription.segments_path:
        try:
            segments_data = await storage.read_json(transcription.segments_path)
        except FileNotFoundError:
            segments_data = None

        if segments_data:
            # Determine what name to search for in segments:
            # After first assignment, segments have the display name, not the raw label.
            # Check speaker_map and participant for the current name.
            old_names = {body.diarization_label}
            if transcription.speaker_map and body.diarization_label in transcription.speaker_map:
                old_names.add(transcription.speaker_map[body.diarization_label].get("name", ""))
            if participant and participant.platform_display_name:
                old_names.add(participant.platform_display_name)
            old_names.discard("")

            new_name = body.speaker_name
            for seg in segments_data:
                if seg.get("speaker") in old_names:
                    seg["speaker"] = new_name
                    segments_updated += 1

            # Update talk stats on participant
            participant.segment_count = segments_updated
            participant.word_count = sum(
                len(seg.get("text", "").split())
                for seg in segments_data
                if seg.get("speaker") == new_name
            )
            participant.talk_time_seconds = sum(
                (seg.get("end", 0) - seg.get("start", 0))
                for seg in segments_data
                if seg.get("speaker") == new_name
            )

    # 3.5 / 4.5 — parallelize the three independent R2 write paths:
    #   (a) segments save (must happen if we mutated segments_data above)
    #   (b) summary.json speaker-name replacement (independent of a)
    #   (c) analysis.json coaching-metrics recompute + save (independent
    #       of a/b, only depends on the in-memory mutated segments_data)
    # Previously these ran serially while holding the request's DB
    # session for ~1.5-2 s. asyncio.gather collapses wall-clock to the
    # max of the three (~300-500 ms), and the boto3 timeouts cap any
    # single op so a degraded R2 can't pin the request indefinitely.
    async def _save_segments():
        if segments_data and transcription and transcription.segments_path:
            await storage.save_json(transcription.segments_path, segments_data)

    async def _update_summary():
        if not (segments_data and transcription and transcription.segments_path):
            return
        try:
            base_dir = transcription.segments_path.rsplit("/", 1)[0]
            summary_path = f"{base_dir}/summary.json"
            if not await storage.exists(summary_path):
                return
            summary_raw = await storage.read_json(summary_path)
            replacements = {old: body.speaker_name for old in old_names if old != body.speaker_name}
            if not replacements:
                return
            from app.api.webhooks import _replace_names_in_obj
            summary_raw = _replace_names_in_obj(summary_raw, replacements)
            await storage.save_json(summary_path, summary_raw)
            logger.info(f"Meeting {meeting_id}: updated summary.json with speaker name '{body.speaker_name}'")
        except Exception as e:
            logger.warning(f"Meeting {meeting_id}: summary update failed (non-fatal): {e}")

    # 4. Rebuild speaker_map cache on transcription
    if transcription:
        # Load all participants for this meeting
        all_parts_result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id
            )
        )
        all_parts = all_parts_result.scalars().all()

        # Batch-fetch every OTHER profile's display_name in one query
        # (the just-assigned profile's name is taken from body.speaker_name
        # directly — no need to round-trip). Previously this loop fired a
        # SELECT per participant for the other-profile branch — N+1 over
        # the participants list, held inside the PATCH transaction.
        other_profile_ids = {
            p.speaker_profile_id
            for p in all_parts
            if p.diarization_label
            and p.speaker_profile_id
            and p.speaker_profile_id != profile.id
        }
        other_names_by_id: dict[uuid.UUID, str] = {}
        if other_profile_ids:
            others = await db.execute(
                select(SpeakerProfile.id, SpeakerProfile.display_name)
                .where(SpeakerProfile.id.in_(other_profile_ids))
            )
            other_names_by_id = {row.id: row.display_name for row in others}

        speaker_map = {}
        for p in all_parts:
            if p.diarization_label and p.speaker_profile_id:
                if p.speaker_profile_id == profile.id:
                    name = body.speaker_name
                else:
                    name = (
                        other_names_by_id.get(p.speaker_profile_id)
                        or p.diarization_label
                    )
                speaker_map[p.diarization_label] = {
                    "name": name,
                    "profile_id": str(p.speaker_profile_id),
                }

        transcription.speaker_map = speaker_map

    # 4.5 Recompute coaching metrics on the renamed segments. When the user
    # merges two diarization labels into the same display name (e.g. both
    # SPEAKER_00 + SPEAKER_01 → "Sathish Kumar"), the stored coaching
    # metrics from T3 still treat them as two distinct speakers — talk
    # ratio, monologue, interruptions all get computed wrong. Recompute
    # from the updated segments here so the right-rail metrics flip
    # immediately on dropdown change.
    async def _recompute_coaching():
        if not (segments_data and meeting.analysis_path):
            return
        try:
            from app.services.coaching_metrics import compute_coaching_metrics
            total_duration = float(meeting.duration_seconds or 0)
            if total_duration <= 0 and segments_data:
                total_duration = max(
                    (seg.get("end", 0.0) or 0.0) for seg in segments_data
                )
            new_coaching = compute_coaching_metrics(
                segments_data, total_duration, rep_speaker=None,
            )
            try:
                analysis_doc = await storage.read_json(meeting.analysis_path)
            except FileNotFoundError:
                analysis_doc = {}
            analysis_doc["coaching_metrics"] = new_coaching
            await storage.save_json(meeting.analysis_path, analysis_doc)
            logger.info(
                f"Meeting {meeting_id}: recomputed coaching metrics after speaker assign "
                f"(talk_ratio={new_coaching.get('talk_ratio', {}).get('rep_percentage')}%)"
            )
        except Exception as e:
            logger.warning(
                f"Meeting {meeting_id}: coaching recompute failed (non-fatal): {e}"
            )

    # Run all three R2 paths in parallel. return_exceptions keeps a
    # failure in any one path from cancelling the others — segments save
    # is the only one we'd consider critical, and storage.save_json
    # raises on real I/O failures anyway.
    await asyncio.gather(
        _save_segments(),
        _update_summary(),
        _recompute_coaching(),
        return_exceptions=True,
    )

    await db.commit()

    # Backfill voice embedding for this speaker (best-effort)
    # First try: read pre-extracted embedding from embeddings.json in R2
    # Fallback: request extraction from TranscriptionBase API
    if profile and transcription and transcription.segments_path:
        try:
            from app.services.voice_embeddings import (
                MIN_DURATION_SECONDS,
                request_single_embedding,
                store_embedding_sample,
            )

            storage = create_storage()
            embedding = None
            emb_duration = 0.0

            # Try pre-extracted embeddings.json first
            base_dir = transcription.segments_path.rsplit("/", 1)[0]
            embeddings_path = f"{base_dir}/embeddings.json"
            try:
                if await storage.exists(embeddings_path):
                    emb_data = await storage.read_json(embeddings_path)
                    emb_entry = emb_data.get(body.diarization_label)
                    if emb_entry and emb_entry.get("embedding"):
                        embedding = emb_entry["embedding"]
                        emb_duration = emb_entry.get("duration", 0)
                        logger.info(f"Meeting {meeting.id}: found pre-extracted embedding for {body.diarization_label}")
            except Exception:
                pass

            # Fallback: request from TranscriptionBase
            if not embedding and meeting.recording_path and segments_data:
                speaker_segs = [
                    seg for seg in segments_data
                    if seg.get("speaker") == body.speaker_name
                ]
                if speaker_segs:
                    best_seg = max(speaker_segs, key=lambda s: s.get("end", 0) - s.get("start", 0))
                    seg_start = best_seg.get("start", 0)
                    seg_end = best_seg.get("end", 0)
                    emb_duration = seg_end - seg_start
                    if emb_duration >= MIN_DURATION_SECONDS:
                        embedding = await request_single_embedding(
                            meeting.recording_path, seg_start, seg_end
                        )

            if embedding:
                await store_embedding_sample(
                    db, profile.id, embedding, emb_duration,
                    meeting_id=meeting.id,
                    diarization_label=body.diarization_label,
                    source="manual",
                )
                await db.commit()
                logger.info(f"Meeting {meeting.id}: voice embedding stored for {body.speaker_name} ({emb_duration:.1f}s)")
        except Exception as e:
            logger.error(f"Voice embedding backfill failed for meeting {meeting.id}: {e}")

    return {
        "status": "updated",
        "diarization_label": body.diarization_label,
        "speaker_name": body.speaker_name,
        "speaker_profile_id": str(profile.id),
        "segments_updated": segments_updated,
    }


# --- Team speaker profiles ---


speaker_router = APIRouter(prefix="/api/speakers", tags=["speakers"])


@speaker_router.get("", response_model=list[SpeakerProfileOut])
async def list_team_speakers(
    team_id: uuid.UUID = Query(..., description="Team ID"),
    q: Optional[str] = Query(None, description="Search by name"),
    meeting_id: Optional[uuid.UUID] = Query(None, description="Include LLM-inferred names from this meeting"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List speaker profiles for a team (for dropdown autocomplete).

    When meeting_id is provided, also includes LLM-inferred speaker names
    from that meeting as suggestions (deduplicated against existing profiles).
    """
    # Verify membership
    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(403, "Not a member of this team")

    query = select(SpeakerProfile).where(SpeakerProfile.team_id == team_id)
    if q:
        query = query.where(SpeakerProfile.display_name.ilike(f"%{q}%"))
    query = query.order_by(SpeakerProfile.display_name).limit(50)

    result = await db.execute(query)
    profiles = result.scalars().all()

    results = [
        SpeakerProfileOut(
            id=p.id,
            display_name=p.display_name,
            email=p.email,
            company=p.company,
            title=p.title,
            meeting_count=p.meeting_count,
        )
        for p in profiles
    ]

    if meeting_id:
        llm_parts_result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.mapping_source == "llm_inference",
                MeetingParticipant.platform_display_name.isnot(None),
            )
        )
        existing_names = {r.display_name.lower() for r in results}
        for lp in llm_parts_result.scalars().all():
            if lp.platform_display_name.lower() not in existing_names:
                results.append(SpeakerProfileOut(
                    id=lp.speaker_profile_id or lp.id,
                    display_name=lp.platform_display_name,
                    email=None,
                    company=None,
                    title=None,
                    meeting_count=0,
                ))

    return results


@speaker_router.post("/{source_id}/merge-into/{target_id}")
async def merge_speaker_profile(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    dry_run: bool = Query(False, description="Report counts without writing"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Merge `source_id` profile INTO `target_id`. Source is removed.

    Reassigns every MeetingParticipant + VoiceEmbeddingSample from
    source → target, sums talk-time / meeting-count, lifts target's
    null identity fields from source where available, then deletes
    source. Both profiles must be in the same team, and the caller
    must be an admin/owner of that team.
    """
    from app.services.profile_merge import merge_profiles, MergeError

    # Verify both profiles + caller is team admin/owner
    src_q = await db.execute(select(SpeakerProfile).where(SpeakerProfile.id == source_id))
    src = src_q.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Source profile not found")

    member_result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == src.team_id,
            TeamMember.user_id == uuid.UUID(user.user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = member_result.scalar_one_or_none()
    if not member or member.role not in ("admin", "owner"):
        raise HTTPException(403, "Requires admin or owner role")

    try:
        report = await merge_profiles(db, source_id, target_id, dry_run=dry_run)
    except MergeError as e:
        raise HTTPException(400, str(e))

    if not dry_run:
        await db.commit()
    return report


# --- Coaching endpoints ---


@router.get("/{meeting_id}/coaching", response_model=CoachingResponse)
async def get_coaching(
    meeting_id: uuid.UUID,
    version: Optional[int] = Query(None, description="Transcription version (default: latest)"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read coaching metrics from analysis.json."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Resolve versioned analysis path
    analysis_path = await _resolve_analysis_path(db, meeting_id, version)
    analysis = await _load_analysis(meeting, analysis_path)

    coaching_metrics = analysis.get("coaching_metrics", {})

    return CoachingResponse(
        meeting_id=meeting_id,
        coaching_metrics=coaching_metrics,
    )


# --- Intelligence endpoints ---


@router.get("/{meeting_id}/intelligence", response_model=IntelligenceResponse)
async def get_intelligence(
    meeting_id: uuid.UUID,
    version: Optional[int] = Query(None, description="Transcription version (default: latest)"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read intelligence data from analysis.json."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    analysis_path = await _resolve_analysis_path(db, meeting_id, version)
    analysis = await _load_analysis(meeting, analysis_path)

    intelligence = analysis.get("intelligence", {})

    return IntelligenceResponse(
        meeting_id=meeting_id,
        intelligence=intelligence,
    )


# --- Memoir endpoints ---


@router.get("/{meeting_id}/memoir", response_model=MemoirResponse)
async def get_memoir(
    meeting_id: uuid.UUID,
    version: Optional[int] = Query(None, description="Transcription version (default: latest)"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the memoir section from analysis.json. Defaults to latest version."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    analysis_path = await _resolve_analysis_path(db, meeting_id, version)

    # Pre-T3 window: no analysis.json yet (P10 LLM hasn't run). Return an
    # empty MemoirResponse instead of impersonating P5 content as P10. The
    # v3 detail page renders the P5 fast summary in its own dedicated
    # source-labeled cards (TL;DR, P5 Action Items, P5 Decisions,
    # P5 Open Questions, P5 Discussion Threads); echoing the same content
    # back through /memoir made it render again under "P10 Narrative
    # Summary", "P10 Action Items", "P10 Next Steps" placeholders, which
    # is misleading because P10 hasn't actually run yet.
    if not analysis_path and version is None:
        return MemoirResponse(
            meeting_id=meeting_id,
            narrative_summary=None,
            discovery_fields=None,
            action_items=None,
            signals=None,
            next_steps=None,
            call_metadata=None,
            disclaimer=None,
            overall_confidence_score=None,
            is_edited=False,
        )

    analysis = await _load_analysis(meeting, analysis_path)

    # Intelligence data may be stored under "intelligence" (from processor) or "memoir" (legacy)
    # Use explicit emptiness check — empty dict {} is truthy but has no data
    raw_intelligence = analysis.get("intelligence")
    raw_memoir = analysis.get("memoir")
    memoir = (raw_intelligence if raw_intelligence else None) or (raw_memoir if raw_memoir else None) or {}

    # Intentionally NO P5 fallback here. If analysis.json exists but memoir
    # is empty, return null fields — the v3 detail page renders the P5
    # fast summary in its own dedicated source-labeled cards, so impersonating
    # P5 content as P10 would render the same data twice under different
    # labels and confuse the user about which prompt produced what.

    # Transform memoir fields using shared helpers
    discovery_fields = transform_discovery_fields(memoir.get("discovery_fields") or {})
    action_items = transform_action_items(memoir.get("action_items") or {})
    signals = transform_signals(memoir.get("signals") or {})
    disclaimer = transform_disclaimer(memoir.get("disclaimer"))

    # Check if memoir has been edited (backup file exists)
    is_edited = False
    if analysis_path:
        storage = create_storage()
        original_path = analysis_path.replace("analysis.json", "analysis_original.json")
        is_edited = await storage.exists(original_path)

    return MemoirResponse(
        meeting_id=meeting_id,
        narrative_summary=memoir.get("narrative_summary"),
        discovery_fields=discovery_fields,
        action_items=action_items,
        signals=signals,
        next_steps=memoir.get("next_steps"),
        call_metadata=memoir.get("call_metadata"),
        disclaimer=disclaimer,
        overall_confidence_score=memoir.get("overall_confidence_score"),
        is_edited=is_edited,
    )


@router.patch("/{meeting_id}/memoir/fields")
async def edit_memoir_fields(
    meeting_id: uuid.UUID,
    body: FieldEditRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a CRM field in the memoir's discovery_fields (latest version)."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Resolve latest analysis path
    analysis_path = await _resolve_analysis_path(db, meeting_id, None)
    if not analysis_path:
        analysis_path = meeting.analysis_path
    if not analysis_path:
        raise HTTPException(404, "No analysis available for this meeting")

    storage = create_storage()
    try:
        analysis = await storage.read_json(analysis_path)
    except FileNotFoundError:
        raise HTTPException(404, "Analysis file not found")

    # Intelligence data may be under "intelligence" or "memoir" (legacy)
    memoir_key = "intelligence" if "intelligence" in analysis else "memoir"
    memoir = analysis.get(memoir_key, {})
    discovery_fields = memoir.get("discovery_fields", {})

    # Update the field
    if isinstance(discovery_fields.get(body.field_name), dict):
        discovery_fields[body.field_name]["value"] = body.value
        if body.confidence:
            discovery_fields[body.field_name]["confidence"] = body.confidence
    else:
        discovery_fields[body.field_name] = {
            "value": body.value,
            "confidence": body.confidence or "high",
        }

    memoir["discovery_fields"] = discovery_fields
    analysis[memoir_key] = memoir

    await storage.save_json(analysis_path, analysis)

    return {"status": "updated", "field_name": body.field_name, "value": body.value}


@router.post("/{meeting_id}/memoir/push-crm")
async def push_crm(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve and push memoir data to CRM (latest version)."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Get latest memoir summary
    ms_result = await db.execute(
        select(MemoirSummary)
        .where(MemoirSummary.meeting_id == meeting_id)
        .order_by(MemoirSummary.version.desc())
        .limit(1)
    )
    memoir_summary = ms_result.scalar_one_or_none()
    if not memoir_summary:
        raise HTTPException(404, "No memoir summary for this meeting")

    if memoir_summary.crm_push_status == "pushed":
        raise HTTPException(400, "CRM data already pushed for this meeting")

    # Update status
    memoir_summary.crm_push_status = "pushed"
    memoir_summary.crm_pushed_at = datetime.now(timezone.utc)
    await db.commit()

    # Publish crm_push_requested event via Redis
    try:
        publisher = RedisEventPublisher(settings.REDIS_URL)
        await publisher.publish(
            "crm_push_requested",
            {
                "meeting_id": str(meeting_id),
                "team_id": str(meeting.team_id),
                "analysis_path": meeting.analysis_path,
                "pushed_by": user.user_id,
            },
        )
        await publisher.close()
    except Exception as e:
        logger.error(f"Failed to publish crm_push_requested event: {e}")

    return {
        "status": "pushed",
        "meeting_id": str(meeting_id),
        "crm_pushed_at": memoir_summary.crm_pushed_at.isoformat(),
    }


@router.post("/{meeting_id}/memoir/skip")
async def skip_crm(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skip CRM push for this meeting (latest version)."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    ms_result = await db.execute(
        select(MemoirSummary)
        .where(MemoirSummary.meeting_id == meeting_id)
        .order_by(MemoirSummary.version.desc())
        .limit(1)
    )
    memoir_summary = ms_result.scalar_one_or_none()
    if not memoir_summary:
        raise HTTPException(404, "No memoir summary for this meeting")

    memoir_summary.crm_push_status = "skipped"
    await db.commit()

    return {"status": "skipped", "meeting_id": str(meeting_id)}


@router.patch("/{meeting_id}/memoir")
async def edit_memoir(
    meeting_id: uuid.UUID,
    body: MemoirEditRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit memoir fields (narrative summary, action items, signals, next steps)."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    analysis_path = await _resolve_analysis_path(db, meeting_id, None)
    if not analysis_path:
        analysis_path = meeting.analysis_path
    if not analysis_path:
        raise HTTPException(404, "No analysis available for this meeting")

    storage = create_storage()
    try:
        analysis = await storage.read_json(analysis_path)
    except FileNotFoundError:
        raise HTTPException(404, "Analysis file not found")

    # Backup original on first edit
    original_path = analysis_path.replace("analysis.json", "analysis_original.json")
    if not await storage.exists(original_path):
        await storage.save_json(original_path, analysis)

    # Intelligence data may be under "intelligence" or "memoir" (legacy)
    memoir_key = "intelligence" if "intelligence" in analysis else "memoir"
    memoir_data = analysis.get(memoir_key, {})

    # Update requested fields
    if body.narrative_summary is not None:
        memoir_data["narrative_summary"] = body.narrative_summary
    if body.action_items is not None:
        memoir_data["action_items"] = body.action_items
    if body.signals is not None:
        memoir_data["signals"] = body.signals
    if body.next_steps is not None:
        memoir_data["next_steps"] = body.next_steps

    analysis[memoir_key] = memoir_data
    await storage.save_json(analysis_path, analysis)

    return {"status": "updated", "meeting_id": str(meeting_id)}


@router.post("/{meeting_id}/memoir/reset")
async def reset_memoir(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset memoir to original AI-generated version."""
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    analysis_path = await _resolve_analysis_path(db, meeting_id, None)
    if not analysis_path:
        analysis_path = meeting.analysis_path
    if not analysis_path:
        raise HTTPException(404, "No analysis available for this meeting")

    storage = create_storage()
    original_path = analysis_path.replace("analysis.json", "analysis_original.json")

    if not await storage.exists(original_path):
        raise HTTPException(404, "No original backup exists — memoir has not been edited")

    original_data = await storage.read_json(original_path)

    # Restore original
    await storage.save_json(analysis_path, original_data)

    # Delete backup so is_edited returns False
    await storage.delete(original_path)

    return {"status": "reset", "meeting_id": str(meeting_id)}


# --- Version & Recording endpoints ---


async def _resolve_analysis_path(
    db: AsyncSession, meeting_id: uuid.UUID, version: int | None
) -> str | None:
    """Resolve the analysis.json path for a given version.

    For versioned transcriptions (v2+), the analysis path is derived from the
    transcription's segments_path directory. For legacy (v1 with top-level files),
    it falls back to the meeting.analysis_path.
    """
    query = select(Transcription.segments_path, Transcription.version).where(
        Transcription.meeting_id == meeting_id
    )
    if version is not None:
        query = query.where(Transcription.version == version)
    else:
        query = query.order_by(Transcription.version.desc()).limit(1)

    result = await db.execute(query)
    row = result.one_or_none()
    if not row or not row[0]:
        return None

    # Derive analysis.json path from segments path directory
    segments_path = row[0]
    # e.g. "meetings/{id}/v2/transcript_segments.json" -> "meetings/{id}/v2/analysis.json"
    # e.g. "meetings/{id}/transcript_segments.json" -> "meetings/{id}/analysis.json"
    base_dir = segments_path.rsplit("/", 1)[0]
    return f"{base_dir}/analysis.json"


@router.get("/{meeting_id}/versions")
async def get_versions(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return list of transcription versions for a meeting."""
    await _get_meeting_with_access(db, meeting_id, user.user_id)

    result = await db.execute(
        select(
            Transcription.version,
            Transcription.created_at,
            Transcription.word_count,
            Transcription.speaker_count,
            Transcription.recording_source,
        )
        .where(Transcription.meeting_id == meeting_id)
        .order_by(Transcription.version.asc())
    )
    rows = result.all()

    versions = [
        {
            "version": row[0],
            "created_at": row[1].isoformat() if row[1] else None,
            "word_count": row[2] or 0,
            "speaker_count": row[3] or 0,
            "recording_source": row[4] or "bot",
        }
        for row in rows
    ]

    return {"versions": versions}


@router.get("/{meeting_id}/recording-url")
async def get_recording_url(
    meeting_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return a playable URL for the meeting recording.

    S3: returns a presigned URL (1 hour expiry).
    Local: returns the direct file-serving endpoint path.
    """
    user = get_current_user(request)
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    if not meeting.recording_path:
        raise HTTPException(404, "No recording available for this meeting")

    if settings.STORAGE_BACKEND == "s3":
        storage = create_storage()
        url = await storage.get_presigned_url(meeting.recording_path, expires_in=3600)
        return {"url": url}

    # Local: return path to the file-serving endpoint
    return {"url": f"/api/meetings/{meeting_id}/recording"}


@router.get("/{meeting_id}/raw-asr")
async def get_raw_asr(
    meeting_id: uuid.UUID,
    request: Request,
    version: Optional[int] = Query(None, description="Transcription version (default: latest)"),
    db: AsyncSession = Depends(get_db),
):
    """Return raw ASR output (before post-processing) for debugging/comparison.

    Mirrors the version-handling pattern used by /transcription: caller can
    pass ?version=N for a specific run, or omit it to get the latest. Used
    to look up the right v{N}/ subpath in R2 — previously hardcoded to v1,
    which broke for every retranscribed meeting.
    """
    user = get_current_user(request)
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    # Resolve target version → latest Transcription row when caller didn't pin one
    target_version = version
    if target_version is None:
        tx_result = await db.execute(
            select(Transcription.version)
            .where(Transcription.meeting_id == meeting_id)
            .order_by(Transcription.version.desc())
            .limit(1)
        )
        target_version = tx_result.scalar_one_or_none() or 1

    storage = create_storage()
    # Primary: polished raw_asr_segments.json from the Type 5 ASR postprocess output
    path = f"meetings/{meeting_id}/v{target_version}/raw_asr_segments.json"
    if await storage.exists(path):
        return await storage.read_json(path)

    # Fast-P5 fallback: between Type 7 done and Type 5 done, the polished
    # raw_asr_segments.json may not exist yet but Type 7 wrote
    # transcript_segments_fast.json with the same per-segment ASR shape.
    # Only fall back for the latest version (older versions should have
    # the polished file by definition).
    if version is None:
        fast_key = f"meetings/{meeting_id}/v{target_version}/transcript_segments_fast.json"
        if await storage.exists(fast_key):
            return await storage.read_json(fast_key)

    raise HTTPException(404, f"Raw ASR output not available for meeting v{target_version}")


@router.get("/{meeting_id}/audio-urls")
async def get_audio_urls(
    meeting_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return presigned URLs for all available audio versions (raw, preprocessed, enhanced)."""
    user = get_current_user(request)
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    storage = create_storage()
    base = f"meetings/{meeting_id}"
    urls = {}
    for key, path in [
        ("raw", meeting.recording_path or f"{base}/recording.wav"),
        ("preprocessed", f"{base}/v1/preprocessed_audio.wav"),
        ("enhanced", f"{base}/v1/enhanced_audio.wav"),
    ]:
        try:
            if await storage.exists(path):
                urls[key] = await storage.get_presigned_url(path, expires_in=3600)
        except Exception:
            pass
    return urls


@router.get("/{meeting_id}/recording/download-url")
async def get_recording_download_url(
    meeting_id: uuid.UUID,
    version: str = Query("preprocessed", description="raw | preprocessed | enhanced"),
    request: Request = None,  # type: ignore[assignment]
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned URL whose response carries
    ``Content-Disposition: attachment; filename="…"`` so the browser
    triggers a file download instead of streaming the audio inline.

    Falls through to the first available version when the requested
    one doesn't exist.
    """
    user = get_current_user(request)
    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    storage = create_storage()
    base = f"meetings/{meeting_id}"
    candidates = [
        ("preprocessed", f"{base}/v1/preprocessed_audio.wav"),
        ("raw", meeting.recording_path or f"{base}/recording.wav"),
        ("enhanced", f"{base}/v1/enhanced_audio.wav"),
    ]
    # Reorder so the user's preference is tried first.
    candidates.sort(key=lambda c: 0 if c[0] == version else 1)

    safe_title = re.sub(r"[^a-zA-Z0-9._-]", "_", meeting.title or "recording")
    for ver_name, path in candidates:
        try:
            if await storage.exists(path):
                ext = path.rsplit(".", 1)[-1] if "." in path else "wav"
                filename = f"{safe_title}.{ext}"
                url = await storage.get_presigned_url(
                    path,
                    expires_in=3600,
                    download_filename=filename,
                )
                return {"url": url, "filename": filename, "version": ver_name}
        except Exception:
            continue
    raise HTTPException(404, "No recording available for this meeting")


@router.get("/{meeting_id}/recording")
async def get_recording(
    meeting_id: uuid.UUID,
    request: Request,
    token: Optional[str] = Query(None, description="JWT token (for audio element src)"),
    db: AsyncSession = Depends(get_db),
):
    """Stream or redirect to the meeting recording audio file.

    Accepts auth via Authorization header or ?token= query param (needed for
    HTML5 <audio> elements which can't set custom headers).
    """
    if token:
        jwt_settings = getattr(request.app.state, "settings", None)
        public_key_path = getattr(jwt_settings, "JWT_PUBLIC_KEY_PATH", "./keys/public.pem")
        payload = decode_jwt(token, public_key_path)
        if not payload:
            raise HTTPException(401, "Invalid or expired token")
        user = UserContext(
            user_id=payload["sub"],
            email=payload.get("email", ""),
            name=payload.get("name", ""),
            team_id=payload.get("team_id"),
            team_role=payload.get("team_role"),
        )
    else:
        user = get_current_user(request)

    meeting = await _get_meeting_with_access(db, meeting_id, user.user_id)

    if not meeting.recording_path:
        raise HTTPException(404, "No recording available for this meeting")

    storage = create_storage()

    if settings.STORAGE_BACKEND == "s3":
        # S3: redirect to presigned URL
        url = await storage.get_presigned_url(meeting.recording_path, expires_in=3600)
        return RedirectResponse(url=url)

    # Local storage: serve file directly
    data_dir = Path(settings.DATA_DIR).resolve()
    file_path = data_dir / meeting.recording_path
    if not file_path.exists():
        raise HTTPException(404, "Recording file not found")

    return FileResponse(
        path=str(file_path),
        media_type="audio/wav",
        filename=f"recording-{meeting_id}.wav",
    )
