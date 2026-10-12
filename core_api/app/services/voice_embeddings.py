"""
Voice embedding storage and matching service.

Embeddings are 192-dimensional float32 vectors stored as bytes in the DB.
Matching uses cosine similarity with a threshold of 0.70.

Extraction is handled by TranscriptionBase (which has SpeechBrain/torchaudio).
This service reads pre-extracted embeddings from files or requests them via HTTP.
"""

import json
import logging
import math
import struct
import uuid
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.speaker import SpeakerProfile, VoiceEmbeddingSample

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 192
EMBEDDING_MODEL = "ecapa-tdnn"
MATCH_THRESHOLD = 0.70
MAX_SAMPLES_PER_PROFILE = 10
MIN_DURATION_SECONDS = 5.0  # Minimum speech for reliable embedding


def embedding_to_bytes(embedding: list[float]) -> bytes:
    """Convert a list of float32 values to bytes for DB storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def bytes_to_embedding(data: bytes) -> list[float]:
    """Convert bytes from DB back to list of floats."""
    count = len(data) // 4  # float32 = 4 bytes
    return list(struct.unpack(f"{count}f", data))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def quality_score(duration_seconds: float) -> float:
    """Compute quality score based on speech duration. 0-5 scale."""
    if duration_seconds < MIN_DURATION_SECONDS:
        return duration_seconds / MIN_DURATION_SECONDS
    return min(5.0, 1.0 + (duration_seconds - MIN_DURATION_SECONDS) / 15.0)


async def match_embedding_to_profiles(
    db: AsyncSession,
    embedding: list[float],
    team_id: uuid.UUID,
    exclude_profile_ids: Optional[set[uuid.UUID]] = None,
) -> Optional[tuple[SpeakerProfile, float]]:
    """Match a voice embedding against all profiles for a team.

    Returns (matched_profile, confidence_score) or None if no match above threshold.
    """
    profile_scores = await _compute_profile_scores(db, [embedding], team_id, exclude_profile_ids)
    if not profile_scores:
        return None

    best_profile_id = None
    best_score = 0.0
    for profile_id, scores in profile_scores.items():
        if scores[0] > best_score:
            best_score = scores[0]
            best_profile_id = profile_id

    if best_score >= MATCH_THRESHOLD and best_profile_id:
        prof_result = await db.execute(
            select(SpeakerProfile).where(SpeakerProfile.id == best_profile_id)
        )
        profile = prof_result.scalar_one_or_none()
        if profile:
            return profile, best_score

    return None


async def match_embeddings_optimal(
    db: AsyncSession,
    speaker_embeddings: dict[str, list[float]],
    team_id: uuid.UUID,
    exclude_profile_ids: Optional[set[uuid.UUID]] = None,
) -> dict[str, tuple[SpeakerProfile, float]]:
    """Match speaker embeddings to profiles using greedy best-first assignment.

    Scores all (speaker, profile) pairs, then assigns greedily starting from
    the highest score. Each speaker and profile can only be matched once.
    Speakers below the threshold are skipped — they don't block other matches.

    Returns dict of {speaker_label: (profile, score)} for matched speakers.
    """
    labels = list(speaker_embeddings.keys())
    embeddings = [speaker_embeddings[l] for l in labels]

    profile_scores = await _compute_profile_scores(db, embeddings, team_id, exclude_profile_ids)
    if not profile_scores:
        return {}

    profile_ids = list(profile_scores.keys())

    # Score all (speaker, profile) pairs above threshold
    scored_pairs: list[tuple[float, int, int]] = []
    for spk_idx in range(len(labels)):
        for prof_idx, pid in enumerate(profile_ids):
            score = profile_scores[pid][spk_idx]
            if score >= MATCH_THRESHOLD:
                scored_pairs.append((score, spk_idx, prof_idx))

    # Sort by score descending — best matches first
    scored_pairs.sort(reverse=True)

    # Greedy assignment: each speaker and profile matched at most once
    used_speakers: set[int] = set()
    used_profiles: set[int] = set()
    assignments: list[tuple[int, int, float]] = []

    for score, spk_idx, prof_idx in scored_pairs:
        if spk_idx in used_speakers or prof_idx in used_profiles:
            continue
        assignments.append((spk_idx, prof_idx, score))
        used_speakers.add(spk_idx)
        used_profiles.add(prof_idx)

    if not assignments:
        return {}

    # Load matched profiles
    matched_pids = {profile_ids[prof_idx] for _, prof_idx, _ in assignments}
    prof_result = await db.execute(
        select(SpeakerProfile).where(SpeakerProfile.id.in_(matched_pids))
    )
    profiles_by_id = {p.id: p for p in prof_result.scalars().all()}

    result = {}
    for spk_idx, prof_idx, score in assignments:
        pid = profile_ids[prof_idx]
        profile = profiles_by_id.get(pid)
        if profile:
            result[labels[spk_idx]] = (profile, score)
            logger.info(
                f"  Voice match: {labels[spk_idx]} → {profile.display_name} "
                f"(score={score:.4f})"
            )

    return result


async def _compute_centroid_scores(
    db: AsyncSession,
    embeddings: list[list[float]],
    team_id: uuid.UUID,
    exclude_profile_ids: Optional[set[uuid.UUID]] = None,
) -> dict[uuid.UUID, list[float]]:
    """Compute similarity scores using unweighted centroids per profile.

    Unlike _compute_profile_scores which uses quality-weighted averages,
    this uses unweighted mean centroids. A single contaminated high-quality
    sample can dominate the weighted average but has less influence on
    the unweighted centroid when the profile has many correct samples.
    """
    result = await db.execute(
        select(VoiceEmbeddingSample)
        .join(SpeakerProfile, VoiceEmbeddingSample.speaker_profile_id == SpeakerProfile.id)
        .where(SpeakerProfile.team_id == team_id)
    )
    samples = result.scalars().all()
    if not samples:
        return {}

    profile_embs: dict[uuid.UUID, list[list[float]]] = {}
    for sample in samples:
        pid = sample.speaker_profile_id
        if exclude_profile_ids and pid in exclude_profile_ids:
            continue
        if pid not in profile_embs:
            profile_embs[pid] = []
        profile_embs[pid].append(bytes_to_embedding(sample.embedding))

    scores: dict[uuid.UUID, list[float]] = {}
    for pid, emb_list in profile_embs.items():
        dim = len(emb_list[0])
        centroid = [0.0] * dim
        for emb in emb_list:
            for i in range(dim):
                centroid[i] += emb[i]
        n = len(emb_list)
        centroid = [x / n for x in centroid]

        profile_scores = []
        for query_emb in embeddings:
            profile_scores.append(cosine_similarity(query_emb, centroid))
        scores[pid] = profile_scores

    return scores


async def _compute_profile_scores(
    db: AsyncSession,
    embeddings: list[list[float]],
    team_id: uuid.UUID,
    exclude_profile_ids: Optional[set[uuid.UUID]] = None,
) -> dict[uuid.UUID, list[float]]:
    """Compute similarity scores for each embedding against each profile.

    Returns {profile_id: [score_for_emb_0, score_for_emb_1, ...]}.

    Score = max(quality_weighted_avg_across_samples, best_single_sample_score).

    The weighted-average defends against false-positive matches when ONE
    sample happens to look similar to an unrelated query. The single-sample
    best defends against false-NEGATIVES when a profile has many samples
    captured in widely varying acoustic environments (different mics,
    different rooms, different codecs) that smear the average below the
    match threshold even though one stored sample matches strongly.

    Witnessed 2026-05-27: profile "Sathish Kumar" with 10 samples from
    past meetings had best-single-sample = 0.7152 (above 0.70 threshold)
    for a tab recording's SPEAKER_00, but weighted-avg = 0.51 — missing
    the match entirely. Same person, just captured in a new acoustic
    environment that wasn't represented in the profile yet.
    """
    result = await db.execute(
        select(VoiceEmbeddingSample)
        .join(SpeakerProfile, VoiceEmbeddingSample.speaker_profile_id == SpeakerProfile.id)
        .where(SpeakerProfile.team_id == team_id)
    )
    samples = result.scalars().all()

    if not samples:
        return {}

    profile_samples: dict[uuid.UUID, list[tuple[list[float], float]]] = {}
    for sample in samples:
        pid = sample.speaker_profile_id
        if exclude_profile_ids and pid in exclude_profile_ids:
            continue
        if pid not in profile_samples:
            profile_samples[pid] = []
        emb = bytes_to_embedding(sample.embedding)
        profile_samples[pid].append((emb, sample.quality_score))

    scores: dict[uuid.UUID, list[float]] = {}
    for profile_id, emb_list in profile_samples.items():
        profile_scores = []
        for query_emb in embeddings:
            total_weight = 0.0
            weighted_sim = 0.0
            best_sim = 0.0
            for stored_emb, q_score in emb_list:
                sim = cosine_similarity(query_emb, stored_emb)
                weight = max(0.1, q_score)
                weighted_sim += sim * weight
                total_weight += weight
                if sim > best_sim:
                    best_sim = sim
            avg_sim = weighted_sim / total_weight if total_weight > 0 else 0.0
            # Score = max of the two signals (see docstring above).
            profile_scores.append(max(avg_sim, best_sim))
        scores[profile_id] = profile_scores

    return scores


def check_embedding_swap(
    embedding: list[float],
    assigned_profile_id: uuid.UUID,
    profile_samples: dict[uuid.UUID, list[tuple[list[float], float]]],
) -> bool:
    """Check if an embedding might be swapped — more similar to another profile.

    Uses centroid-based comparison instead of weighted average to avoid being
    fooled by contaminated samples. Computes the centroid (unweighted mean) of
    each profile's embeddings, then checks if the new embedding is closer to
    a different profile's centroid.

    Also checks internal consistency: if the embedding's similarity to the
    assigned profile's centroid is below 0.80, it's likely wrong.

    Returns True if the embedding should NOT be stored (potential swap detected).
    """
    def _centroid(emb_list):
        if not emb_list:
            return None
        dim = len(emb_list[0][0])
        avg = [0.0] * dim
        for emb, _ in emb_list:
            for i in range(dim):
                avg[i] += emb[i]
        n = len(emb_list)
        return [x / n for x in avg]

    centroids = {}
    for profile_id, emb_list in profile_samples.items():
        c = _centroid(emb_list)
        if c:
            centroids[profile_id] = c

    if assigned_profile_id not in centroids:
        return False

    assigned_sim = cosine_similarity(embedding, centroids[assigned_profile_id])

    # Internal consistency: if similarity to own centroid is too low, skip
    if assigned_sim < 0.80:
        logger.warning(
            f"Swap detection: embedding similarity to assigned profile "
            f"{assigned_profile_id} centroid is only {assigned_sim:.4f} (< 0.80) "
            f"— skipping storage"
        )
        return True

    best_other_sim = 0.0
    best_other_id = None
    for profile_id, centroid in centroids.items():
        if profile_id == assigned_profile_id:
            continue
        sim = cosine_similarity(embedding, centroid)
        if sim > best_other_sim:
            best_other_sim = sim
            best_other_id = profile_id

    if best_other_sim > assigned_sim and best_other_sim >= MATCH_THRESHOLD:
        logger.warning(
            f"Swap detection: embedding matches profile {best_other_id} centroid "
            f"({best_other_sim:.4f}) better than assigned profile {assigned_profile_id} "
            f"({assigned_sim:.4f}) — skipping storage"
        )
        return True

    return False


async def store_embedding_sample(
    db: AsyncSession,
    profile_id: uuid.UUID,
    embedding: list[float],
    duration_seconds: float,
    meeting_id: Optional[uuid.UUID] = None,
    diarization_label: Optional[str] = None,
    source: str = "meeting",
) -> VoiceEmbeddingSample:
    """Store a voice embedding sample for a speaker profile.

    Maintains max 10 samples per profile — replaces lowest quality if full.
    """
    # Check existing sample count
    result = await db.execute(
        select(VoiceEmbeddingSample)
        .where(VoiceEmbeddingSample.speaker_profile_id == profile_id)
        .order_by(VoiceEmbeddingSample.quality_score.asc())
    )
    existing = result.scalars().all()

    q_score = quality_score(duration_seconds)

    if len(existing) >= MAX_SAMPLES_PER_PROFILE:
        # Replace the lowest quality sample if new one is better
        worst = existing[0]
        if q_score > worst.quality_score:
            await db.delete(worst)
        else:
            logger.debug(f"Profile {profile_id}: all {MAX_SAMPLES_PER_PROFILE} samples are higher quality, skipping")
            return existing[-1]  # Return best existing

    sample = VoiceEmbeddingSample(
        speaker_profile_id=profile_id,
        embedding=embedding_to_bytes(embedding),
        embedding_model=EMBEDDING_MODEL,
        sample_duration_seconds=duration_seconds,
        source_meeting_id=meeting_id,
        source_diarization_label=diarization_label,
        source=source,
        quality_score=q_score,
    )
    db.add(sample)
    await db.flush()
    return sample


# --- New functions for the split extraction model ---


def load_embeddings_from_file(embeddings_path: str) -> Optional[dict]:
    """Read embeddings.json saved by TranscriptionBase pipeline.

    Returns dict of {speaker_label: {"embedding": [...], "start": ..., "end": ..., "duration": ...}}
    or None if file doesn't exist or can't be read.
    """
    from pathlib import Path

    path = Path(embeddings_path)
    if not path.exists():
        logger.debug(f"Embeddings file not found: {embeddings_path}")
        return None

    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            logger.warning(f"Embeddings file has unexpected format: {embeddings_path}")
            return None
        return data
    except Exception as e:
        logger.error(f"Failed to read embeddings file {embeddings_path}: {e}")
        return None


async def store_embeddings_for_speakers(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    speaker_map: dict,
    embeddings_data: dict,
) -> dict[str, uuid.UUID]:
    """Store pre-extracted embeddings for mapped speakers.

    Includes swap detection: before storing, checks if the embedding is
    more similar to a different profile than the assigned one. If so,
    skips storage to prevent contamination.

    Returns dict of diarization_label -> profile_id for profiles that got embeddings.
    """
    results = {}

    # Pre-load all profile samples for swap detection
    all_profile_ids = set()
    label_embeddings = {}
    for label, entry in speaker_map.items():
        pid_str = entry.get("profile_id")
        if pid_str:
            all_profile_ids.add(uuid.UUID(pid_str))
            emb_entry = embeddings_data.get(label)
            if emb_entry:
                embedding = emb_entry.get("embedding")
                if embedding and len(embedding) == EMBEDDING_DIM:
                    label_embeddings[label] = embedding

    # Load existing samples for swap detection (only if multiple profiles)
    profile_samples_cache: dict[uuid.UUID, list[tuple[list[float], float]]] = {}
    if len(all_profile_ids) >= 2:
        from sqlalchemy import select as _sel
        sample_result = await db.execute(
            _sel(VoiceEmbeddingSample).where(
                VoiceEmbeddingSample.speaker_profile_id.in_(all_profile_ids)
            )
        )
        for sample in sample_result.scalars().all():
            pid = sample.speaker_profile_id
            if pid not in profile_samples_cache:
                profile_samples_cache[pid] = []
            emb = bytes_to_embedding(sample.embedding)
            profile_samples_cache[pid].append((emb, sample.quality_score))

    for label, entry in speaker_map.items():
        profile_id_str = entry.get("profile_id")
        if not profile_id_str:
            continue

        profile_id = uuid.UUID(profile_id_str)
        emb_entry = embeddings_data.get(label)
        if not emb_entry:
            logger.debug(f"Speaker {label}: no embedding data found")
            continue

        embedding = emb_entry.get("embedding")
        duration = emb_entry.get("duration", 0)

        if not embedding or len(embedding) != EMBEDDING_DIM:
            logger.warning(f"Speaker {label}: invalid embedding (len={len(embedding) if embedding else 0})")
            continue

        # Swap detection: skip if embedding matches another profile better
        if len(profile_samples_cache) >= 2:
            if check_embedding_swap(embedding, profile_id, profile_samples_cache):
                logger.warning(f"Speaker {label}: skipped embedding storage (swap detected)")
                continue

        await store_embedding_sample(
            db, profile_id, embedding, duration,
            meeting_id=meeting_id,
            diarization_label=label,
            source="meeting",
        )
        results[label] = profile_id
        logger.info(f"Speaker {label}: embedding stored ({duration:.1f}s)")

    return results


async def request_single_embedding(
    recording_path: str, start: float, end: float
) -> Optional[list[float]]:
    """Request a single voice embedding from TranscriptionBase.

    Calls POST /audio-preprocess/voice-embeddings/extract on TranscriptionBase.
    Returns the 192-dim embedding list or None on failure.
    """
    url = f"{settings.TRANSCRIPTION_SERVICE_URL}/audio-preprocess/voice-embeddings/extract"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json={
                "recording_path": recording_path,
                "start": start,
                "end": end,
            })

        if resp.status_code == 200:
            data = resp.json()
            return data.get("embedding")
        elif resp.status_code == 422:
            logger.debug(f"TranscriptionBase rejected embedding request: {resp.text[:200]}")
            return None
        else:
            logger.warning(f"TranscriptionBase embedding request failed ({resp.status_code}): {resp.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"Failed to request embedding from TranscriptionBase: {e}")
        return None
