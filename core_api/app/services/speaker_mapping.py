"""
Post-meeting speaker mapping pipeline.

After transcription completes, this service:
1. Loads diarization labels from transcript segments
2. Loads bot-captured participant names (MeetingParticipant rows)
3. Loads calendar attendees (if available)
4. Uses fuzzy name matching to map labels to people
5. Creates/updates SpeakerProfile and MeetingParticipant records
6. Builds the speaker_map cache on the Transcription row
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import Meeting
from app.models.speaker import MeetingParticipant, SpeakerProfile
from app.models.transcription import Transcription

logger = logging.getLogger(__name__)


# Confidence weight per candidate-name source. Higher = more trustworthy
# at fuzzy-dedup tie-break time. Caption attribution arrives via the
# overlap-matrix path (not this candidate list) and gets the highest
# weight in that path directly — kept here for completeness.
# bot_capture and web_capture both come from real-time DOM observers
# during the meeting; they share a weight. Calendar attendees are a
# pre-meeting source — sometimes accurate, sometimes stale.
_SOURCE_WEIGHT: dict[str, float] = {
    "caption": 0.95,
    "voice_match": 0.87,
    "active_speaker_overlap": 0.85,
    "voice_embedding": 0.87,
    "bot_capture": 0.65,
    "web_capture": 0.65,
    "calendar": 0.60,
    "llm_inferred": 0.40,
    "unidentified": 0.0,
}


def is_internal_participant(speaker_profile, team) -> bool:
    """Classify a participant as internal (same org) or external.

    Two checks:
    1. speaker_profile.internal_user_id is set (linked to a Memoir user account)
    2. speaker_profile.email domain matches team.domain
    """
    if speaker_profile.internal_user_id:
        return True
    if speaker_profile.email and team.domain:
        try:
            email_domain = speaker_profile.email.split("@")[1].lower()
            if email_domain == team.domain.lower():
                return True
        except (IndexError, AttributeError):
            pass
    return False


def _normalize_name(name: str) -> str:
    """Lowercase, strip whitespace, remove special chars for comparison."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _fuzzy_name_match(name_a: str, name_b: str) -> float:
    """Compute a simple fuzzy match score between two names.

    Returns 0.0 to 1.0 where 1.0 = exact match.
    """
    a = _normalize_name(name_a)
    b = _normalize_name(name_b)

    if not a or not b:
        return 0.0

    # Exact match
    if a == b:
        return 1.0

    # Token overlap (handles "John Smith" vs "Smith, John")
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b)
        total = max(len(tokens_a), len(tokens_b))
        token_score = overlap / total
        if token_score >= 0.5:
            return 0.7 + (token_score * 0.3)

    # First name + initial match ("John S" vs "John Smith")
    parts_a = a.split()
    parts_b = b.split()
    if len(parts_a) >= 1 and len(parts_b) >= 1:
        if parts_a[0] == parts_b[0]:
            # Same first name
            if len(parts_a) == 1 or len(parts_b) == 1:
                return 0.6  # First name only match
            if parts_a[1][0] == parts_b[1][0]:
                return 0.7  # First name + initial

    # Simple Levenshtein-like check for short names
    if abs(len(a) - len(b)) <= 2 and len(a) <= 20:
        common = sum(1 for ca, cb in zip(a, b) if ca == cb)
        ratio = common / max(len(a), len(b))
        if ratio >= 0.75:
            return 0.5 + (ratio * 0.2)

    return 0.0


async def run_speaker_mapping(
    db: AsyncSession,
    meeting: Meeting,
    transcription: Transcription,
    segments: list[dict],
    detected_introductions: list[dict] | None = None,
    speaker_name_map: dict[str, str] | None = None,
    embeddings_data: dict | None = None,
    llm_inferred_names: dict[str, str] | None = None,
) -> dict:
    """Run the speaker mapping pipeline for a completed meeting.

    Returns the speaker_map dict: {diarization_label: {name, profile_id}}.
    """
    team_id = meeting.team_id
    meeting_id = meeting.id

    # 1. Collect unique diarization labels and their talk stats
    label_stats: dict[str, dict] = {}
    for seg in segments:
        speaker = seg.get("speaker")
        if not speaker:
            continue
        if speaker not in label_stats:
            label_stats[speaker] = {"segment_count": 0, "word_count": 0, "talk_seconds": 0.0}
        stats = label_stats[speaker]
        stats["segment_count"] += 1
        stats["word_count"] += len(seg.get("text", "").split())
        stats["talk_seconds"] += seg.get("end", 0) - seg.get("start", 0)

    if not label_stats:
        return {}

    # Resolve "UNKNOWN" fallback labels (issued by diarize._find_speaker_changes
    # when a Whisper ASR segment lands in a pyannote-coverage gap). These are
    # usually NOT a new speaker — pyannote just missed a brief burst from an
    # existing speaker. So fold each UNKNOWN segment into the nearest pyannote
    # SPEAKER_NN by time-proximity, rather than promoting it to a fake new
    # speaker. Only if there are no SPEAKER_* labels at all do we synthesize a
    # SPEAKER_00 fallback (pathological case — should never happen if T6 ran).
    if "UNKNOWN" in label_stats:
        speaker_labels = [lbl for lbl in label_stats if lbl.startswith("SPEAKER_")]
        if speaker_labels:
            # Build a quick {label: (mid_time, ...)} map from segments so we can
            # find the nearest by time. For each UNKNOWN segment, pick the
            # SPEAKER_NN whose midpoint is closest.
            label_midpoints: dict[str, list[float]] = {lbl: [] for lbl in speaker_labels}
            for seg in segments:
                sp = seg.get("speaker")
                if sp in label_midpoints:
                    label_midpoints[sp].append((seg.get("start", 0) + seg.get("end", 0)) / 2)
            for seg in segments:
                if seg.get("speaker") != "UNKNOWN":
                    continue
                seg_mid = (seg.get("start", 0) + seg.get("end", 0)) / 2
                best_label, best_dist = speaker_labels[0], float("inf")
                for lbl, mids in label_midpoints.items():
                    if not mids:
                        continue
                    d = min(abs(seg_mid - m) for m in mids)
                    if d < best_dist:
                        best_dist = d
                        best_label = lbl
                seg["speaker"] = best_label
            # Recompute stats: drop UNKNOWN, fold into nearest assigned label.
            unknown_stats = label_stats.pop("UNKNOWN")
            # The above per-segment loop already rewrote keys; recompute stats
            # from the rewritten segments to keep counts accurate.
            for lbl in speaker_labels:
                label_stats[lbl] = {"segment_count": 0, "word_count": 0, "talk_seconds": 0.0}
            for seg in segments:
                sp = seg.get("speaker")
                if sp not in label_stats:
                    continue
                s = label_stats[sp]
                s["segment_count"] += 1
                s["word_count"] += len(seg.get("text", "").split())
                s["talk_seconds"] += seg.get("end", 0) - seg.get("start", 0)
            logger.info(
                f"Meeting {meeting_id}: folded UNKNOWN gap segments into nearest "
                f"pyannote labels (was {unknown_stats['segment_count']} segs, "
                f"{unknown_stats['talk_seconds']:.2f}s)"
            )
        else:
            # No real pyannote speakers at all — promote UNKNOWN to SPEAKER_00.
            label_stats["SPEAKER_00"] = label_stats.pop("UNKNOWN")
            for seg in segments:
                if seg.get("speaker") == "UNKNOWN":
                    seg["speaker"] = "SPEAKER_00"
            logger.info(
                f"Meeting {meeting_id}: no pyannote speakers detected — "
                f"promoted UNKNOWN to SPEAKER_00"
            )

    # 2. Load existing bot-captured participants (from recording_complete)
    # Filter out bot names — the recording bot itself is not a meeting participant
    BOT_NAMES = {
        "meeting notetaker",
        "memoir notetaker",
        "memoir bot",
        "memoir",
        "notetaker",
        "support memoir",
        "memoir support",
        "hello@meetmemoir.com",
    }

    result = await db.execute(
        select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)
    )
    existing_participants = {}
    for p in result.scalars().all():
        if not p.platform_display_name:
            continue
        if p.platform_display_name.lower().strip() in BOT_NAMES:
            await db.delete(p)
            logger.info(f"Meeting {meeting_id}: filtered bot participant '{p.platform_display_name}'")
            continue
        existing_participants[p.platform_display_name] = p

    # 3. Load calendar attendees (if the meeting was from a calendar event)
    calendar_attendees = await _load_calendar_attendees(db, meeting)

    # 4. Build candidate name list (bot-captured / web-captured + calendar)
    # Source weighting tie-breaks duplicates: prefer higher-confidence
    # sources when fuzzy-matched names collide. Captions land via the
    # overlap-matrix path (not in candidates) at confidence 0.95 — the
    # values below are for pre-mapping rows where we have a name but
    # not yet a diarization label.
    candidates: list[dict] = []
    for name, participant in existing_participants.items():
        # Preserve the stored mapping_source instead of hardcoding
        # "bot_capture" — web_recorder rows arrive with mapping_source
        # = "web_capture", and operator/admin tooling needs the real
        # provenance for debugging speaker attribution.
        # Skip "unidentified" rows: prior to this refactor every
        # MeetingParticipant fed into candidates as bot_capture (0.65),
        # so unidentified rows participated in dedup at that weight.
        # Now that we honor the stored source, an unidentified row
        # would weight at 0.0 and lose every tie-break — which is a
        # behavior change for historical meetings. Treat unidentified
        # as "no candidate signal" and let calendar + voice match win.
        stored_source = participant.mapping_source or "bot_capture"
        if stored_source == "unidentified":
            continue
        candidates.append({
            "name": name,
            "source": stored_source,
            "email": None,
            "participant": participant,
        })
    for attendee in calendar_attendees:
        name = attendee.get("name") or attendee.get("email", "").split("@")[0]
        if not name:
            continue
        # Dedup by name with weight-aware tie-break: if an existing
        # candidate fuzzy-matches and has equal or higher source weight,
        # skip the calendar entry; otherwise replace.
        collision_idx = next(
            (i for i, c in enumerate(candidates) if _fuzzy_name_match(name, c["name"]) > 0.8),
            None,
        )
        if collision_idx is not None:
            existing_source = candidates[collision_idx]["source"]
            if _SOURCE_WEIGHT.get(existing_source, 0) >= _SOURCE_WEIGHT.get("calendar", 0):
                continue
            # Calendar source has higher weight — replace and continue
            candidates[collision_idx] = {
                "name": name,
                "source": "calendar",
                "email": attendee.get("email"),
                "participant": candidates[collision_idx].get("participant"),
            }
            continue
        candidates.append({
            "name": name,
            "source": "calendar",
            "email": attendee.get("email"),
            "participant": None,
        })

    # 4.3 Voice embedding matching FIRST — highest confidence signal
    speaker_map = {}
    voice_matched_labels: set[str] = set()
    already_matched_profile_ids: set[uuid.UUID] = set()
    sorted_labels = sorted(label_stats.keys(), key=lambda l: label_stats[l]["segment_count"], reverse=True)

    def _embedding_for(label: str) -> Optional[list[float]]:
        """Helper — pull this label's voice embedding out of the input
        dict if available. Threaded into _find_or_create_profile call
        sites below so a 0.70+ voice match against an existing profile
        prevents same-person/different-display-name duplicates."""
        if not embeddings_data:
            return None
        emb_entry = embeddings_data.get(label)
        if not emb_entry:
            return None
        emb = emb_entry.get("embedding") if isinstance(emb_entry, dict) else None
        return emb if emb else None

    if embeddings_data:
        try:
            from app.services.voice_embeddings import match_embeddings_optimal, EMBEDDING_DIM

            all_embeddings = {}
            for label in sorted_labels:
                emb_entry = embeddings_data.get(label)
                if not emb_entry:
                    continue
                embedding = emb_entry.get("embedding")
                if not embedding or len(embedding) != EMBEDDING_DIM:
                    continue
                all_embeddings[label] = embedding

            if all_embeddings:
                matches = await match_embeddings_optimal(
                    db, all_embeddings, team_id,
                    exclude_profile_ids=already_matched_profile_ids,
                )

                for label, (profile, cosine_score) in matches.items():
                    stats = label_stats[label]
                    # Check if bot-captured participant exists for this profile
                    existing_part = None
                    for cand_name, cand_part in existing_participants.items():
                        if cand_part.speaker_profile_id == profile.id:
                            existing_part = cand_part
                            break

                    if existing_part:
                        existing_part.diarization_label = label
                        existing_part.speaker_profile_id = profile.id
                        existing_part.platform_display_name = profile.display_name
                        existing_part.mapping_source = "voice_match"
                        existing_part.mapping_confidence = cosine_score
                        existing_part.talk_time_seconds = stats["talk_seconds"]
                        existing_part.segment_count = stats["segment_count"]
                        existing_part.word_count = stats["word_count"]
                    else:
                        participant = MeetingParticipant(
                            meeting_id=meeting_id,
                            diarization_label=label,
                            speaker_profile_id=profile.id,
                            platform_display_name=profile.display_name,
                            mapping_source="voice_match",
                            mapping_confidence=cosine_score,
                            talk_time_seconds=stats["talk_seconds"],
                            segment_count=stats["segment_count"],
                            word_count=stats["word_count"],
                        )
                        db.add(participant)

                    speaker_map[label] = {
                        "name": profile.display_name,
                        "profile_id": str(profile.id),
                    }
                    voice_matched_labels.add(label)
                    already_matched_profile_ids.add(profile.id)

            if voice_matched_labels:
                logger.info(
                    f"Meeting {meeting_id}: {len(voice_matched_labels)} speakers "
                    f"assigned via voice embedding match (priority)"
                )
        except Exception as e:
            logger.warning(f"Voice embedding matching failed for meeting {meeting_id}: {e}")

    # 4.5 Apply active-speaker overlap name map (for labels not matched by voice)
    overlap_assigned_labels: set[str] = set()

    if speaker_name_map:
        for label, name in speaker_name_map.items():
            if label not in label_stats:
                continue
            if label in voice_matched_labels:
                continue
            stats = label_stats[label]
            profile = await _find_or_create_profile(
                db, team_id, name, None, "active_speaker_overlap",
                embedding=_embedding_for(label),
            )
            # Check if a bot-captured participant exists for this name
            existing_participant = None
            for cand in candidates:
                if _fuzzy_name_match(name, cand["name"]) > 0.8 and cand.get("participant"):
                    existing_participant = cand["participant"]
                    break

            if existing_participant:
                existing_participant.diarization_label = label
                existing_participant.speaker_profile_id = profile.id
                existing_participant.mapping_source = "active_speaker_overlap"
                existing_participant.mapping_confidence = 0.85
                existing_participant.talk_time_seconds = stats["talk_seconds"]
                existing_participant.segment_count = stats["segment_count"]
                existing_participant.word_count = stats["word_count"]
            else:
                participant = MeetingParticipant(
                    meeting_id=meeting_id,
                    diarization_label=label,
                    speaker_profile_id=profile.id,
                    platform_display_name=name,
                    mapping_source="active_speaker_overlap",
                    mapping_confidence=0.85,
                    talk_time_seconds=stats["talk_seconds"],
                    segment_count=stats["segment_count"],
                    word_count=stats["word_count"],
                )
                db.add(participant)

            speaker_map[label] = {
                "name": name,
                "profile_id": str(profile.id),
            }
            overlap_assigned_labels.add(label)

        if overlap_assigned_labels:
            logger.info(
                f"Meeting {meeting_id}: {len(overlap_assigned_labels)} speakers "
                f"assigned via active speaker overlap"
            )

    # 4.6 Apply detected introductions as direct label→name assignments
    intro_assigned_labels: set[str] = set()

    if detected_introductions:
        for intro in detected_introductions:
            label = intro.get("speaker_label", "")
            name = intro.get("name", "").strip()
            if not label or not name or label not in label_stats:
                continue
            # Skip if already assigned via overlap
            if label in overlap_assigned_labels:
                continue
            # Skip if a bot-captured name already matched this label with high confidence
            if any(_fuzzy_name_match(name, c["name"]) > 0.8 for c in candidates):
                # A bot/calendar candidate exists for this person — let fuzzy matching
                # handle it (bot_capture/calendar have higher confidence)
                continue

            stats = label_stats[label]
            profile = await _find_or_create_profile(
                db, team_id, name, None, "introduction_detection",
                embedding=_embedding_for(label),
            )
            # Update profile with company/title from introduction
            if intro.get("company") and not profile.company:
                profile.company = intro["company"]
            if intro.get("title") and not profile.title:
                profile.title = intro["title"]

            participant = MeetingParticipant(
                meeting_id=meeting_id,
                diarization_label=label,
                speaker_profile_id=profile.id,
                platform_display_name=name,
                mapping_source="introduction_detection",
                mapping_confidence=0.6,
                talk_time_seconds=stats["talk_seconds"],
                segment_count=stats["segment_count"],
                word_count=stats["word_count"],
            )
            db.add(participant)

            speaker_map[label] = {
                "name": name,
                "profile_id": str(profile.id),
            }
            intro_assigned_labels.add(label)

        if intro_assigned_labels:
            logger.info(
                f"Meeting {meeting_id}: {len(intro_assigned_labels)} speakers assigned via introduction detection"
            )

    # 4.8 LLM-inferred names as fallback (lower confidence than voice/overlap)
    llm_assigned_labels: set[str] = set()
    if llm_inferred_names:
        for label in sorted_labels:
            if label in overlap_assigned_labels or label in intro_assigned_labels or label in voice_matched_labels:
                continue
            name = llm_inferred_names.get(label)
            if not name:
                continue
            stats = label_stats[label]
            profile = await _find_or_create_profile(
                db, team_id, name, None, "llm_inference",
                embedding=_embedding_for(label),
            )
            participant = MeetingParticipant(
                meeting_id=meeting_id,
                diarization_label=label,
                speaker_profile_id=profile.id,
                platform_display_name=name,
                mapping_source="llm_inference",
                mapping_confidence=0.3,
                talk_time_seconds=stats["talk_seconds"],
                segment_count=stats["segment_count"],
                word_count=stats["word_count"],
            )
            db.add(participant)
            speaker_map[label] = {"name": name, "profile_id": str(profile.id)}
            llm_assigned_labels.add(label)

        if llm_assigned_labels:
            logger.info(
                f"Meeting {meeting_id}: {len(llm_assigned_labels)} speakers "
                f"assigned via LLM inference"
            )

    # 5. Match diarization labels to candidates using fuzzy matching
    matched_candidates: set[int] = set()

    # Sort labels by segment count (most talkative first — higher chance of good match)
    sorted_labels = sorted(label_stats.keys(), key=lambda l: label_stats[l]["segment_count"], reverse=True)

    for label in sorted_labels:
        if label in overlap_assigned_labels or label in intro_assigned_labels or label in voice_matched_labels or label in llm_assigned_labels:
            continue
        best_score = 0.0
        best_idx = -1

        for idx, candidate in enumerate(candidates):
            if idx in matched_candidates:
                continue
            score = _fuzzy_name_match(label, candidate["name"])
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_score >= 0.5 and best_idx >= 0:
            matched_candidates.add(best_idx)
            candidate = candidates[best_idx]
            stats = label_stats[label]

            # Find or create speaker profile
            profile = await _find_or_create_profile(
                db, team_id, candidate["name"], candidate.get("email"), candidate["source"],
                embedding=_embedding_for(label),
            )

            # Update or create MeetingParticipant
            participant = candidate.get("participant")
            if participant:
                participant.diarization_label = label
                participant.speaker_profile_id = profile.id
                participant.mapping_source = candidate["source"]
                participant.mapping_confidence = best_score
            else:
                participant = MeetingParticipant(
                    meeting_id=meeting_id,
                    diarization_label=label,
                    speaker_profile_id=profile.id,
                    platform_display_name=candidate["name"],
                    mapping_source=candidate["source"],
                    mapping_confidence=best_score,
                )
                db.add(participant)

            # Update talk stats
            participant.talk_time_seconds = stats["talk_seconds"]
            participant.segment_count = stats["segment_count"]
            participant.word_count = stats["word_count"]

            speaker_map[label] = {
                "name": candidate["name"],
                "profile_id": str(profile.id),
            }

    # 6. Create participant rows for unmatched labels
    for label in sorted_labels:
        if label in speaker_map:
            continue
        stats = label_stats[label]

        # Check if a participant row already exists for this label
        existing = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.diarization_label == label,
            )
        )
        if not existing.scalar_one_or_none():
            participant = MeetingParticipant(
                meeting_id=meeting_id,
                diarization_label=label,
                mapping_source="unidentified",
                talk_time_seconds=stats["talk_seconds"],
                segment_count=stats["segment_count"],
                word_count=stats["word_count"],
            )
            db.add(participant)

    # 6.5 Remove participants with no diarization label (bot-captured duplicates)
    orphan_result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.diarization_label.is_(None),
        )
    )
    for orphan in orphan_result.scalars().all():
        logger.info(
            f"Meeting {meeting_id}: removing unmatched participant "
            f"'{orphan.platform_display_name}' (no diarization label)"
        )
        await db.delete(orphan)

    # 7. Update transcription speaker_map cache
    transcription.speaker_map = speaker_map if speaker_map else None

    # 8. Update speaker profile stats
    for label, entry in speaker_map.items():
        profile_id = uuid.UUID(entry["profile_id"])
        prof_result = await db.execute(
            select(SpeakerProfile).where(SpeakerProfile.id == profile_id)
        )
        profile = prof_result.scalar_one_or_none()
        if profile:
            profile.meeting_count = (profile.meeting_count or 0) + 1
            profile.total_talk_seconds = (profile.total_talk_seconds or 0) + label_stats[label]["talk_seconds"]
            now = datetime.now(timezone.utc)
            if not profile.first_seen_at:
                profile.first_seen_at = now
            profile.last_seen_at = now

    await db.flush()
    logger.info(
        f"Meeting {meeting_id}: mapped {len(speaker_map)}/{len(label_stats)} speakers "
        f"({len(existing_participants)} bot-captured, {len(calendar_attendees)} calendar, "
        f"{len(overlap_assigned_labels)} overlap, {len(intro_assigned_labels)} introductions, "
        f"{len(voice_matched_labels)} voice, {len(llm_assigned_labels)} llm)"
    )

    return speaker_map


async def _load_calendar_attendees(db: AsyncSession, meeting: Meeting) -> list[dict]:
    """Load attendees from the linked calendar event, if any."""
    from app.models.calendar import CalendarEvent

    if not meeting.id:
        return []

    # Find calendar event linked to this meeting
    result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.linked_meeting_id == meeting.id)
    )
    event = result.scalar_one_or_none()
    if not event or not event.attendees:
        return []

    return event.attendees if isinstance(event.attendees, list) else []


async def _find_or_create_profile(
    db: AsyncSession,
    team_id: uuid.UUID,
    name: str,
    email: Optional[str],
    source: str,
    *,
    embedding: Optional[list[float]] = None,
) -> SpeakerProfile:
    """Find an existing speaker profile or create a new one.

    Resolution order:
      1. Exact email match (when both have email)
      2. Exact display_name match (case-sensitive)
      3. Voice-embedding match against ALL team profiles (when embedding
         provided) using the same MATCH_THRESHOLD (0.70) as voice_match
         step. Prevents creating duplicate profiles when the bot scrapes
         a slightly different display name for the same person (e.g.
         "Sathish Kumar" vs "Sathish Kumar Balasubramani") — without
         this, every minor display-name variant becomes a new profile
         even though the voice embedding clearly identifies the speaker.
      4. Create new profile.
    """
    # 1. Email match
    if email:
        result = await db.execute(
            select(SpeakerProfile).where(
                SpeakerProfile.team_id == team_id,
                SpeakerProfile.email == email,
            )
        )
        profile = result.scalar_one_or_none()
        if profile:
            return profile

    # 2. Exact name match
    result = await db.execute(
        select(SpeakerProfile).where(
            SpeakerProfile.team_id == team_id,
            SpeakerProfile.display_name == name,
        )
    )
    profile = result.scalar_one_or_none()
    if profile:
        if email and not profile.email:
            profile.email = email
        return profile

    # 3. Voice-embedding match — only when we have an embedding and the
    # cheaper name/email lookups missed. This is what prevents the
    # "Sathish Kumar" vs "Sathish Kumar Balasubramani" split from
    # recurring on every new meeting where the bot sees a shortened
    # display name.
    if embedding:
        try:
            from app.services.voice_embeddings import match_embedding_to_profiles
            match = await match_embedding_to_profiles(db, embedding, team_id)
            if match:
                profile, score = match
                logger.info(
                    f"Voice-match reused profile {profile.id} ('{profile.display_name}') "
                    f"for incoming name '{name}' (score={score:.3f})"
                )
                # If the new display name is longer (typically more
                # canonical), upgrade. Shorter scrapes lose to fuller
                # forms — "Sathish Kumar Balasubramani" wins over
                # "Sathish Kumar".
                if len(name.strip()) > len(profile.display_name or ""):
                    profile.display_name = name
                if email and not profile.email:
                    profile.email = email
                return profile
        except Exception as e:
            logger.warning(f"Voice-embedding lookup failed (non-fatal): {e}")

    # 4. Create new profile
    profile = SpeakerProfile(
        team_id=team_id,
        display_name=name,
        email=email,
        source=source,
    )
    db.add(profile)
    await db.flush()
    return profile
