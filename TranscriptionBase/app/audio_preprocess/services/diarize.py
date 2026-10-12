"""
Speaker diarization service — identifies "who spoke when" in audio.

Uses pyannote.audio 3.1 speaker-diarization-3.1 model.
Supports multichannel detection (stereo with separate L/R speakers)
and single-channel AI diarization via pyannote.

Architecture:
    1. Check if audio is multichannel (stereo with different content per channel)
    2. If mono/same-content stereo → run pyannote diarization
    3. Return structured speaker segments

Integration:
    Called from pipeline.py AFTER preprocessing and BEFORE ASR.
    Speaker labels are then aligned to Whisper segments by temporal overlap.
"""
import logging
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import torch

logger = logging.getLogger("audio_preprocess")

# Pyannote pipeline singleton
_diarization_pipeline = None
_diarization_lock = threading.Lock()

# Speaker embedding model singleton
_embedding_inference = None
_embedding_lock = threading.Lock()

# Cluster centroids carried through the pipeline via the diarization_result
# dict (key: "cluster_centroids"). Type 6's verify_speaker_clusters() writes
# them in (as lists of floats — JSON-serializable so they survive the
# R2 round-trip to Type 5). Type 5's _verify_segment_speakers() reads them
# back and re-hydrates to numpy arrays. Saves ~120 wespeaker embedding
# calls per meeting (~6-12 s GPU time) by avoiding a duplicate
# _extract_cluster_embeddings() call.
def _centroids_to_lists(centroids: dict) -> dict:
    """Convert {speaker: np.array} to {speaker: list[float]} for JSON storage."""
    out: dict = {}
    for spk, arr in (centroids or {}).items():
        if arr is None:
            continue
        try:
            out[spk] = [float(x) for x in np.array(arr).flatten().tolist()]
        except Exception:
            continue
    return out


def _centroids_from_lists(serialized: dict) -> dict:
    """Convert {speaker: list[float]} back to {speaker: np.array} for use in cosine sims."""
    out: dict = {}
    for spk, lst in (serialized or {}).items():
        if not lst:
            continue
        try:
            arr = np.array(lst, dtype=np.float64)
            if arr.size == 0:
                continue
            out[spk] = arr
        except Exception:
            continue
    return out


def _load_diarization_pipeline(model_name: str, device: str, hf_token: str,
                               clustering_threshold: float = 0.0,
                               min_cluster_size: int = 0):
    """Load pyannote diarization pipeline as a singleton (thread-safe, double-check pattern).

    Args:
        clustering_threshold: Override agglomerative clustering threshold (0.0 = use default).
            Lower values = more aggressive speaker splitting.
        min_cluster_size: Override minimum cluster size (0 = use default).
            Lower values = allow shorter speaker segments to form clusters.
    """
    global _diarization_pipeline
    if _diarization_pipeline is not None:
        return _diarization_pipeline
    with _diarization_lock:
        if _diarization_pipeline is not None:
            return _diarization_pipeline
        logger.info(f"Loading diarization pipeline: {model_name} on {device}...")
        import os
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(model_name)
        pipeline.to(torch.device(device))

        # Log default hyperparameters before any override
        try:
            default_threshold = pipeline.clustering.threshold
            default_min_cluster = pipeline.clustering.min_cluster_size
            logger.info(f"Pyannote defaults — clustering.threshold={default_threshold}, "
                        f"clustering.min_cluster_size={default_min_cluster}")
        except Exception as e:
            logger.warning(f"Could not read default hyperparameters: {e}")

        # Override hyperparameters if configured
        if clustering_threshold > 0.0:
            pipeline.clustering.threshold = clustering_threshold
            logger.info(f"Overriding clustering.threshold → {clustering_threshold}")
        if min_cluster_size > 0:
            pipeline.clustering.min_cluster_size = min_cluster_size
            logger.info(f"Overriding clustering.min_cluster_size → {min_cluster_size}")

        _diarization_pipeline = pipeline
        logger.info("Diarization pipeline loaded")
        return _diarization_pipeline


def _load_embedding_model(model_name: str, device: str, hf_token: str):
    """Load speaker embedding model as a singleton (thread-safe, double-check pattern).

    Uses pyannote's Inference wrapper around wespeaker-voxceleb-resnet34-LM.
    The model is already cached locally (~25MB) since pyannote uses it internally.
    """
    global _embedding_inference
    if _embedding_inference is not None:
        return _embedding_inference
    with _embedding_lock:
        if _embedding_inference is not None:
            return _embedding_inference
        logger.info(f"Loading speaker embedding model: {model_name} on {device}...")
        from pyannote.audio import Model, Inference
        import os
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token
        model = Model.from_pretrained(model_name)
        model.to(torch.device(device))
        _embedding_inference = Inference(model, window="whole")
        logger.info("Speaker embedding model loaded")
        return _embedding_inference


def _extract_cluster_embeddings(inference, audio_path: Path, speakers: list, min_duration: float):
    """Extract speaker embeddings for each diarization segment and compute cluster centroids.

    Args:
        inference: pyannote Inference instance for embedding extraction.
        audio_path: Path to the audio file.
        speakers: List of diarization segments [{"speaker", "start", "end"}, ...].
        min_duration: Minimum segment duration (seconds) for embedding extraction.

    Returns:
        (cluster_centroids, cluster_embeddings, short_segments)
        - cluster_centroids: {speaker_label: L2-normalized centroid array}
        - cluster_embeddings: {speaker_label: [(embedding, duration, seg_index), ...]}
        - short_segments: [(seg_index, speaker_label)] for segments below min_duration
    """
    from pyannote.core import Segment

    cluster_embeddings = {}  # speaker -> [(embedding, duration, seg_index)]
    short_segments = []      # (seg_index, speaker_label)

    for i, seg in enumerate(speakers):
        duration = seg["end"] - seg["start"]
        if duration < min_duration:
            short_segments.append((i, seg["speaker"]))
            continue
        try:
            embedding = inference.crop(str(audio_path), Segment(seg["start"], seg["end"]))
            emb = np.array(embedding).flatten()
            if emb.shape[0] == 0:
                short_segments.append((i, seg["speaker"]))
                continue
            speaker = seg["speaker"]
            if speaker not in cluster_embeddings:
                cluster_embeddings[speaker] = []
            cluster_embeddings[speaker].append((emb, duration, i))
        except Exception as e:
            logger.warning(f"Embedding extraction failed for segment {i} [{seg['start']:.2f}-{seg['end']:.2f}]: {e}")
            short_segments.append((i, seg["speaker"]))

    # Compute duration-weighted centroids per cluster
    cluster_centroids = {}
    for speaker, emb_list in cluster_embeddings.items():
        if not emb_list:
            continue
        weighted_sum = np.zeros_like(emb_list[0][0])
        total_weight = 0.0
        for emb, duration, _ in emb_list:
            weighted_sum += emb * duration
            total_weight += duration
        centroid = weighted_sum / total_weight
        # L2-normalize
        norm = np.linalg.norm(centroid)
        if norm > 1e-8:
            centroid = centroid / norm
        cluster_centroids[speaker] = centroid

    return cluster_centroids, cluster_embeddings, short_segments


def _merge_similar_clusters(cluster_centroids: dict, cluster_embeddings: dict, merge_threshold: float):
    """Greedily merge clusters whose centroids exceed the similarity threshold.

    Returns:
        merge_map: {old_label: new_label} for all merged speakers.
    """
    merge_map = {}
    labels = list(cluster_centroids.keys())

    while True:
        best_sim = -1.0
        best_pair = None

        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a, b = labels[i], labels[j]
                sim = float(np.dot(cluster_centroids[a], cluster_centroids[b]))
                if sim > best_sim:
                    best_sim = sim
                    best_pair = (a, b)

        if best_sim < merge_threshold or best_pair is None:
            break

        a, b = best_pair
        # Merge smaller cluster (by total duration) into larger
        dur_a = sum(d for _, d, _ in cluster_embeddings[a])
        dur_b = sum(d for _, d, _ in cluster_embeddings[b])
        if dur_a >= dur_b:
            keep, discard = a, b
        else:
            keep, discard = b, a

        logger.info(f"Merged {discard} into {keep} (similarity: {best_sim:.2f})")
        merge_map[discard] = keep

        # Move embeddings from discard to keep
        cluster_embeddings[keep].extend(cluster_embeddings[discard])
        del cluster_embeddings[discard]

        # Recompute centroid for merged cluster
        weighted_sum = np.zeros_like(cluster_centroids[keep])
        total_weight = 0.0
        for emb, duration, _ in cluster_embeddings[keep]:
            weighted_sum += emb * duration
            total_weight += duration
        centroid = weighted_sum / total_weight
        norm = np.linalg.norm(centroid)
        if norm > 1e-8:
            centroid = centroid / norm
        cluster_centroids[keep] = centroid
        del cluster_centroids[discard]

        labels.remove(discard)

    # Resolve transitive merges (A→B, B→C becomes A→C, B→C)
    for label in list(merge_map.keys()):
        target = merge_map[label]
        while target in merge_map:
            target = merge_map[target]
        merge_map[label] = target

    return merge_map


def _reassign_outlier_segments(cluster_centroids: dict, cluster_embeddings: dict,
                               speakers: list, short_segments: list,
                               outlier_threshold: float, reassign_threshold: float,
                               reassign_margin: float):
    """Reassign segments that are poorly matched to their cluster.

    For segments with embeddings: check similarity to own centroid, reassign if
    a better cluster is found above the threshold with sufficient margin.

    For short segments (no embedding): inherit speaker from nearest segment
    that has an embedding.

    Returns:
        Updated speakers list (modified in place).
    """
    reassigned_count = 0
    inherited_count = 0

    # Build index of segment embeddings for quick lookup
    seg_embeddings = {}  # seg_index -> embedding
    for speaker, emb_list in cluster_embeddings.items():
        for emb, duration, seg_idx in emb_list:
            seg_embeddings[seg_idx] = emb

    # Phase 1: Reassign outlier segments with embeddings
    for seg_idx, emb in seg_embeddings.items():
        current_speaker = speakers[seg_idx]["speaker"]
        if current_speaker not in cluster_centroids:
            continue

        own_sim = float(np.dot(emb, cluster_centroids[current_speaker]))
        if own_sim >= outlier_threshold:
            continue

        # This segment is an outlier — check other clusters
        best_speaker = current_speaker
        best_sim = own_sim
        for other_speaker, other_centroid in cluster_centroids.items():
            if other_speaker == current_speaker:
                continue
            sim = float(np.dot(emb, other_centroid))
            if sim > best_sim:
                best_sim = sim
                best_speaker = other_speaker

        if (best_speaker != current_speaker
                and best_sim >= reassign_threshold
                and best_sim - own_sim >= reassign_margin):
            logger.info(
                f"Reassigned segment {seg_idx} [{speakers[seg_idx]['start']:.2f}-{speakers[seg_idx]['end']:.2f}] "
                f"from {current_speaker} to {best_speaker} "
                f"(own_sim={own_sim:.2f}, best_sim={best_sim:.2f})"
            )
            speakers[seg_idx]["speaker"] = best_speaker
            reassigned_count += 1

    # Phase 2: Inherit speaker for short segments from nearest segment with embedding
    # Build list of segments that have embeddings (sorted by time)
    embedded_segments = []
    for seg_idx in seg_embeddings:
        mid = (speakers[seg_idx]["start"] + speakers[seg_idx]["end"]) / 2
        embedded_segments.append((mid, seg_idx))
    embedded_segments.sort(key=lambda x: x[0])

    for seg_idx, original_speaker in short_segments:
        seg_mid = (speakers[seg_idx]["start"] + speakers[seg_idx]["end"]) / 2
        # Find nearest embedded segment by time
        best_dist = float("inf")
        best_speaker = original_speaker
        for emb_mid, emb_idx in embedded_segments:
            dist = abs(seg_mid - emb_mid)
            if dist < best_dist:
                best_dist = dist
                best_speaker = speakers[emb_idx]["speaker"]
        if best_speaker != original_speaker:
            speakers[seg_idx]["speaker"] = best_speaker
            inherited_count += 1

    return reassigned_count, inherited_count


def verify_speaker_clusters(audio_path: Path, speakers: list, config) -> tuple:
    """Post-diarization verification using speaker embeddings.

    Orchestrates: embedding extraction → cluster merging → outlier reassignment → relabeling.

    Args:
        audio_path: Path to audio file.
        speakers: List of diarization segments (modified in place).
        config: PreprocessConfig instance.

    Returns:
        (speakers, verification_stats) tuple.
    """
    t0 = time.time()
    unique_before = set(s["speaker"] for s in speakers)
    original_count = len(unique_before)

    # Fast path: single cluster — nothing to verify
    if original_count <= 1:
        stats = {
            "original_clusters": original_count,
            "merged_clusters": 0,
            "final_clusters": original_count,
            "segments_reassigned": 0,
            "short_segments_inherited": 0,
            "processing_time_seconds": round(time.time() - t0, 2),
        }
        return speakers, stats

    # Load embedding model
    inference = _load_embedding_model(
        config.DIARIZATION_EMBEDDING_MODEL,
        config.DIARIZATION_DEVICE,
        config.HF_AUTH_TOKEN,
    )

    # Extract embeddings + compute centroids
    cluster_centroids, cluster_embeddings, short_segments = _extract_cluster_embeddings(
        inference, audio_path, speakers, config.DIARIZATION_MIN_EMBED_DURATION,
    )

    if not cluster_centroids:
        logger.warning("No embeddings extracted (all segments too short) — skipping verification")
        stats = {
            "original_clusters": original_count,
            "merged_clusters": 0,
            "final_clusters": original_count,
            "segments_reassigned": 0,
            "short_segments_inherited": 0,
            "processing_time_seconds": round(time.time() - t0, 2),
        }
        return speakers, stats

    # Phase A: Merge similar clusters
    merge_map = _merge_similar_clusters(
        cluster_centroids, cluster_embeddings, config.DIARIZATION_MERGE_THRESHOLD,
    )
    merged_count = len(merge_map)

    # Apply merge map to all speakers
    if merge_map:
        for seg in speakers:
            if seg["speaker"] in merge_map:
                seg["speaker"] = merge_map[seg["speaker"]]

    # Phase B: Reassign outlier segments
    reassigned, inherited = _reassign_outlier_segments(
        cluster_centroids, cluster_embeddings, speakers, short_segments,
        config.DIARIZATION_OUTLIER_THRESHOLD,
        config.DIARIZATION_REASSIGN_THRESHOLD,
        config.DIARIZATION_REASSIGN_MARGIN,
    )

    # Relabel sequentially by first appearance
    label_map = {}
    next_id = 0
    for seg in speakers:
        old = seg["speaker"]
        if old not in label_map:
            label_map[old] = f"SPEAKER_{next_id:02d}"
            next_id += 1
        seg["speaker"] = label_map[old]

    unique_after = set(s["speaker"] for s in speakers)
    elapsed = round(time.time() - t0, 2)

    logger.info(
        f"Speaker verification complete: {original_count} → {len(unique_after)} clusters "
        f"({merged_count} merged, {reassigned} reassigned, {inherited} inherited) in {elapsed}s"
    )

    # Carry the FINAL centroids (post-merge, post-relabel) forward via the
    # stats dict, which becomes diarization_result["verification"]. The dict
    # version uses Python lists so the centroids survive JSON serialization
    # between Type 6 (writes diarization_result.json to R2) and Type 5
    # (downloads + reads it). _verify_segment_speakers re-hydrates to numpy
    # arrays before computing cosine sims.
    final_centroids: dict = {}
    for old_label, centroid in cluster_centroids.items():
        new_label = label_map.get(old_label)
        if new_label is not None:
            final_centroids[new_label] = centroid
    centroids_serialized = _centroids_to_lists(final_centroids)

    stats = {
        "original_clusters": original_count,
        "merged_clusters": merged_count,
        "final_clusters": len(unique_after),
        "segments_reassigned": reassigned,
        "short_segments_inherited": inherited,
        "processing_time_seconds": elapsed,
        "cluster_centroids": centroids_serialized,
    }
    return speakers, stats


def _detect_multichannel(audio_path: Path) -> bool:
    """
    Check if audio is stereo with different content per channel.

    If the left and right channels have low cross-correlation (< 0.9),
    they likely contain separate speakers (e.g., telephony recording
    where each speaker is on their own channel).

    Returns True if channels are sufficiently different to use channel-based diarization.
    """
    info = sf.info(str(audio_path))
    if info.channels < 2:
        return False

    audio, sr = sf.read(str(audio_path), dtype="float32")
    if audio.ndim < 2 or audio.shape[1] < 2:
        return False

    left = audio[:, 0]
    right = audio[:, 1]

    # Normalize to avoid division by zero
    left_norm = np.linalg.norm(left)
    right_norm = np.linalg.norm(right)
    if left_norm < 1e-8 or right_norm < 1e-8:
        return False

    # Cross-correlation at zero lag
    correlation = np.dot(left, right) / (left_norm * right_norm)
    logger.info(f"Multichannel cross-correlation: {correlation:.4f}")

    return correlation < 0.9


def _diarize_multichannel(audio_path: Path) -> dict:
    """
    Channel-based diarization for stereo audio with separate speakers per channel.

    Assigns SPEAKER_00 to left channel, SPEAKER_01 to right channel.
    Uses energy-based VAD per channel to find speech regions.
    """
    audio, sr = sf.read(str(audio_path), dtype="float32")
    left = audio[:, 0]
    right = audio[:, 1]
    duration = len(left) / sr

    speakers = []
    # Simple energy-based segmentation per channel
    frame_size = int(0.5 * sr)  # 500ms frames
    for ch_idx, (channel, speaker_id) in enumerate([(left, "SPEAKER_00"), (right, "SPEAKER_01")]):
        num_frames = len(channel) // frame_size
        in_speech = False
        seg_start = 0.0

        for i in range(num_frames):
            frame = channel[i * frame_size:(i + 1) * frame_size]
            energy = np.sqrt(np.mean(frame ** 2))
            is_speech = energy > 0.01  # Simple energy threshold

            t = i * 0.5  # Time in seconds

            if is_speech and not in_speech:
                seg_start = t
                in_speech = True
            elif not is_speech and in_speech:
                speakers.append({
                    "speaker": speaker_id,
                    "start": round(seg_start, 2),
                    "end": round(t, 2),
                })
                in_speech = False

        # Close any open segment
        if in_speech:
            speakers.append({
                "speaker": speaker_id,
                "start": round(seg_start, 2),
                "end": round(duration, 2),
            })

    # Sort by start time
    speakers.sort(key=lambda s: s["start"])

    unique_speakers = set(s["speaker"] for s in speakers)
    return {
        "speakers": speakers,
        "num_speakers": len(unique_speakers),
        "method": "multichannel",
    }


def _snap_boundaries_to_silence(speakers: list, audio_path: Path, sr: int = 16000,
                                 window_sec: float = 2.0, frame_ms: int = 25) -> int:
    """Snap diarization boundaries to the nearest silence gap.

    Real speaker changes have a brief pause — pyannote's boundary often lands
    mid-word instead of at the actual pause. For each boundary between two
    different speakers, load audio around the boundary, compute short-time
    energy, and snap to the lowest-energy point.

    Args:
        speakers: List of diarization segments (modified in place).
        audio_path: Path to audio file.
        sr: Sample rate of audio.
        window_sec: How far (seconds) around each boundary to search.
        frame_ms: Frame size in milliseconds for energy computation.

    Returns:
        Number of boundaries snapped.
    """
    audio, file_sr = sf.read(str(audio_path), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if file_sr != sr:
        sr = file_sr

    frame_size = int(sr * frame_ms / 1000)
    snapped = 0

    for i in range(1, len(speakers)):
        if speakers[i]["speaker"] == speakers[i - 1]["speaker"]:
            continue

        boundary = speakers[i]["start"]
        search_start = max(0, boundary - window_sec)
        search_end = min(len(audio) / sr, boundary + window_sec)

        start_sample = int(search_start * sr)
        end_sample = int(search_end * sr)
        segment = audio[start_sample:end_sample]

        if len(segment) < frame_size * 2:
            continue

        num_frames = len(segment) // frame_size
        energies = np.zeros(num_frames)
        for f in range(num_frames):
            frame = segment[f * frame_size:(f + 1) * frame_size]
            energies[f] = np.sqrt(np.mean(frame ** 2))

        min_frame = int(np.argmin(energies))
        snap_time = round(search_start + (min_frame * frame_ms / 1000), 3)

        if abs(snap_time - boundary) < 0.05:
            continue

        speakers[i - 1]["end"] = round(snap_time, 2)
        speakers[i]["start"] = round(snap_time, 2)
        snapped += 1
        logger.info(f"  Snapped boundary {boundary:.2f}s → {snap_time:.2f}s "
                    f"(delta={snap_time - boundary:+.2f}s, "
                    f"{speakers[i-1]['speaker']}→{speakers[i]['speaker']})")

    if snapped:
        logger.info(f"Snapped {snapped} boundaries to silence gaps")
    else:
        logger.info("No boundaries snapped (all already at silence)")

    return snapped


def _run_pyannote_inprocess(audio_path: Path, config, num_speakers: int) -> tuple[list, float]:
    """In-process pyannote 3.1 diarization (current default path).

    Returns (speakers_list, elapsed_seconds). NO post-processing — that's
    done by _finalize_speakers() which is shared with the service path.

    Body lifted verbatim from the pre-migration diarize_audio() lines
    607–663: pipeline load + invocation + 3.1/4.0 Annotation shim +
    speaker-list construction + diagnostic turn logging.
    """
    t0 = time.time()
    logger.info("Running pyannote speaker diarization (in-process)...")
    pipeline = _load_diarization_pipeline(
        config.DIARIZATION_MODEL,
        config.DIARIZATION_DEVICE,
        config.HF_AUTH_TOKEN,
        clustering_threshold=getattr(config, 'DIARIZATION_CLUSTERING_THRESHOLD', 0.0),
        min_cluster_size=getattr(config, 'DIARIZATION_MIN_CLUSTER_SIZE', 0),
    )

    # Build pipeline kwargs
    kwargs = {}
    if num_speakers > 0:
        kwargs["num_speakers"] = num_speakers
    else:
        kwargs["min_speakers"] = config.DIARIZATION_MIN_SPEAKERS
        kwargs["max_speakers"] = config.DIARIZATION_MAX_SPEAKERS

    diarization_output = pipeline(str(audio_path), **kwargs)

    # pyannote 4.0 returns DiarizeOutput, 3.x returns Annotation directly
    if hasattr(diarization_output, 'speaker_diarization'):
        diarization = diarization_output.speaker_diarization
    else:
        diarization = diarization_output

    # Extract speaker segments from pyannote Annotation object
    speakers = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        speakers.append({
            "speaker": speaker,
            "start": round(turn.start, 2),
            "end": round(turn.end, 2),
        })

    unique_speakers = set(s["speaker"] for s in speakers)
    elapsed = round(time.time() - t0, 2)
    logger.info(f"Diarization complete: {len(unique_speakers)} speakers, {len(speakers)} segments, {elapsed}s")

    # ── Diagnostic: log every pyannote turn ──
    logger.info("=== Pyannote raw turns ===")
    for i, s in enumerate(speakers):
        dur = round(s["end"] - s["start"], 2)
        flag = ""
        if dur > 2.5:
            flag = " [LONG — possible missed speaker change]"
        logger.info(f"  Turn {i}: {s['speaker']} [{s['start']:.2f}s - {s['end']:.2f}s] ({dur:.2f}s){flag}")

    # Flag rapid speaker transitions (gap < 0.5s between different speakers)
    for i in range(1, len(speakers)):
        prev = speakers[i - 1]
        curr = speakers[i]
        if prev["speaker"] != curr["speaker"]:
            gap = round(curr["start"] - prev["end"], 2)
            if gap < 0.5:
                logger.info(f"  Rapid transition: {prev['speaker']}→{curr['speaker']} gap={gap:.2f}s "
                            f"at {prev['end']:.2f}s")
    logger.info("=== End pyannote turns ===")

    return speakers, elapsed


def _diarize_via_service(
    audio_path: Path,
    num_speakers: int,
    config,
) -> tuple[list, float]:
    """HTTP call to the pyannote 4.0 community-1 microservice on port 8012.

    Returns (speakers_list, elapsed_seconds) — same shape as
    _run_pyannote_inprocess so the dispatcher can swap them transparently.

    Failure modes and what diarize_audio() does with each:
      - ConnectError / ConnectTimeout / HTTPStatusError → fall back to
        in-process 3.1 fast. These mean "service is down or broken."
      - ReadTimeout → DO NOT fall back. The server IS doing the work; a
        long read just means diarization is slow. Falling back would
        double the wall clock (we waited N seconds for nothing, then we
        run the same job on the slower in-process path). Re-raise so the
        caller surfaces it as a real failure instead of silently regressing.
      - PoolTimeout / WriteTimeout → re-raise (genuine local-side bugs).
    """
    payload = {
        "audio_path": str(audio_path),
        "num_speakers": num_speakers,
        "clustering_threshold": getattr(config, 'DIARIZATION_CLUSTERING_THRESHOLD', 0.0),
        "min_cluster_size": getattr(config, 'DIARIZATION_MIN_CLUSTER_SIZE', 0),
        "min_speakers": config.DIARIZATION_MIN_SPEAKERS,
        "max_speakers": config.DIARIZATION_MAX_SPEAKERS,
    }
    service_url = config.DIARIZATION_SERVICE_URL
    connect_timeout = getattr(config, 'DIARIZATION_SERVICE_CONNECT_TIMEOUT_S', 10.0)
    read_timeout = getattr(config, 'DIARIZATION_SERVICE_READ_TIMEOUT_S', 3600.0)
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=connect_timeout,
        pool=connect_timeout,
    )

    logger.info(
        "Calling diarization service at %s (connect=%.1fs read=%.0fs) ...",
        service_url, connect_timeout, read_timeout,
    )
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{service_url}/diarize", json=payload)
    response.raise_for_status()
    result = response.json()

    speakers = [
        {
            "speaker": s["speaker"],
            "start": round(float(s["start"]), 2),
            "end": round(float(s["end"]), 2),
        }
        for s in result.get("speakers", [])
    ]
    elapsed = float(result.get("processing_time_seconds", 0))
    method = result.get("method", "pyannote-4.0-community-1")
    logger.info(
        "Diarization service returned in %.1fs (%d segs, %d speakers, method=%s)",
        elapsed, len(speakers), result.get("num_speakers", 0), method,
    )

    # Reuse the existing diagnostic turn logging from the in-process path
    logger.info("=== Pyannote raw turns (service) ===")
    for i, s in enumerate(speakers):
        dur = round(s["end"] - s["start"], 2)
        flag = " [LONG — possible missed speaker change]" if dur > 2.5 else ""
        logger.info(f"  Turn {i}: {s['speaker']} [{s['start']:.2f}s - {s['end']:.2f}s] ({dur:.2f}s){flag}")
    for i in range(1, len(speakers)):
        prev = speakers[i - 1]
        curr = speakers[i]
        if prev["speaker"] != curr["speaker"]:
            gap = round(curr["start"] - prev["end"], 2)
            if gap < 0.5:
                logger.info(
                    f"  Rapid transition: {prev['speaker']}→{curr['speaker']} "
                    f"gap={gap:.2f}s at {prev['end']:.2f}s"
                )
    logger.info("=== End pyannote turns (service) ===")

    return speakers, elapsed


def _finalize_speakers(audio_path: Path, speakers: list, config) -> tuple[list, dict | None]:
    """Apply post-pyannote patches shared by both in-process and service providers.

    1. Speaker embedding verification (if DIARIZATION_VERIFY_SPEAKERS)
    2. Snap boundaries to silence
    3. Fill gaps in diarization timeline

    Lifted verbatim from the pre-migration diarize_audio() lines 665–695.
    Returns (speakers, verification_stats). speakers is modified in place.
    """
    # ── Speaker Embedding Verification (post-diarization cleanup) ──
    verification_stats = None
    if getattr(config, 'DIARIZATION_VERIFY_SPEAKERS', False):
        logger.info("=== Speaker Embedding Verification ===")
        try:
            speakers, verification_stats = verify_speaker_clusters(audio_path, speakers, config)
        except Exception as e:
            logger.error(f"Speaker verification failed (keeping original labels): {e}")

    # ── Snap boundaries to silence (Fix #2) ──
    if len(speakers) > 1:
        logger.info("=== VAD Boundary Snapping ===")
        _snap_boundaries_to_silence(speakers, audio_path)

    # ── Fill gaps in diarization timeline ──
    # Pyannote leaves small gaps between speaker turns. Fill them by extending
    # the nearest segment's boundary so every moment has a speaker assigned.
    if len(speakers) > 1:
        filled = 0
        for i in range(1, len(speakers)):
            prev = speakers[i - 1]
            curr = speakers[i]
            gap = curr["start"] - prev["end"]
            if gap > 0 and gap < 2.0:
                midpoint = prev["end"] + gap / 2
                prev["end"] = round(midpoint, 2)
                curr["start"] = round(midpoint, 2)
                filled += 1
        if filled:
            logger.info(f"Filled {filled} gaps in diarization timeline")

    return speakers, verification_stats


def diarize_audio(audio_path: Path, config, num_speakers: int = 0) -> dict:
    """
    Run speaker diarization on audio file.

    Args:
        audio_path: Path to preprocessed audio (16kHz mono WAV).
        config: PreprocessConfig instance.
        num_speakers: 0 = auto-detect, >0 = force exact speaker count.

    Returns:
        {
            "speakers": [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.2},
                {"speaker": "SPEAKER_01", "start": 5.2, "end": 12.8},
                ...
            ],
            "num_speakers": 2,
            "method": "pyannote" | "pyannote-4.0-community-1" | "multichannel",
            "processing_time_seconds": 3.5
        }

    Dispatch behavior:
      - Multichannel audio  → _diarize_multichannel (fully bypasses pyannote)
      - DIARIZATION_PROVIDER=service → _diarize_via_service (pyannote 4.0)
        Falls back to _run_pyannote_inprocess on HTTP errors.
      - Otherwise → _run_pyannote_inprocess (current 3.1 default)

    Post-processing (verification + boundary snap + gap fill) is shared
    across both providers via _finalize_speakers — quality patches stay
    identical regardless of which pyannote ran.
    """
    t0 = time.time()

    # 1. Multichannel shortcut — unchanged, fully bypasses pyannote and patches
    if _detect_multichannel(audio_path):
        logger.info("Multichannel detected — using channel-based diarization")
        result = _diarize_multichannel(audio_path)
        result["processing_time_seconds"] = round(time.time() - t0, 2)
        return result

    # 2. Provider dispatch — pick the pyannote backend
    use_service = (
        getattr(config, "DIARIZATION_PROVIDER", "inprocess") == "service"
        and bool(getattr(config, "DIARIZATION_SERVICE_URL", None))
    )

    method = "pyannote"
    speakers: list = []
    pyannote_elapsed: float = 0.0
    if use_service:
        try:
            speakers, pyannote_elapsed = _diarize_via_service(audio_path, num_speakers, config)
            method = "pyannote-4.0-community-1"
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPStatusError) as e:
            # Service is unreachable or returned an HTTP error code — fast-fail
            # signals where falling back to in-process 3.1 is the right move.
            logger.warning(
                "Diarization service unreachable (%s: %s) — falling back to in-process 3.1",
                type(e).__name__, e,
            )
            speakers, pyannote_elapsed = _run_pyannote_inprocess(audio_path, config, num_speakers)
            method = "pyannote"
        # NOTE: httpx.ReadTimeout / PoolTimeout / WriteTimeout deliberately
        # NOT caught here. Read timeout means the service IS processing —
        # falling back would double wall clock. Let it propagate.
    else:
        speakers, pyannote_elapsed = _run_pyannote_inprocess(audio_path, config, num_speakers)

    # 3. Shared post-pyannote patches (verification + snap + gap fill)
    speakers, verification_stats = _finalize_speakers(audio_path, speakers, config)

    unique_speakers = set(s["speaker"] for s in speakers)
    total_elapsed = round(time.time() - t0, 2)
    logger.info(
        f"Diarize total: {len(unique_speakers)} speakers, {len(speakers)} segments, "
        f"{total_elapsed}s (pyannote {pyannote_elapsed:.1f}s, method={method})"
    )

    return {
        "speakers": speakers,
        "num_speakers": len(unique_speakers),
        "method": method,
        "processing_time_seconds": total_elapsed,
        "verification": verification_stats,
    }


def _get_speaker_at(diar_segments: list, time_point: float) -> str:
    """Get the speaker label at a specific time point. Falls back to nearest speaker."""
    for ds in diar_segments:
        if ds["start"] <= time_point < ds["end"]:
            return ds["speaker"]
    # Fallback: find nearest diarization segment by time
    best = None
    best_dist = float("inf")
    for ds in diar_segments:
        dist = min(abs(ds["start"] - time_point), abs(ds["end"] - time_point))
        if dist < best_dist:
            best_dist = dist
            best = ds["speaker"]
    return best or "UNKNOWN"


def _find_speaker_changes(diar_segments: list, seg_start: float, seg_end: float) -> list:
    """
    Find diarization speaker turns that overlap with a Whisper segment.

    Returns list of (start, end, speaker) tuples covering the segment's time range.
    Adjacent turns with the same speaker are merged.
    Overlapping turns from different speakers are resolved by splitting at the midpoint.
    """
    turns = []
    for ds in diar_segments:
        overlap_start = max(seg_start, ds["start"])
        overlap_end = min(seg_end, ds["end"])
        if overlap_start < overlap_end:
            turns.append((overlap_start, overlap_end, ds["speaker"]))

    if not turns:
        return [(seg_start, seg_end, "UNKNOWN")]

    # Merge all turns per speaker FIRST — pyannote produces multiple short
    # sub-turns for the same speaker. Group-merge prevents a single
    # other-speaker overlap turn from eliminating all the sub-turns.
    from collections import defaultdict
    speaker_turns = defaultdict(list)
    for start, end, speaker in turns:
        speaker_turns[speaker].append((start, end))

    merged = []
    for speaker, intervals in speaker_turns.items():
        intervals.sort()
        cur_start, cur_end = intervals[0]
        for s, e in intervals[1:]:
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                merged.append((cur_start, cur_end, speaker))
                cur_start, cur_end = s, e
        merged.append((cur_start, cur_end, speaker))

    # Sort by start time
    merged.sort(key=lambda t: t[0])

    # Resolve overlapping turns from different speakers.
    # Trim N+1's start to N's end so turns never overlap.
    resolved = [merged[0]]
    for start, end, speaker in merged[1:]:
        prev_start, prev_end, prev_speaker = resolved[-1]
        if start < prev_end and speaker != prev_speaker:
            adjusted_start = prev_end
            if adjusted_start < end:
                resolved.append((adjusted_start, end, speaker))
        else:
            resolved.append((start, end, speaker))

    return resolved


def assign_speakers_to_segments(
    diarization_result: dict,
    whisper_segments: list,
    audio_path: Path = None,
    config=None,
) -> list:
    """
    Assign speaker labels to Whisper ASR segments by splitting at
    diarization speaker-change boundaries.

    If a Whisper segment spans multiple speaker turns, it gets split into
    separate segments — one per speaker turn. The text is distributed based
    on word timestamps (if available) or proportionally by duration.

    Args:
        diarization_result: Output from diarize_audio().
        whisper_segments: List of Whisper segment dicts with "start" and "end" keys.
        audio_path: Path to audio file (for word-level embedding verification).
        config: PreprocessConfig instance (for embedding model settings).

    Returns:
        New list of segments with "speaker" field, potentially more segments
        than the input if splits occurred.
    """
    diar_segments = diarization_result.get("speakers", [])
    if not diar_segments:
        for seg in whisper_segments:
            seg["speaker"] = "UNKNOWN"
        return whisper_segments

    result_segments = []

    logger.info("=== Speaker assignment decisions ===")
    for seg in whisper_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_text = seg.get("text", "")[:60]

        # Find all speaker turns within this segment
        turns = _find_speaker_changes(diar_segments, seg_start, seg_end)

        # Log the decision
        turn_summary = ", ".join(f"{spk}[{s:.1f}-{e:.1f}]" for s, e, spk in turns)
        if len(turns) == 1:
            logger.info(f"  Seg [{seg_start:.2f}-{seg_end:.2f}] → 1 turn ({turn_summary}) | \"{seg_text}\"")
        else:
            logger.info(f"  Seg [{seg_start:.2f}-{seg_end:.2f}] → {len(turns)} turns SPLIT ({turn_summary}) | \"{seg_text}\"")

        # Single speaker — no split needed
        if len(turns) == 1:
            seg["speaker"] = turns[0][2]
            result_segments.append(seg)
            continue

        # Multiple speakers — split the segment
        words = seg.get("words", [])

        if words:
            # Word-level timestamps available — split words by speaker turns
            turn_word_groups = []
            for turn_start, turn_end, speaker in turns:
                turn_words = []
                for w in words:
                    w_start = w.get("start", 0)
                    w_end = w.get("end", w_start)
                    w_mid = (w_start + w_end) / 2
                    if turn_start <= w_mid < turn_end:
                        turn_words.append(w)
                turn_word_groups.append((turn_start, turn_end, speaker, turn_words))

            # Fragment verification: when a split produces a tiny fragment
            # (< 3 words), verify with voice embeddings using a 2s audio window.
            # Reassign to the correct speaker if embedding says otherwise.
            MIN_SPLIT_WORDS = 3
            _has_fragments = any(0 < len(tw) < MIN_SPLIT_WORDS for _, _, _, tw in turn_word_groups)
            if _has_fragments:
                logger.info(f"  Fragment detected: audio_path={audio_path}, turns={len(turn_word_groups)}, words_per_turn={[len(tw) for _,_,_,tw in turn_word_groups]}")
            if _has_fragments and audio_path and len(turn_word_groups) >= 2:
                try:
                    from app.audio_preprocess.services.voice_embeddings import _get_classifier
                    import torchaudio
                    from scipy.spatial.distance import cosine as _cosine
                    import numpy as _np

                    logger.info(f"  Fragment verify: loading classifier and audio...")
                    _classifier = _get_classifier()
                    _waveform, _wav_sr = torchaudio.load(str(audio_path))
                    if _wav_sr != 16000:
                        _waveform = torchaudio.transforms.Resample(_wav_sr, 16000)(_waveform)
                        _wav_sr = 16000

                    def _get_emb_window(center_start, center_end, min_dur=2.0):
                        """Extract embedding from a window of at least min_dur seconds centered on the given range."""
                        mid = (center_start + center_end) / 2
                        half = max(min_dur / 2, (center_end - center_start) / 2)
                        s = max(0, int((mid - half) * _wav_sr))
                        e = min(_waveform.shape[1], int((mid + half) * _wav_sr))
                        w = _waveform[:, s:e]
                        if w.shape[1] < _wav_sr * 1.0:
                            return None
                        return _classifier.encode_batch(w).squeeze().cpu().numpy()

                    # Build centroids from the longer turns in this split
                    _speaker_embs = {}
                    for _ts, _te, _spk, _tw in turn_word_groups:
                        if len(_tw) >= MIN_SPLIT_WORDS:
                            _emb = _get_emb_window(_ts, _te, min_dur=2.0)
                            if _emb is not None:
                                _speaker_embs[_spk] = _emb

                    logger.info(f"  Fragment verify: built {len(_speaker_embs)} centroid(s) from {[(_s, len(_tw)) for _s, _, _, _tw in turn_word_groups if len(_tw) >= MIN_SPLIT_WORDS]}")
                    if len(_speaker_embs) >= 1:
                        for idx in range(len(turn_word_groups)):
                            _ts, _te, _spk, _tw = turn_word_groups[idx]
                            if not (0 < len(_tw) < MIN_SPLIT_WORDS):
                                continue

                            _frag_text = " ".join(w.get("word", "") for w in _tw)

                            # Get 2s window embedding centered on the fragment
                            _frag_start = _tw[0].get("start", _ts)
                            _frag_end = _tw[-1].get("end", _te)
                            _frag_emb = _get_emb_window(_frag_start, _frag_end, min_dur=2.0)
                            if _frag_emb is None:
                                logger.info(f"  Fragment verify: \"{_frag_text}\" — skipped (no embedding)")
                                continue

                            # Compare against all available centroids
                            _best_spk = _spk
                            _best_sim = 1 - _cosine(_frag_emb, _speaker_embs[_spk]) if _spk in _speaker_embs else -1
                            for _other_spk, _other_emb in _speaker_embs.items():
                                if _other_spk == _spk:
                                    continue
                                _other_sim = 1 - _cosine(_frag_emb, _other_emb)
                                if _other_sim > _best_sim:
                                    _best_sim = _other_sim
                                    _best_spk = _other_spk

                            logger.info(f"  Fragment verify: \"{_frag_text}\" ({_spk}) own={1 - _cosine(_frag_emb, _speaker_embs[_spk]) if _spk in _speaker_embs else -1:.2f}, best={_best_sim:.2f} → {_best_spk}")

                            _FRAGMENT_MIN_SIMILARITY = 0.45
                            if _best_spk != _spk and _best_sim >= _FRAGMENT_MIN_SIMILARITY:
                                for _tidx in range(len(turn_word_groups)):
                                    if turn_word_groups[_tidx][2] == _best_spk and len(turn_word_groups[_tidx][3]) >= MIN_SPLIT_WORDS:
                                        _tts, _tte, _tspk, _ttw = turn_word_groups[_tidx]
                                        if _tidx < idx:
                                            turn_word_groups[_tidx] = (_tts, _tte, _tspk, _ttw + _tw)
                                        else:
                                            turn_word_groups[_tidx] = (_tts, _tte, _tspk, _tw + _ttw)
                                        turn_word_groups[idx] = (_ts, _te, _spk, [])
                                        logger.info(
                                            f"  Fragment verify: \"{_frag_text}\" reassigned "
                                            f"{_spk}→{_best_spk} (best={_best_sim:.2f})"
                                        )
                                        break
                except Exception as _e:
                    logger.warning(f"  Fragment verification failed (non-fatal): {_e}")

            for turn_start, turn_end, speaker, turn_words in turn_word_groups:
                if not turn_words:
                    continue

                turn_text = " ".join(w.get("word", "") for w in turn_words).strip()
                if not turn_text:
                    continue

                new_seg = {
                    **seg,
                    "start": round(turn_words[0].get("start", turn_start), 2),
                    "end": round(turn_words[-1].get("end", turn_end), 2),
                    "text": turn_text,
                    "words": turn_words,
                    "speaker": speaker,
                }
                result_segments.append(new_seg)
        else:
            # No word timestamps — split text proportionally by duration
            seg_duration = seg_end - seg_start
            if seg_duration <= 0:
                seg["speaker"] = turns[0][2]
                result_segments.append(seg)
                continue

            full_text = seg.get("text", "")
            text_words = full_text.split()
            total_words = len(text_words)

            # Build proportional word groups
            turn_text_groups = []
            word_idx = 0
            for turn_start, turn_end, speaker in turns:
                turn_duration = turn_end - turn_start
                turn_ratio = turn_duration / seg_duration
                n_words = max(1, round(total_words * turn_ratio))
                tw = text_words[word_idx:word_idx + n_words]
                word_idx += n_words
                turn_text_groups.append((turn_start, turn_end, speaker, tw))

            # Handle remaining words
            if word_idx < total_words and turn_text_groups:
                ts, te, spk, tw = turn_text_groups[-1]
                turn_text_groups[-1] = (ts, te, spk, tw + text_words[word_idx:])

            # Commented: fragment absorption for proportional splits
            # (replaced by _merge_short_segments after segment-level verification)

            for turn_start, turn_end, speaker, turn_text_words in turn_text_groups:
                if not turn_text_words:
                    continue
                new_seg = {
                    **seg,
                    "start": round(turn_start, 2),
                    "end": round(turn_end, 2),
                    "text": " ".join(turn_text_words),
                    "speaker": speaker,
                }
                new_seg.pop("words", None)
                result_segments.append(new_seg)

    # Re-sort by start time and re-number IDs
    result_segments.sort(key=lambda s: s["start"])

    split_count = len(result_segments) - len(whisper_segments)
    if split_count > 0:
        logger.info(
            f"Speaker assignment: split {len(whisper_segments)} segments into "
            f"{len(result_segments)} ({split_count} splits at speaker changes)"
        )

    # Commented: fragment realignment (replaced by segment-level verification)
    # result_segments = _realign_speaker_boundaries(result_segments)

    # Commented: word-level embedding verification (replaced by segment-level)
    # if audio_path and config:
    #     logger.info("=== Word-Level Boundary Verification ===")
    #     try:
    #         result_segments = _verify_boundary_words(
    #             audio_path, result_segments, diarization_result, config,
    #         )
    #         result_segments = _realign_speaker_boundaries(result_segments)
    #     except Exception as e:
    #         logger.warning(f"Boundary word verification failed: {e}")

    # Segment-level embedding verification — checks ALL segments against
    # cluster centroids. Catches both misplaced and missing pyannote boundaries.
    if audio_path and config:
        logger.info("=== Segment-Level Speaker Verification ===")
        try:
            result_segments = _verify_segment_speakers(
                audio_path, result_segments, diarization_result, config,
            )
        except Exception as e:
            logger.warning(f"Segment speaker verification failed (keeping current assignments): {e}")

    # Merge short orphan segments (< 1.5s) into adjacent segments
    result_segments = _merge_short_segments(result_segments)

    # Capitalize first letter of each segment
    for seg in result_segments:
        text = seg.get("text", "")
        if text and text[0].islower():
            seg["text"] = text[0].upper() + text[1:]

    for idx, seg in enumerate(result_segments):
        seg["id"] = idx

    return result_segments


def _embed_window(inference, audio_path: Path, start: float, end: float, audio_duration: float):
    """Extract one L2-normalized wespeaker embedding over [start, end].

    Returns the 1D numpy array, or None if extraction fails (e.g., window
    too short or pyannote raises on the slice).
    """
    from pyannote.core import Segment
    s = max(0.0, start)
    e = min(audio_duration, end)
    if e - s < 0.5:
        return None
    try:
        emb = inference.crop(str(audio_path), Segment(s, e))
        emb = np.array(emb).flatten()
        if emb.shape[0] == 0:
            return None
        norm = np.linalg.norm(emb)
        if norm > 1e-8:
            emb = emb / norm
        return emb
    except Exception:
        return None


def _score_against_centroids(emb, centroids: dict, own_speaker: str) -> tuple[float, str, float]:
    """Score an embedding against all cluster centroids.

    Returns (own_sim, best_other_speaker, best_other_sim). If own_speaker
    isn't in the centroids dict, own_sim is -inf so callers can detect.
    """
    own_sim = float(np.dot(emb, centroids[own_speaker])) if own_speaker in centroids else float("-inf")
    best_other_spk = own_speaker
    best_other_sim = float("-inf")
    for spk, centroid in centroids.items():
        if spk == own_speaker:
            continue
        sim = float(np.dot(emb, centroid))
        if sim > best_other_sim:
            best_other_sim = sim
            best_other_spk = spk
    return own_sim, best_other_spk, best_other_sim


def _split_segment_at_times(seg: dict, splits: list) -> list:
    """Split one ASR segment into multiple pieces at the given (time, new_speaker)
    pairs. Word-aware: each piece contains the words whose start time falls
    within its [start, end) range. Pieces inherit non-time/non-speaker keys
    from the parent (e.g., language, logprob).

    Args:
        seg: original ASR segment with at least keys speaker / start / end /
             text / words. words is a list of {start, end, word} dicts.
        splits: list of (split_time, new_speaker) tuples, will be processed
                in time order. Splits with no words on either side are skipped.

    Returns:
        List of segment dicts (>=1 entry). If no valid splits, returns [seg]
        unchanged.
    """
    if not splits or not seg.get("words"):
        return [seg]

    splits = sorted(splits, key=lambda x: x[0])
    pieces: list = []
    cur_start = seg["start"]
    cur_speaker = seg["speaker"]
    remaining_words = list(seg["words"])

    base_keys = {k: v for k, v in seg.items() if k not in ("start", "end", "speaker", "words", "text")}

    for split_t, new_spk in splits:
        # cut_idx = first word index whose start >= split_t (the "after" side)
        cut_idx = next((i for i, w in enumerate(remaining_words) if w.get("start", 0) >= split_t), None)
        if cut_idx is None or cut_idx == 0 or cut_idx >= len(remaining_words):
            # Can't split here cleanly: no words after split point, or no words before
            continue
        before_words = remaining_words[:cut_idx]
        after_words = remaining_words[cut_idx:]
        before_text = " ".join(w.get("word", "") for w in before_words).strip()
        pieces.append({
            **base_keys,
            "speaker": cur_speaker,
            "start": cur_start,
            "end": before_words[-1].get("end", split_t),
            "words": before_words,
            "text": before_text,
        })
        cur_speaker = new_spk
        cur_start = after_words[0].get("start", split_t)
        remaining_words = after_words

    # Final tail piece (or original if no splits applied)
    if remaining_words:
        pieces.append({
            **base_keys,
            "speaker": cur_speaker,
            "start": cur_start,
            "end": seg["end"],
            "words": remaining_words,
            "text": " ".join(w.get("word", "") for w in remaining_words).strip(),
        })

    return pieces if pieces else [seg]


def _scan_long_segment_for_splits(
    seg: dict,
    inference,
    audio_path: Path,
    audio_duration: float,
    centroids: dict,
    cfg,
) -> list:
    """Probe a long segment at word-gap boundaries; return candidate splits.

    For each word-gap of ≥ DIARIZATION_WORD_GAP_MIN seconds inside the
    segment, take a DIARIZATION_LONG_SEG_WINDOW-second window centered on
    the gap, embed it, and score against centroids. If the window matches
    a different speaker strongly (strict thresholds), that gap is a split
    candidate. Returns the top DIARIZATION_MAX_SPLITS_PER_SEG by margin.
    """
    own_speaker = seg.get("speaker")
    if not own_speaker or own_speaker not in centroids:
        return []

    words = seg.get("words") or []
    if len(words) < 2:
        return []

    gap_min = getattr(cfg, "DIARIZATION_WORD_GAP_MIN", 0.10)
    win = getattr(cfg, "DIARIZATION_LONG_SEG_WINDOW", 2.0)
    outlier = getattr(cfg, "DIARIZATION_OUTLIER_STRICT", 0.55)
    reassign = getattr(cfg, "DIARIZATION_REASSIGN_STRICT", 0.60)
    margin_min = getattr(cfg, "DIARIZATION_MARGIN_STRICT", 0.25)
    max_splits = getattr(cfg, "DIARIZATION_MAX_SPLITS_PER_SEG", 2)
    debug = getattr(cfg, "DIARIZATION_LONG_SEG_DEBUG", True)

    # Find word-gap candidate times (mid-point of each gap)
    candidates: list = []
    for i in range(len(words) - 1):
        gap = words[i + 1].get("start", 0) - words[i].get("end", 0)
        if gap >= gap_min:
            split_t = words[i].get("end", 0) + gap / 2.0
            candidates.append((split_t, gap))

    if not candidates:
        if debug:
            logger.info(
                f"  Long-seg scan: [{seg['start']:.1f}-{seg['end']:.1f}] "
                f"({seg['end']-seg['start']:.1f}s, spk={own_speaker}) — "
                f"0 word-gaps ≥ {gap_min*1000:.0f}ms found in {len(words)} words"
            )
        return []

    if debug:
        logger.info(
            f"  Long-seg scan: [{seg['start']:.1f}-{seg['end']:.1f}] "
            f"({seg['end']-seg['start']:.1f}s, spk={own_speaker}) — "
            f"{len(candidates)} word-gap candidates ≥ {gap_min*1000:.0f}ms"
        )

    probed: list = []  # (margin, split_t, new_spk, own_sim, other_sim)
    for split_t, gap_size in candidates:
        emb = _embed_window(inference, audio_path, split_t - win / 2.0, split_t + win / 2.0, audio_duration)
        if emb is None:
            if debug:
                logger.info(f"    probe @ {split_t:.2f}s (gap={gap_size*1000:.0f}ms): embed FAILED")
            continue
        own_sim, other_spk, other_sim = _score_against_centroids(emb, centroids, own_speaker)
        passes = own_sim < outlier and other_sim > reassign and (other_sim - own_sim) >= margin_min
        if debug:
            verdict = "SPLIT-CANDIDATE" if passes else "ok"
            reasons = []
            if own_sim >= outlier:
                reasons.append(f"own_sim {own_sim:.2f}≥{outlier:.2f}")
            if other_sim <= reassign:
                reasons.append(f"other_sim {other_sim:.2f}≤{reassign:.2f}")
            if (other_sim - own_sim) < margin_min:
                reasons.append(f"margin {other_sim-own_sim:.2f}<{margin_min:.2f}")
            reason_str = f" ({', '.join(reasons)})" if reasons else ""
            logger.info(
                f"    probe @ {split_t:.2f}s (gap={gap_size*1000:.0f}ms): "
                f"own({own_speaker})={own_sim:.2f} other({other_spk})={other_sim:.2f} "
                f"margin={other_sim-own_sim:+.2f} → {verdict}{reason_str}"
            )
        if passes:
            probed.append((other_sim - own_sim, split_t, other_spk, own_sim, other_sim))

    if not probed:
        return []

    # Keep the top-K probes by margin
    probed.sort(key=lambda x: -x[0])
    selected = probed[:max_splits]

    # Log decisions for observability
    for margin, split_t, other_spk, own_sim, other_sim in selected:
        logger.info(
            f"  Long-seg SPLIT: [{seg['start']:.1f}-{seg['end']:.1f}] @ {split_t:.2f}s "
            f"→ {own_speaker}→{other_spk} (own={own_sim:.2f}, other={other_sim:.2f}, "
            f"margin={margin:.2f})"
        )

    return [(split_t, other_spk) for _margin, split_t, other_spk, _o, _b in selected]


def _verify_segment_speakers(
    audio_path: Path,
    segments: list,
    diarization_result: dict,
    config,
    min_duration: float = 1.5,
    suspicious_threshold: float = 0.5,
    reassign_margin: float = 0.15,
) -> list:
    """Verify speaker assignments at the segment level using embeddings.

    For each ASR segment, extracts a speaker embedding from the full segment
    audio (3-15s) and compares to cluster centroids. Unlike word-level
    verification which only checks near boundaries, this checks ALL segments
    and can catch cases where pyannote missed a speaker change entirely.

    Long-segment handling (May 11, post-pyannote 4.0 enhancement): for
    segments longer than DIARIZATION_LONG_SEG_THRESHOLD, this also runs a
    word-gap-anchored windowed scan to detect mid-segment speaker changes.
    Matching gaps split the segment into pieces, attributing each piece to
    the correct speaker. See plan/pyannote_4_migration.md §15.

    Args:
        audio_path: Path to audio file.
        segments: List of speaker-assigned segments.
        diarization_result: Output from diarize_audio().
        config: PreprocessConfig instance.
        min_duration: Skip segments shorter than this (unreliable embeddings).
        suspicious_threshold: If similarity to assigned speaker < this, check others.
        reassign_margin: Minimum margin to reassign to a different speaker.

    Returns:
        Updated segments with corrected speaker assignments (whole-segment
        reassigns + long-segment splits as applicable).
    """
    diar_speakers = diarization_result.get("speakers", [])
    if not diar_speakers or len(segments) < 2:
        return segments

    # Centroids: prefer the ones Type 6 already computed (verify_speaker_clusters
    # writes them into diarization_result["verification"]["cluster_centroids"]
    # as JSON-serializable lists, so they survive the R2 round-trip). If
    # missing (older saved diarization_result without the new key, or callers
    # that skipped Pass 2 entirely), recompute via _extract_cluster_embeddings.
    cluster_centroids: dict = {}
    verification = diarization_result.get("verification") or {}
    serialized = verification.get("cluster_centroids") or {}
    if serialized:
        cluster_centroids = _centroids_from_lists(serialized)
        if cluster_centroids:
            logger.info(
                f"Segment verification: using carry-forward centroids "
                f"({len(cluster_centroids)} speakers) from Type 6 verify_speaker_clusters"
            )

    inference = _load_embedding_model(
        config.DIARIZATION_EMBEDDING_MODEL,
        config.DIARIZATION_DEVICE,
        config.HF_AUTH_TOKEN,
    )

    if not cluster_centroids:
        logger.info("Segment verification: no carried centroids — recomputing via _extract_cluster_embeddings")
        cluster_centroids, _, _ = _extract_cluster_embeddings(
            inference, audio_path, diar_speakers, config.DIARIZATION_MIN_EMBED_DURATION,
        )

    if not cluster_centroids or len(cluster_centroids) < 2:
        logger.info("Segment verification: fewer than 2 speaker centroids, skipping")
        return segments

    audio_info = sf.info(str(audio_path))
    audio_duration = audio_info.duration

    long_seg_threshold = getattr(config, "DIARIZATION_LONG_SEG_THRESHOLD", 8.0)

    reassigned = 0
    skipped_short = 0
    long_seg_split_count = 0

    output_segments: list = []
    for seg in segments:
        seg_speaker = seg.get("speaker", "UNKNOWN")
        seg_duration = seg["end"] - seg["start"]

        if seg_speaker not in cluster_centroids:
            output_segments.append(seg)
            continue

        if seg_duration < min_duration:
            skipped_short += 1
            output_segments.append(seg)
            continue

        # --- Long segment branch: windowed scan + split ---
        if seg_duration > long_seg_threshold:
            splits = _scan_long_segment_for_splits(
                seg, inference, audio_path, audio_duration, cluster_centroids, config
            )
            if splits:
                pieces = _split_segment_at_times(seg, splits)
                long_seg_split_count += len(splits)
                output_segments.extend(pieces)
                continue
            # No splits found — fall through to whole-segment check below

        # --- Whole-segment check (short + long-with-no-splits) ---
        emb = _embed_window(inference, audio_path, seg["start"], seg["end"], audio_duration)
        if emb is None:
            output_segments.append(seg)
            continue

        own_sim, best_speaker, best_sim = _score_against_centroids(emb, cluster_centroids, seg_speaker)

        if own_sim >= suspicious_threshold:
            output_segments.append(seg)
            continue

        if best_speaker != seg_speaker and (best_sim - own_sim) >= reassign_margin:
            seg_text = seg.get("text", "")[:50]
            logger.info(
                f"  Segment verify: [{seg['start']:.1f}-{seg['end']:.1f}] "
                f"reassigned {seg_speaker}→{best_speaker} "
                f"(own={own_sim:.2f}, best={best_sim:.2f}, margin={best_sim-own_sim:.2f}) "
                f"\"{seg_text}\""
            )
            seg["speaker"] = best_speaker
            reassigned += 1

        output_segments.append(seg)

    segments = output_segments

    # Merge adjacent same-speaker segments
    if reassigned > 0 or long_seg_split_count > 0:
        merged = [segments[0]]
        for seg in segments[1:]:
            prev = merged[-1]
            if seg["speaker"] == prev["speaker"]:
                prev["end"] = seg["end"]
                prev["text"] = prev["text"].rstrip() + " " + seg["text"].lstrip()
                if "words" in prev and "words" in seg:
                    prev["words"] = prev.get("words", []) + seg.get("words", [])
            else:
                merged.append(seg)
        segments = merged

    logger.info(
        f"Segment verification: {reassigned} reassigned, {long_seg_split_count} long-seg splits, "
        f"{skipped_short} skipped (< {min_duration}s)"
    )

    return segments


def _merge_short_segments(segments: list, min_duration: float = 1.5) -> list:
    """Merge short orphan segments into same-speaker adjacent segments.

    After segment-level verification, tiny segments (< min_duration) may remain.
    Only merges into a neighbor with the SAME speaker. If both neighbors are
    different speakers, the segment is kept as-is to preserve speaker boundaries.
    """
    if len(segments) < 2:
        return segments

    merged_count = 0
    i = 0
    while i < len(segments):
        seg = segments[i]
        duration = seg["end"] - seg["start"]

        if duration >= min_duration:
            i += 1
            continue

        speaker = seg.get("speaker", "")
        prev_same = i > 0 and segments[i - 1].get("speaker") == speaker
        next_same = i < len(segments) - 1 and segments[i + 1].get("speaker") == speaker

        if prev_same:
            prev = segments[i - 1]
            prev["end"] = seg["end"]
            prev["text"] = prev["text"].rstrip() + " " + seg["text"].lstrip()
            if "words" in prev and "words" in seg:
                prev["words"] = prev.get("words", []) + seg.get("words", [])
            segments.pop(i)
            merged_count += 1
        elif next_same:
            nxt = segments[i + 1]
            nxt["start"] = seg["start"]
            nxt["text"] = seg["text"].rstrip() + " " + nxt["text"].lstrip()
            if "words" in nxt and "words" in seg:
                nxt["words"] = seg.get("words", []) + nxt.get("words", [])
            segments.pop(i)
            merged_count += 1
        else:
            i += 1

    if merged_count:
        logger.info(f"Merged {merged_count} short segments (< {min_duration}s)")

    return segments


def _verify_boundary_words(
    audio_path: Path,
    segments: list,
    diarization_result: dict,
    config,
    boundary_zone: float = 2.0,
    window_seconds: float = 1.0,
    min_margin: float = 0.20,
    min_best_sim: float = 0.50,
) -> list:
    """Verify and correct word-level speaker assignments near boundaries.

    For words within boundary_zone seconds of a speaker change, extract a short
    speaker embedding (~0.75s window) and compare to cluster centroids. If the
    word acoustically matches a different speaker with sufficient margin,
    reassign it. After reassignment, re-merge adjacent same-speaker segments.

    Args:
        audio_path: Path to audio file.
        segments: List of speaker-assigned segments with word-level timestamps.
        diarization_result: Output from diarize_audio() (for speaker list).
        config: PreprocessConfig instance.
        boundary_zone: Seconds around each boundary to check words.
        window_seconds: Duration of audio window for embedding extraction.
        min_margin: Minimum cosine similarity margin to reassign a word.
        min_best_sim: Minimum absolute similarity to the best speaker to reassign.

    Returns:
        Updated segments list with corrected speaker assignments.
    """
    from pyannote.core import Segment

    diar_speakers = diarization_result.get("speakers", [])
    if not diar_speakers or len(segments) < 2:
        return segments

    # Load embedding model (reuses singleton if still loaded)
    inference = _load_embedding_model(
        config.DIARIZATION_EMBEDDING_MODEL,
        config.DIARIZATION_DEVICE,
        config.HF_AUTH_TOKEN,
    )

    # Compute cluster centroids from diarization segments
    cluster_centroids, _, _ = _extract_cluster_embeddings(
        inference, audio_path, diar_speakers, config.DIARIZATION_MIN_EMBED_DURATION,
    )

    if len(cluster_centroids) < 2:
        logger.info("Boundary verification: fewer than 2 speaker centroids, skipping")
        return segments

    # Find boundaries (speaker changes between adjacent segments)
    boundaries = []
    for i in range(1, len(segments)):
        if segments[i].get("speaker") != segments[i - 1].get("speaker"):
            boundary_time = segments[i]["start"]
            boundaries.append(boundary_time)

    if not boundaries:
        logger.info("Boundary verification: no speaker changes found")
        return segments

    # Get audio duration for clamping
    audio_info = sf.info(str(audio_path))
    audio_duration = audio_info.duration

    reassigned = 0
    for seg in segments:
        words = seg.get("words", [])
        if not words:
            continue

        seg_speaker = seg.get("speaker", "UNKNOWN")
        if seg_speaker not in cluster_centroids:
            continue

        for w in words:
            w_start = w.get("start", 0)
            w_end = w.get("end", w_start)
            w_mid = (w_start + w_end) / 2

            # Check if this word is near any boundary
            near_boundary = False
            for bt in boundaries:
                if abs(w_mid - bt) <= boundary_zone:
                    near_boundary = True
                    break

            if not near_boundary:
                continue

            # Extract embedding for this word (centered window)
            emb_start = max(0, w_mid - window_seconds / 2)
            emb_end = min(audio_duration, w_mid + window_seconds / 2)
            if emb_end - emb_start < 0.3:
                continue

            try:
                emb = inference.crop(str(audio_path), Segment(emb_start, emb_end))
                emb = np.array(emb).flatten()
                if emb.shape[0] == 0:
                    continue
                # L2-normalize
                norm = np.linalg.norm(emb)
                if norm > 1e-8:
                    emb = emb / norm
            except Exception:
                continue

            # Compare to all cluster centroids
            own_sim = float(np.dot(emb, cluster_centroids[seg_speaker]))
            best_speaker = seg_speaker
            best_sim = own_sim
            for spk, centroid in cluster_centroids.items():
                if spk == seg_speaker:
                    continue
                sim = float(np.dot(emb, centroid))
                if sim > best_sim:
                    best_sim = sim
                    best_speaker = spk

            margin = best_sim - own_sim
            if (best_speaker != seg_speaker
                    and margin >= min_margin
                    and best_sim >= min_best_sim):
                word_text = w.get("word", "")
                logger.info(
                    f"  Boundary verify: word \"{word_text}\" at {w_mid:.2f}s "
                    f"reassigned {seg_speaker}→{best_speaker} "
                    f"(own={own_sim:.2f}, best={best_sim:.2f}, margin={margin:.2f})"
                )
                w["_reassigned_speaker"] = best_speaker
                reassigned += 1

    if reassigned == 0:
        logger.info("Boundary verification: 0 words reassigned")
        return segments

    # Rebuild segments: split segments where words were reassigned to different speakers
    new_segments = []
    for seg in segments:
        words = seg.get("words", [])
        if not words or not any("_reassigned_speaker" in w for w in words):
            new_segments.append(seg)
            continue

        # Group consecutive words by effective speaker
        groups = []
        for w in words:
            effective_speaker = w.pop("_reassigned_speaker", seg["speaker"])
            if groups and groups[-1][0] == effective_speaker:
                groups[-1][1].append(w)
            else:
                groups.append((effective_speaker, [w]))

        for speaker, group_words in groups:
            if not group_words:
                continue
            new_seg = {
                **seg,
                "start": round(group_words[0].get("start", seg["start"]), 2),
                "end": round(group_words[-1].get("end", seg["end"]), 2),
                "text": " ".join(w.get("word", "") for w in group_words).strip(),
                "words": group_words,
                "speaker": speaker,
            }
            new_segments.append(new_seg)

    # Merge adjacent segments with same speaker
    merged = [new_segments[0]]
    for seg in new_segments[1:]:
        prev = merged[-1]
        if seg["speaker"] == prev["speaker"]:
            prev["end"] = seg["end"]
            prev["text"] = prev["text"].rstrip() + " " + seg["text"].lstrip()
            if "words" in prev and "words" in seg:
                prev["words"] = prev.get("words", []) + seg.get("words", [])
        else:
            merged.append(seg)

    logger.info(f"Boundary verification: reassigned {reassigned} words "
                f"({len(new_segments)} → {len(merged)} segments after merge)")

    return merged


_SENTENCE_ENDINGS = {'.', '?', '!'}


def _realign_speaker_boundaries(segments: list, min_words: int = 4, min_duration: float = 1.5) -> list:
    """Merge tiny speaker fragments back into their neighboring segment.

    Only merges when one side of a speaker change is a fragment (< min_words
    AND < min_duration). Never merges two substantial segments — those
    represent real speaker changes even without sentence-ending punctuation.
    """
    if len(segments) < 2:
        return segments

    realigned_count = 0

    def _is_fragment(seg):
        word_count = len(seg["text"].split())
        duration = seg["end"] - seg["start"]
        return word_count < min_words and duration < min_duration

    i = 0
    while i < len(segments):
        seg = segments[i]
        if not _is_fragment(seg):
            i += 1
            continue

        prev_speaker = segments[i - 1]["speaker"] if i > 0 else None
        next_speaker = segments[i + 1]["speaker"] if i < len(segments) - 1 else None

        if seg["speaker"] == prev_speaker or seg["speaker"] == next_speaker:
            i += 1
            continue

        seg_text = seg["text"].strip()
        prev_text = segments[i - 1]["text"].strip() if i > 0 else ""
        merge_before = False
        merge_after = False

        if i > 0 and prev_text and prev_text[-1] not in _SENTENCE_ENDINGS:
            merge_before = True
        elif i < len(segments) - 1:
            if seg_text and seg_text[0].islower():
                merge_before = True
            else:
                merge_after = True
        elif i > 0:
            merge_before = True

        if merge_before and i > 0:
            segments[i]["speaker"] = segments[i - 1]["speaker"]
            realigned_count += 1
            logger.info(f"  Realign: fragment \"{seg_text[:40]}\" ({seg['start']:.1f}s) → merged with previous speaker")
        elif merge_after and i < len(segments) - 1:
            segments[i]["speaker"] = segments[i + 1]["speaker"]
            realigned_count += 1
            logger.info(f"  Realign: fragment \"{seg_text[:40]}\" ({seg['start']:.1f}s) → merged with next speaker")

        i += 1

    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg["speaker"] == prev["speaker"]:
            prev["end"] = seg["end"]
            prev["text"] = prev["text"].rstrip() + " " + seg["text"].lstrip()
            if "words" in prev and "words" in seg:
                prev["words"] = prev.get("words", []) + seg.get("words", [])
        else:
            merged.append(seg)

    if realigned_count > 0:
        logger.info(f"Speaker realignment: merged {realigned_count} fragments ({len(segments)} → {len(merged)} segments)")
    else:
        logger.info("Speaker realignment: no fragments found")

    return merged


def _verify_speaker_boundaries_llm(
    segments: list,
    base_url: str,
    model: str,
    min_words: int = 3,
    min_duration: float = 1.5,
) -> list:
    """Use LLM to verify suspicious speaker transitions (short fragments)."""
    if len(segments) < 2:
        return segments

    suspicious = []
    for i in range(1, len(segments)):
        if segments[i]["speaker"] == segments[i - 1]["speaker"]:
            continue

        for check_idx in (i - 1, i):
            seg = segments[check_idx]
            word_count = len(seg["text"].split())
            duration = seg["end"] - seg["start"]
            if word_count < min_words or duration < min_duration:
                if check_idx not in [s["idx"] for s in suspicious]:
                    suspicious.append({
                        "idx": check_idx,
                        "text": seg["text"],
                        "speaker": seg["speaker"],
                        "word_count": word_count,
                        "duration": duration,
                    })

    if not suspicious:
        logger.info("Speaker LLM verification: no suspicious transitions found")
        return segments

    logger.info(f"Speaker LLM verification: checking {len(suspicious)} suspicious segments")

    context_entries = []
    for s in suspicious:
        idx = s["idx"]
        ctx_start = max(0, idx - 2)
        ctx_end = min(len(segments), idx + 3)
        context_lines = []
        for j in range(ctx_start, ctx_end):
            marker = " >>> " if j == idx else "     "
            context_lines.append(f"{marker}[{segments[j]['speaker']}] \"{segments[j]['text']}\"")
        context_entries.append({
            "id": len(context_entries),
            "idx": idx,
            "context": "\n".join(context_lines),
            "flagged_text": s["text"],
            "flagged_speaker": s["speaker"],
        })

    prompt_parts = []
    for entry in context_entries:
        prompt_parts.append(
            f"--- Case {entry['id']} ---\n"
            f"Flagged segment: [{entry['flagged_speaker']}] \"{entry['flagged_text']}\"\n"
            f"Context:\n{entry['context']}\n"
        )

    system_prompt = (
        "You analyze meeting transcripts for speaker attribution errors. "
        "Short segments (1-2 words) at speaker boundaries are often misattributed "
        "due to diarization imprecision. For each flagged segment, decide: "
        "should it stay with its current speaker, merge with the speaker BEFORE it, "
        "or merge with the speaker AFTER it? "
        "Respond in JSON: {\"decisions\": [{\"id\": 0, \"action\": \"keep\" | \"merge_before\" | \"merge_after\", \"reason\": \"...\"}]}"
    )
    user_prompt = "\n".join(prompt_parts)

    import httpx
    prompt_chars = len(system_prompt) + len(user_prompt)
    num_ctx = max(4096, int(prompt_chars / 3.5) + 2000)

    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": num_ctx},
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            logger.warning(f"Speaker LLM verification: Ollama returned {resp.status_code}")
            return segments

        import json
        content = resp.json().get("message", {}).get("content", "")
        result = json.loads(content)
        decisions = result.get("decisions", [])

        corrected_count = 0
        for decision in decisions:
            did = decision.get("id")
            action = decision.get("action", "keep")
            if did is None or did >= len(context_entries):
                continue

            seg_idx = context_entries[did]["idx"]

            if action == "merge_before" and seg_idx > 0:
                segments[seg_idx]["speaker"] = segments[seg_idx - 1]["speaker"]
                corrected_count += 1
                logger.info(f"  LLM: merged seg {seg_idx} (\"{segments[seg_idx]['text'][:30]}\") with previous speaker — {decision.get('reason', '')}")
            elif action == "merge_after" and seg_idx < len(segments) - 1:
                segments[seg_idx]["speaker"] = segments[seg_idx + 1]["speaker"]
                corrected_count += 1
                logger.info(f"  LLM: merged seg {seg_idx} (\"{segments[seg_idx]['text'][:30]}\") with next speaker — {decision.get('reason', '')}")

        if corrected_count > 0:
            merged = [segments[0]]
            for seg in segments[1:]:
                prev_seg = merged[-1]
                if seg["speaker"] == prev_seg["speaker"]:
                    prev_seg["end"] = seg["end"]
                    prev_seg["text"] = prev_seg["text"].rstrip() + " " + seg["text"].lstrip()
                    if "words" in prev_seg and "words" in seg:
                        prev_seg["words"] = prev_seg.get("words", []) + seg.get("words", [])
                else:
                    merged.append(seg)
            segments = merged
            logger.info(f"Speaker LLM verification: corrected {corrected_count} assignments")
        else:
            logger.info("Speaker LLM verification: all assignments confirmed correct")

    except Exception as e:
        logger.warning(f"Speaker LLM verification failed (non-fatal): {e}")

    return segments


def verify_speakers_sliding_window(
    segments: list,
    audio_path: str,
    config,
    speaker_embeddings: dict = None,
) -> list:
    """Post-alignment speaker verification: check tail (and optionally head)
    of each segment.

    Pass 1 — Tail check (always runs): For each segment, checks if the last
    2 seconds match the assigned speaker using ECAPA-TDNN embeddings. If
    mismatch, slides backwards to find where the speaker changed and splits
    at that point. Catches Whisper rightward-extension into the next speaker.

    Pass 2 — Head check (runs when DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL,
    default true): Same idea but checks the FIRST 2 seconds and slides
    forward. Catches "first portion mislabeled" cases — observed on May 12
    when pyannote 4.0 community-1 merged a Sathish utterance into a long
    Vivek-labeled segment; tail pass missed it because the end was
    correctly Vivek. Skips segments shorter than 4 seconds (head and tail
    windows would overlap).

    To disable: set DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL=false to skip
    just the head pass, or comment out the single call in processor.py to
    skip both passes.
    """
    import numpy as np
    from scipy.spatial.distance import cosine

    speaker_ids = {s.get("speaker") for s in segments if s.get("speaker")}
    if len(speaker_ids) < 2:
        return segments

    if not speaker_embeddings or len(speaker_embeddings) < 2:
        logger.info("Speaker verify: need embeddings for at least 2 speakers, skipping")
        return segments

    try:
        centroids = {}
        for spk, data in speaker_embeddings.items():
            emb = data.get("embedding", [])
            if emb:
                centroids[spk] = np.array(emb)

        if len(centroids) < 2:
            return segments

        from app.audio_preprocess.services.voice_embeddings import _get_classifier
        import torchaudio
        classifier = _get_classifier()

        waveform, wav_sr = torchaudio.load(str(audio_path))
        if wav_sr != 16000:
            waveform = torchaudio.transforms.Resample(wav_sr, 16000)(waveform)
            wav_sr = 16000

        logger.info(f"Speaker verify: checking {len(segments)} segments against {len(centroids)} centroids")

        def get_emb(start, end):
            s = max(0, int(start * wav_sr))
            e = min(waveform.shape[1], int(end * wav_sr))
            w = waveform[:, s:e]
            if w.shape[1] < wav_sr * 1.0:
                return None
            return classifier.encode_batch(w).squeeze().cpu().numpy()

        split_count = 0
        i = 0
        while i < len(segments):
            seg = segments[i]
            spk = seg.get("speaker", "")
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            dur = end - start

            if not spk or spk not in centroids or dur < 2.0:
                i += 1
                continue

            # Check last 2s
            tail_emb = get_emb(end - 2.0, end)
            if tail_emb is None:
                i += 1
                continue

            assigned_sim = 1 - cosine(tail_emb, centroids[spk])
            other_spk = [s for s in centroids if s != spk][0]
            other_sim = 1 - cosine(tail_emb, centroids[other_spk])

            if other_sim <= assigned_sim:
                i += 1
                continue

            # Mismatch — slide 2s window backwards to find transition zone
            win_size = 2.0
            win_step = 0.5
            other_pos = None  # last position where other speaker was detected
            assigned_pos = None  # first position where assigned speaker is detected

            pos = end - win_size
            while pos >= start:
                emb = get_emb(pos, min(pos + win_size, end))
                if emb is not None:
                    a = 1 - cosine(emb, centroids[spk])
                    o = 1 - cosine(emb, centroids[other_spk])
                    if a >= o:
                        assigned_pos = pos
                        break
                    else:
                        other_pos = pos
                pos -= win_step

            if not assigned_pos or not other_pos:
                i += 1
                continue

            # Transition zone: [assigned_pos, other_pos + win_size]
            zone_start = assigned_pos
            zone_end = other_pos + win_size

            # Binary search within the zone to find exact transition
            mid = (zone_start + zone_end) / 2
            emb_mid = get_emb(zone_start, mid)
            if emb_mid is not None:
                a = 1 - cosine(emb_mid, centroids[spk])
                o = 1 - cosine(emb_mid, centroids[other_spk])
                if a >= o:
                    # First half is assigned speaker, change is after midpoint
                    split_time = mid
                else:
                    # First half already other speaker, change is before midpoint
                    split_time = zone_start
            else:
                split_time = mid

            # Find the word boundary at the split point — look for the largest
            # gap between consecutive words near split_time (speaker pause)
            words = seg.get("words", [])
            if not words:
                i += 1
                continue

            best_gap = 0
            split_idx = None
            for wi in range(1, len(words)):
                w_prev_end = words[wi - 1].get("end", 0)
                w_cur_start = words[wi].get("start", 0)
                # Only consider gaps near the split time
                if abs(w_cur_start - split_time) > win_size:
                    continue
                gap = w_cur_start - w_prev_end
                if gap > best_gap:
                    best_gap = gap
                    split_idx = wi

            if split_idx is None or split_idx == 0 or split_idx >= len(words):
                i += 1
                continue

            # Split segment
            words_before = words[:split_idx]
            words_after = words[split_idx:]

            seg1 = dict(seg)
            seg1["words"] = words_before
            seg1["text"] = " ".join(w.get("word", "") for w in words_before).strip()
            seg1["end"] = words_before[-1].get("end", end)
            if "original_end" in seg1:
                seg1["original_end"] = words_before[-1].get("original_end", seg1.get("original_end"))
            seg1["speaker"] = spk

            seg2 = dict(seg)
            seg2["words"] = words_after
            seg2["text"] = " ".join(w.get("word", "") for w in words_after).strip()
            seg2["start"] = words_after[0].get("start", start)
            seg2["end"] = words_after[-1].get("end", end)
            if "original_start" in seg2:
                seg2["original_start"] = words_after[0].get("original_start", seg2.get("original_start"))
            if "original_end" in seg2:
                seg2["original_end"] = words_after[-1].get("original_end", seg2.get("original_end"))
            seg2["speaker"] = other_spk

            segments[i:i + 1] = [seg1, seg2]
            split_count += 1
            logger.info(
                f"Speaker verify: split at {split_time:.1f}s, "
                f"{spk}→{other_spk}: \"{seg2['text'][:50]}\""
            )
            i += 1  # skip the first half, check the second half next iteration

        # ============================================================
        # Pass 2 — head check: mirror the tail logic but inspect the
        # FIRST 2 sec of each segment, slide FORWARD to find the speaker
        # transition. Splits with the first portion reassigned to the
        # other speaker, second portion kept as assigned.
        # ============================================================
        head_bidirectional = getattr(
            config, "DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL", True
        )
        head_split_count = 0
        if head_bidirectional:
            i = 0
            while i < len(segments):
                seg = segments[i]
                spk = seg.get("speaker", "")
                start = seg.get("start", 0)
                end = seg.get("end", 0)
                dur = end - start

                # Need at least 4s so head and tail windows don't overlap
                if not spk or spk not in centroids or dur < 4.0:
                    i += 1
                    continue

                # Check first 2s
                head_emb = get_emb(start, start + 2.0)
                if head_emb is None:
                    i += 1
                    continue

                assigned_sim = 1 - cosine(head_emb, centroids[spk])
                other_spk = [s for s in centroids if s != spk][0]
                other_sim = 1 - cosine(head_emb, centroids[other_spk])

                if other_sim <= assigned_sim:
                    i += 1
                    continue

                # Mismatch — slide 2s window forward to find where the
                # assigned speaker starts. Track last position where other
                # speaker was seen + first position where assigned appears.
                win_size = 2.0
                win_step = 0.5
                other_pos = start  # last position where other speaker confirmed
                assigned_pos = None  # first position where assigned speaker confirmed

                pos = start + win_step
                while pos + win_size <= end:
                    emb = get_emb(pos, pos + win_size)
                    if emb is not None:
                        a = 1 - cosine(emb, centroids[spk])
                        o = 1 - cosine(emb, centroids[other_spk])
                        if a >= o:
                            assigned_pos = pos
                            break
                        else:
                            other_pos = pos
                    pos += win_step

                if assigned_pos is None:
                    # Whole segment is the other speaker — relabel rather
                    # than split. This is a "wholly mislabeled segment"
                    # case that's safer to handle by reassignment.
                    i += 1
                    continue

                # Transition zone: [other_pos, assigned_pos + win_size]
                zone_start = other_pos
                zone_end = assigned_pos + win_size

                # Binary search within the zone to find exact transition
                mid = (zone_start + zone_end) / 2
                emb_mid = get_emb(mid, min(mid + win_size, end))
                if emb_mid is not None:
                    a = 1 - cosine(emb_mid, centroids[spk])
                    o = 1 - cosine(emb_mid, centroids[other_spk])
                    if a >= o:
                        # Mid is already assigned speaker; transition is before mid
                        split_time = zone_start
                    else:
                        # Mid is still other speaker; transition is after mid
                        split_time = mid
                else:
                    split_time = mid

                # Find the word boundary near split_time (largest pause)
                words = seg.get("words", [])
                if not words:
                    i += 1
                    continue

                best_gap = 0
                split_idx = None
                for wi in range(1, len(words)):
                    w_prev_end = words[wi - 1].get("end", 0)
                    w_cur_start = words[wi].get("start", 0)
                    if abs(w_cur_start - split_time) > win_size:
                        continue
                    gap = w_cur_start - w_prev_end
                    if gap > best_gap:
                        best_gap = gap
                        split_idx = wi

                if split_idx is None or split_idx == 0 or split_idx >= len(words):
                    i += 1
                    continue

                # Split: first half → other_spk, second half stays as spk
                words_before = words[:split_idx]
                words_after = words[split_idx:]

                seg1 = dict(seg)
                seg1["words"] = words_before
                seg1["text"] = " ".join(w.get("word", "") for w in words_before).strip()
                seg1["end"] = words_before[-1].get("end", end)
                if "original_end" in seg1:
                    seg1["original_end"] = words_before[-1].get(
                        "original_end", seg1.get("original_end")
                    )
                seg1["speaker"] = other_spk

                seg2 = dict(seg)
                seg2["words"] = words_after
                seg2["text"] = " ".join(w.get("word", "") for w in words_after).strip()
                seg2["start"] = words_after[0].get("start", start)
                seg2["end"] = words_after[-1].get("end", end)
                if "original_start" in seg2:
                    seg2["original_start"] = words_after[0].get(
                        "original_start", seg2.get("original_start")
                    )
                if "original_end" in seg2:
                    seg2["original_end"] = words_after[-1].get(
                        "original_end", seg2.get("original_end")
                    )
                seg2["speaker"] = spk

                segments[i:i + 1] = [seg1, seg2]
                head_split_count += 1
                logger.info(
                    f"Speaker verify: head-split at {split_time:.1f}s, "
                    f"{spk}→{other_spk}: \"{seg1['text'][:50]}\""
                )
                # Move past both new segments (first half is the relabeled
                # other-speaker portion, second half is already-checked
                # spk — no need to re-tail-check since this pass is independent)
                i += 2

            if head_split_count > 0:
                logger.info(f"Speaker verify (head pass): {head_split_count} splits applied")
            else:
                logger.info("Speaker verify (head pass): no head mismatches found")
            split_count += head_split_count

        # Merge consecutive same-speaker segments with small time gap (< 1s)
        # These are segments that were originally separate but have the same speaker
        if split_count > 0:
            merged = [segments[0]]
            for seg in segments[1:]:
                prev = merged[-1]
                gap = seg.get("start", 0) - prev.get("end", 0)
                if seg.get("speaker") == prev.get("speaker") and gap < 1.0:
                    prev["text"] = (prev.get("text", "") + " " + seg.get("text", "")).strip()
                    prev["end"] = seg.get("end", prev.get("end", 0))
                    prev_words = prev.get("words", [])
                    prev_words.extend(seg.get("words", []))
                    prev["words"] = prev_words
                    if "original_end" in seg:
                        prev["original_end"] = seg.get("original_end")
                else:
                    merged.append(seg)
            if len(merged) < len(segments):
                logger.info(f"Speaker verify: merged {len(segments) - len(merged)} adjacent same-speaker segments")
            segments = merged
            logger.info(f"Speaker verify: {split_count} splits applied, {len(segments)} final segments")
        else:
            logger.info("Speaker verify: all segments OK")

    except Exception as e:
        logger.warning(f"Speaker verify failed (non-fatal): {e}")

    return segments
