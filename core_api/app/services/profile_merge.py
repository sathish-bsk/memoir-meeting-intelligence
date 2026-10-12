"""Speaker profile merge — fold one duplicate profile into another.

Used by:
  - One-shot script (scripts/merge_speaker_profiles.py) for cleaning up
    known duplicate pairs identified via voice-embedding comparison.
  - POST /api/speakers/profiles/{id}/merge-into/{target_id} endpoint
    for manual merges from the speaker profile UI.

The merge is one-way: source → target. The target keeps its identity
(id, display_name, email, crm_links). The source's meetings, voice
samples, and stats roll into the target, then the source row is deleted.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.speaker import (
    SpeakerProfile,
    MeetingParticipant,
    VoiceEmbeddingSample,
)

logger = logging.getLogger(__name__)


class MergeError(Exception):
    pass


async def merge_profiles(
    db: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    dry_run: bool = False,
) -> dict:
    """Merge source profile into target. Returns counts.

    Operations (all in one transaction):
      1. Reassign every MeetingParticipant.speaker_profile_id from source → target
      2. Reassign every VoiceEmbeddingSample.speaker_profile_id from source → target
      3. Sum source.meeting_count and source.total_talk_seconds into target
      4. min(first_seen_at), max(last_seen_at)
      5. Preserve target.email/display_name/crm_links — but lift them from
         source if target's field is null and source has a value
      6. Delete source profile row (CASCADE drops nothing useful — voice
         samples already migrated, participants already migrated)

    Raises MergeError when source and target are in different teams, or
    either id is missing.

    dry_run=True reports the counts that WOULD change without writing.
    """
    if source_id == target_id:
        raise MergeError("source and target must differ")

    src_q = await db.execute(select(SpeakerProfile).where(SpeakerProfile.id == source_id))
    src = src_q.scalar_one_or_none()
    if not src:
        raise MergeError(f"source profile {source_id} not found")

    tgt_q = await db.execute(select(SpeakerProfile).where(SpeakerProfile.id == target_id))
    tgt = tgt_q.scalar_one_or_none()
    if not tgt:
        raise MergeError(f"target profile {target_id} not found")

    if src.team_id != tgt.team_id:
        raise MergeError(
            f"team mismatch: source team={src.team_id} target team={tgt.team_id}"
        )

    # Count what we're about to move
    parts_q = await db.execute(
        select(MeetingParticipant).where(MeetingParticipant.speaker_profile_id == source_id)
    )
    moving_participants = parts_q.scalars().all()
    parts_count = len(moving_participants)

    samples_q = await db.execute(
        select(VoiceEmbeddingSample).where(VoiceEmbeddingSample.speaker_profile_id == source_id)
    )
    moving_samples = samples_q.scalars().all()
    samples_count = len(moving_samples)

    report = {
        "source_id": str(source_id),
        "source_name": src.display_name,
        "source_email": src.email,
        "source_team_id": str(src.team_id),
        "target_id": str(target_id),
        "target_name": tgt.display_name,
        "target_email": tgt.email,
        "participants_to_move": parts_count,
        "samples_to_move": samples_count,
        "talk_seconds_to_add": float(src.total_talk_seconds or 0),
        "meeting_count_to_add": int(src.meeting_count or 0),
        "dry_run": dry_run,
    }

    if dry_run:
        return report

    # Reassign meeting_participants
    await db.execute(
        update(MeetingParticipant)
        .where(MeetingParticipant.speaker_profile_id == source_id)
        .values(speaker_profile_id=target_id)
    )

    # Reassign voice samples
    await db.execute(
        update(VoiceEmbeddingSample)
        .where(VoiceEmbeddingSample.speaker_profile_id == source_id)
        .values(speaker_profile_id=target_id)
    )

    # Sum stats
    tgt.meeting_count = (tgt.meeting_count or 0) + (src.meeting_count or 0)
    tgt.total_talk_seconds = (tgt.total_talk_seconds or 0) + (src.total_talk_seconds or 0)
    if src.first_seen_at and (not tgt.first_seen_at or src.first_seen_at < tgt.first_seen_at):
        tgt.first_seen_at = src.first_seen_at
    if src.last_seen_at and (not tgt.last_seen_at or src.last_seen_at > tgt.last_seen_at):
        tgt.last_seen_at = src.last_seen_at

    # Lift identity fields if target's are missing
    if not tgt.email and src.email:
        tgt.email = src.email
    if not tgt.phone and src.phone:
        tgt.phone = src.phone
    if not tgt.company and src.company:
        tgt.company = src.company
    if not tgt.title and src.title:
        tgt.title = src.title
    if not tgt.internal_user_id and src.internal_user_id:
        tgt.internal_user_id = src.internal_user_id

    # Merge crm_links (target wins on conflict)
    if isinstance(src.crm_links, dict) and src.crm_links:
        merged_links = dict(src.crm_links)
        if isinstance(tgt.crm_links, dict):
            merged_links.update(tgt.crm_links)
        tgt.crm_links = merged_links

    tgt.updated_at = datetime.now(timezone.utc)

    # Delete source
    await db.delete(src)
    await db.flush()

    logger.info(
        f"Merged profile {source_id} ({src.display_name}) → {target_id} "
        f"({tgt.display_name}): {parts_count} participants, {samples_count} samples moved"
    )
    return report
