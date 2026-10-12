"""
Voice embedding extraction using ECAPA-TDNN (SpeechBrain).

Extracts 192-dimensional speaker embeddings from audio segments.
Used by the transcription pipeline (batch) and the manual speaker edit API (single).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("audio_preprocess.voice_embeddings")

EMBEDDING_DIM = 192
MIN_DURATION_SECONDS = 5.0

# Lazy-loaded globals (heavy imports)
_classifier = None


def _patch_torch_amp():
    """Patch torch.amp for SpeechBrain compatibility across torch versions.

    torch < 2.4: torch.amp.custom_fwd doesn't exist, SpeechBrain calls it
    with device_type kwarg → need to redirect to torch.cuda.amp.custom_fwd
    and strip device_type.

    torch >= 2.4: torch.amp.custom_fwd exists and accepts device_type
    natively → no patch needed.
    """
    import torch

    if hasattr(torch.amp, "custom_fwd"):
        return

    import functools
    _orig_fwd = torch.cuda.amp.custom_fwd
    _orig_bwd = torch.cuda.amp.custom_bwd

    @functools.wraps(_orig_fwd)
    def _compat_custom_fwd(*args, **kwargs):
        kwargs.pop("device_type", None)
        return _orig_fwd(*args, **kwargs)

    @functools.wraps(_orig_bwd)
    def _compat_custom_bwd(*args, **kwargs):
        kwargs.pop("device_type", None)
        return _orig_bwd(*args, **kwargs)

    torch.amp.custom_fwd = _compat_custom_fwd
    torch.amp.custom_bwd = _compat_custom_bwd


def _get_classifier():
    """Lazy-load the SpeechBrain ECAPA-TDNN classifier (cached after first call).

    Tries CUDA first; on CUDA OOM falls back to CPU. ECAPA is small (~85 MB)
    and only runs 2-3 times per call (one per unique speaker for the persistent
    embedding + a handful for sliding-window verification). CPU fallback adds
    ~5-10s total per call — acceptable cost to keep voice-embedding name
    mapping working under VRAM pressure from vLLM + faster-whisper.

    Without this fallback, the call site catches the OOM as a "non-fatal"
    warning and skips voice lookup entirely — leaving SPEAKER_XX labels
    unmapped in the polished transcript even when DB profiles exist.

    To force CPU regardless: VOICE_EMBEDDING_DEVICE=cpu.
    """
    import os
    global _classifier
    if _classifier is not None:
        return _classifier

    _patch_torch_amp()
    from speechbrain.inference.speaker import EncoderClassifier

    forced_device = os.getenv("VOICE_EMBEDDING_DEVICE", "").lower()
    # Try CUDA unless explicitly disabled
    try_devices = []
    if forced_device == "cpu":
        try_devices = ["cpu"]
    elif forced_device == "cuda":
        try_devices = ["cuda"]
    else:
        try_devices = ["cuda", "cpu"]  # auto: try cuda, fall back to cpu

    last_err: Exception | None = None
    for device in try_devices:
        try:
            _classifier = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                run_opts={"device": device},
            )
            logger.info(f"ECAPA-TDNN speaker encoder loaded ({device.upper()})")
            return _classifier
        except Exception as e:
            msg = str(e)
            last_err = e
            if "out of memory" in msg.lower() or "CUDA" in msg or "cublas" in msg.lower():
                logger.warning(
                    f"ECAPA-TDNN load on {device} failed (likely VRAM contention): {msg[:200]} — "
                    f"trying next device" if device != try_devices[-1] else f"all devices exhausted"
                )
                continue
            raise

    raise RuntimeError(f"Failed to load ECAPA-TDNN on any device: {last_err}")


def _reload_classifier_on_cpu() -> None:
    """Force-reload the ECAPA classifier on CPU. Called after a CUDA OOM at
    inference time to recover gracefully and keep voice-embedding lookup
    working in the rest of the pipeline."""
    global _classifier
    _classifier = None  # drop the GPU-resident copy
    import os
    os.environ["VOICE_EMBEDDING_DEVICE"] = "cpu"  # pin subsequent loads to CPU
    _get_classifier()  # re-init on CPU


def extract_embedding(audio_path: str, start: float, end: float) -> list[float] | None:
    """Extract a speaker embedding from a segment of audio.

    Returns a 192-dim float list or None if extraction fails.

    Handles CUDA OOM at encode_batch time: catches the OOM, reloads the
    classifier on CPU, retries once. After this fallback fires, all
    subsequent calls run on CPU (no thrashing back to GPU).
    """
    import torchaudio

    duration = end - start
    if duration < MIN_DURATION_SECONDS:
        logger.debug(f"Segment too short ({duration:.1f}s < {MIN_DURATION_SECONDS}s)")
        return None

    classifier = _get_classifier()

    waveform, sample_rate = torchaudio.load(audio_path)
    start_frame = int(start * sample_rate)
    end_frame = int(end * sample_rate)
    segment = waveform[:, start_frame:end_frame]

    if segment.shape[1] < sample_rate * MIN_DURATION_SECONDS:
        logger.debug(f"Audio segment too short after framing ({segment.shape[1] / sample_rate:.1f}s)")
        return None

    try:
        embedding = classifier.encode_batch(segment)
    except Exception as e:
        msg = str(e)
        if "out of memory" in msg.lower() or "cuda" in msg.lower():
            logger.warning(
                f"ECAPA encode OOM on GPU — falling back to CPU for this and "
                f"all subsequent calls: {msg[:200]}"
            )
            _reload_classifier_on_cpu()
            classifier = _get_classifier()
            embedding = classifier.encode_batch(segment)  # retry on CPU
        else:
            raise

    return embedding.squeeze().cpu().numpy().tolist()


def extract_all_embeddings(audio_path: str, segments: list[dict]) -> dict:
    """Extract voice embeddings for all unique speakers in a transcription.

    For each speaker, finds their longest segment and extracts an embedding.

    Args:
        audio_path: Path to the recording WAV file.
        segments: List of transcript segment dicts with 'speaker', 'start', 'end' keys.

    Returns:
        Dict of {speaker_label: {"embedding": [...], "start": float, "end": float, "duration": float}}
    """
    # Group segments by speaker
    speaker_segments: dict[str, list[dict]] = {}
    for seg in segments:
        speaker = seg.get("speaker")
        if not speaker:
            continue
        if speaker not in speaker_segments:
            speaker_segments[speaker] = []
        speaker_segments[speaker].append(seg)

    results = {}

    for label, segs in speaker_segments.items():
        # Total speech duration for this speaker
        total_duration = sum(s.get("end", 0) - s.get("start", 0) for s in segs)
        if total_duration < MIN_DURATION_SECONDS:
            logger.debug(f"{label}: insufficient speech ({total_duration:.1f}s < {MIN_DURATION_SECONDS}s)")
            continue

        # Use the longest continuous segment (most reliable for embedding)
        best_seg = max(segs, key=lambda s: s.get("end", 0) - s.get("start", 0))
        seg_start = best_seg.get("start", 0)
        seg_end = best_seg.get("end", 0)
        seg_duration = seg_end - seg_start

        embedding = extract_embedding(audio_path, seg_start, seg_end)
        if embedding is not None:
            results[label] = {
                "embedding": embedding,
                "start": seg_start,
                "end": seg_end,
                "duration": seg_duration,
            }
            logger.info(f"Extracted embedding for {label} ({seg_duration:.1f}s segment)")

    return results


def save_embeddings(embeddings: dict, output_path: str) -> None:
    """Save embeddings dict to a JSON file."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(embeddings, indent=2))
    logger.info(f"Saved {len(embeddings)} embeddings to {output_path}")


MATCH_THRESHOLD = 0.70


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    import numpy as np
    a_np = np.array(a, dtype=np.float32)
    b_np = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(a_np)
    norm_b = np.linalg.norm(b_np)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_np, b_np) / (norm_a * norm_b))


async def lookup_voice_names(
    team_id: str,
    embeddings_data: dict,
    db_url: str | None = None,
) -> dict[str, str]:
    """Look up speaker names from DB voice profiles by cosine similarity.

    READ-ONLY DB query. No writes, no model creation.
    Returns {SPEAKER_XX: "Full Name"} for matched speakers.
    """
    import os
    import asyncpg

    if not embeddings_data or not team_id:
        return {}

    url = db_url or os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("lookup_voice_names: no DATABASE_URL")
        return {}

    try:
        conn = await asyncpg.connect(url)
    except Exception as e:
        logger.warning(f"lookup_voice_names: DB connect failed: {e}")
        return {}

    try:
        rows = await conn.fetch("""
            SELECT sp.display_name, ves.embedding, ves.quality_score
            FROM voice_embedding_samples ves
            JOIN speaker_profiles sp ON sp.id = ves.speaker_profile_id
            WHERE sp.team_id = $1
        """, team_id if not isinstance(team_id, str) else __import__('uuid').UUID(team_id))

        if not rows:
            return {}

        # Build profile embeddings: {name: [(embedding, quality_score), ...]}
        import struct
        profiles: dict[str, list[tuple[list[float], float]]] = {}
        for row in rows:
            name = row["display_name"]
            emb_bytes = row["embedding"]
            quality = row["quality_score"] or 1.0
            if not emb_bytes or len(emb_bytes) != EMBEDDING_DIM * 4:
                continue
            embedding = list(struct.unpack(f"{EMBEDDING_DIM}f", emb_bytes))
            if name not in profiles:
                profiles[name] = []
            profiles[name].append((embedding, quality))

        if not profiles:
            return {}

        # Compute quality-weighted centroid per profile
        import numpy as np
        centroids: dict[str, list[float]] = {}
        for name, samples in profiles.items():
            weighted_sum = np.zeros(EMBEDDING_DIM, dtype=np.float64)
            total_weight = 0.0
            for emb, quality in samples:
                weighted_sum += np.array(emb, dtype=np.float64) * quality
                total_weight += quality
            if total_weight > 0:
                centroids[name] = (weighted_sum / total_weight).tolist()

        # Match each new embedding to best profile
        result: dict[str, str] = {}
        used_profiles: set[str] = set()

        # Score all pairs, then assign greedily (best first)
        scores: list[tuple[float, str, str]] = []
        for label, emb_entry in embeddings_data.items():
            embedding = emb_entry.get("embedding") if isinstance(emb_entry, dict) else None
            if not embedding or len(embedding) != EMBEDDING_DIM:
                continue
            for name, centroid in centroids.items():
                sim = _cosine_similarity(embedding, centroid)
                if sim >= MATCH_THRESHOLD:
                    scores.append((sim, label, name))

        scores.sort(reverse=True)
        used_labels: set[str] = set()
        for sim, label, name in scores:
            if label in used_labels or name in used_profiles:
                continue
            result[label] = name
            used_labels.add(label)
            used_profiles.add(name)
            logger.info(f"Voice lookup: {label} → {name} (score={sim:.4f})")

        return result

    except Exception as e:
        logger.warning(f"lookup_voice_names failed: {e}")
        return {}
    finally:
        await conn.close()
