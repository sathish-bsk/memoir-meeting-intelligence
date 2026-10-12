"""
Standalone ASR for the audio preprocessing pipeline.
Supports two engines: openai-whisper (PyTorch) and faster-whisper (CTranslate2).
Supports smart chunking via Silero VAD for long audio with per-chunk language detection.
Returns both full text and segment-level timestamps.
"""
import gc
import logging
import os
import re
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Optional, List, Tuple

import numpy as np
import soundfile as sf
import torch

logger = logging.getLogger("audio_preprocess")

# ── openai-whisper model cache ──
_ow_model = None
_ow_model_size = None
_ow_lock = threading.Lock()

# ── faster-whisper model cache ──
_fw_model = None
_fw_model_size = None
_fw_lock = threading.Lock()

# ── Shared VAD model cache ──
_vad_model = None
_vad_utils = None
_vad_lock = threading.Lock()

MAX_CHUNK_SECONDS = 28.0  # Whisper context window is 30s, leave margin
MAX_CHUNK_SECONDS_PHONE = 18.0  # Shorter chunks for phone mode — reduces volume contrast within chunks
TAIL_MIN_GAP_SECONDS = 1.5  # Re-transcribe if > 1.5s uncovered at chunk end
TAIL_OVERLAP_SECONDS = 1.0  # Overlap with last segment for decoder context
GAP_RESCUE_MIN_SECONDS = 1.0  # Minimum gap size to trigger rescue re-transcription


# ═══════════════════════════════════════════════════════════════════
# Model Loading
# ═══════════════════════════════════════════════════════════════════

def _load_openai_whisper(model_size: str = "medium", device: str = "cpu"):
    global _ow_model, _ow_model_size
    if _ow_model is not None and _ow_model_size == model_size:
        return _ow_model
    with _ow_lock:
        if _ow_model is not None and _ow_model_size == model_size:
            return _ow_model
        if _ow_model is not None:
            logger.info(f"openai-whisper model size changed: {_ow_model_size} -> {model_size}, reloading...")
            del _ow_model
            _ow_model = None
            gc.collect()
        logger.info(f"Loading openai-whisper model: {model_size} on {device}")
        import whisper
        _ow_model = whisper.load_model(model_size, device=device)
        _ow_model_size = model_size
        logger.info("openai-whisper model loaded")
        return _ow_model


def _load_faster_whisper(model_size: str = "medium", device: str = "cpu", compute_type: str = "int8"):
    global _fw_model, _fw_model_size
    if _fw_model is not None and _fw_model_size == model_size:
        return _fw_model
    with _fw_lock:
        if _fw_model is not None and _fw_model_size == model_size:
            return _fw_model
        if _fw_model is not None:
            logger.info(f"faster-whisper model size changed: {_fw_model_size} -> {model_size}, reloading...")
            del _fw_model
            _fw_model = None
            gc.collect()
        logger.info(f"Loading faster-whisper model: {model_size} on {device} (compute_type={compute_type})")
        from faster_whisper import WhisperModel
        _fw_model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _fw_model_size = model_size
        logger.info("faster-whisper model loaded")
        return _fw_model


def _load_vad():
    global _vad_model, _vad_utils
    if _vad_model is not None:
        return _vad_model, _vad_utils
    with _vad_lock:
        if _vad_model is not None:
            return _vad_model, _vad_utils
        logger.info("Loading Silero VAD for smart chunking...")
        _vad_model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        _vad_utils = utils
        logger.info("Silero VAD loaded for chunking")
        return _vad_model, _vad_utils


# ═══════════════════════════════════════════════════════════════════
# Transcribe Options (engine-specific)
# ═══════════════════════════════════════════════════════════════════

def _get_options_ow(language: Optional[str] = None, phone_mode: bool = False, initial_prompt: Optional[str] = None) -> dict:
    """openai-whisper transcribe options."""
    options = {
        "task": "transcribe",
        "verbose": False,
        "beam_size": 5,
        "best_of": 1,
        "temperature": 0,
        "condition_on_previous_text": False,
        "initial_prompt": initial_prompt or (
            "This is a clear, well-structured transcription with proper punctuation, "
            "capitalization, and natural sentence breaks."
        ),
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": None if phone_mode else 0.6,
    }
    if language:
        options["language"] = language
    return options


def _get_options_fw(language: Optional[str] = None, hotwords: str = "", phone_mode: bool = False, initial_prompt: Optional[str] = None) -> dict:
    """faster-whisper transcribe options."""
    options = {
        "beam_size": 5,
        "best_of": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "initial_prompt": initial_prompt or (
            "This is a clear, well-structured transcription with proper punctuation, "
            "capitalization, and natural sentence breaks."
        ),
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": None if phone_mode else 0.6,
        "vad_filter": False,
    }
    if language:
        options["language"] = language
    if hotwords:
        options["hotwords"] = hotwords
    return options


# ═══════════════════════════════════════════════════════════════════
# Shared: VAD Chunking (engine-agnostic)
# ═══════════════════════════════════════════════════════════════════

def _find_lowest_energy_point(audio: np.ndarray, sr: int, window_ms: int = 50) -> int:
    """
    Find the sample index with the lowest energy in a sliding window.
    Used to split at natural breath pauses / low-energy points.
    """
    window_samples = int(sr * window_ms / 1000)
    if len(audio) < window_samples * 2:
        return len(audio) // 2

    margin = len(audio) // 5
    search_start = margin
    search_end = len(audio) - margin

    min_energy = float("inf")
    min_idx = len(audio) // 2

    for i in range(search_start, search_end - window_samples, window_samples // 2):
        window = audio[i : i + window_samples]
        energy = np.sqrt(np.mean(window ** 2))
        if energy < min_energy:
            min_energy = energy
            min_idx = i + window_samples // 2

    return min_idx


def _split_oversized_chunk(audio: np.ndarray, sr: int, start_sample: int, end_sample: int, max_seconds: float = MAX_CHUNK_SECONDS) -> List[Tuple[int, int]]:
    """
    Split a chunk that exceeds max_seconds by finding low-energy
    split points. Recursively splits until all chunks are within limit.
    """
    duration = (end_sample - start_sample) / sr
    if duration <= max_seconds:
        return [(start_sample, end_sample)]

    chunk_audio = audio[start_sample:end_sample]
    split_offset = _find_lowest_energy_point(chunk_audio, sr)
    split_sample = start_sample + split_offset

    logger.info(
        f"  Splitting oversized chunk ({duration:.1f}s) at {split_sample/sr:.1f}s "
        f"(energy-based split point)"
    )

    left = _split_oversized_chunk(audio, sr, start_sample, split_sample, max_seconds)
    right = _split_oversized_chunk(audio, sr, split_sample, end_sample, max_seconds)
    return left + right


def _get_speech_chunks(audio: np.ndarray, sr: int, phone_mode: bool = False) -> List[Tuple[int, int]]:
    """
    Smart chunking strategy:
    1. Try Silero VAD to find speech boundaries (pauses >= 500ms)
    2. Group into chunks <= MAX_CHUNK_SECONDS
    3. Any oversized chunks are split at low-energy points
    Returns list of (start_sample, end_sample) tuples.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    model, utils = _load_vad()
    get_speech_timestamps = utils[0]

    if phone_mode:
        vad_threshold = cfg.PHONE_MODE_VAD_THRESHOLD
        min_speech_ms = cfg.PHONE_MODE_MIN_SPEECH_MS
        speech_pad_ms = cfg.PHONE_MODE_SPEECH_PAD_MS
    else:
        vad_threshold = 0.5
        min_speech_ms = 250
        speech_pad_ms = 200

    # Chunking VAD always uses min_silence=300ms to find natural split points.
    # Phone mode trim VAD uses 500ms (to preserve quiet speech), but chunking
    # needs shorter pauses as boundaries — otherwise large merged segments
    # force energy-based splitting which creates sub-optimal chunk boundaries.
    wav_tensor = torch.from_numpy(audio)
    speech_timestamps = get_speech_timestamps(
        wav_tensor, model,
        sampling_rate=sr,
        threshold=vad_threshold,
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=300,
        speech_pad_ms=speech_pad_ms,
    )

    max_chunk = MAX_CHUNK_SECONDS_PHONE if phone_mode else MAX_CHUNK_SECONDS

    if not speech_timestamps:
        logger.warning("VAD found no speech boundaries, using energy-based splitting")
        return _split_oversized_chunk(audio, sr, 0, len(audio), max_chunk)

    raw_chunks = []
    current_start = speech_timestamps[0]["start"]
    current_end = speech_timestamps[0]["end"]

    for ts in speech_timestamps[1:]:
        potential_duration = (ts["end"] - current_start) / sr
        if potential_duration <= max_chunk:
            current_end = ts["end"]
        else:
            raw_chunks.append((current_start, current_end))
            current_start = ts["start"]
            current_end = ts["end"]

    raw_chunks.append((current_start, current_end))

    chunks = []
    for start, end in raw_chunks:
        chunk_dur = (end - start) / sr
        if chunk_dur <= max_chunk:
            chunks.append((start, end))
        else:
            logger.info(f"  Chunk {start/sr:.1f}s-{end/sr:.1f}s is {chunk_dur:.1f}s, needs splitting")
            chunks.extend(_split_oversized_chunk(audio, sr, start, end, max_chunk))

    # Extend chunks to cover full audio — the input is already VAD-trimmed
    # (silence removed), so edges contain speech that chunking VAD may have
    # marginally missed due to threshold/padding differences
    if chunks:
        first_start, first_end = chunks[0]
        if first_start > 0:
            logger.info(f"  Extending first chunk start: {first_start/sr:.2f}s -> 0.0s")
            chunks[0] = (0, first_end)

        last_start, last_end = chunks[-1]
        if len(audio) - last_end > int(sr * 0.1):  # > 100ms uncovered
            logger.info(f"  Extending last chunk end: {last_end/sr:.2f}s -> {len(audio)/sr:.2f}s")
            chunks[-1] = (last_start, len(audio))

    logger.info(
        f"Smart chunking: {len(audio)/sr:.1f}s audio -> {len(chunks)} chunks "
        f"(from {len(speech_timestamps)} VAD segments)"
    )
    for i, (s, e) in enumerate(chunks):
        logger.info(f"  Chunk {i+1}: {s/sr:.1f}s - {e/sr:.1f}s ({(e-s)/sr:.1f}s)")

    return chunks


# ═══════════════════════════════════════════════════════════════════
# Multilingual Mode: Language Survey + Chunk-Level Dual-Pass
# ═══════════════════════════════════════════════════════════════════

LID_SURVEY_SEGMENT_SECONDS = 15.0  # Sample 15s segments for reliable LID


def _detect_language_ow(model, audio_path: str) -> dict:
    """Detect language using openai-whisper. Returns {language, confidence, probs}."""
    import whisper
    audio = whisper.load_audio(audio_path)
    audio = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(audio).to(model.device)
    _, probs = model.detect_language(mel)
    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    return {
        "language": sorted_probs[0][0],
        "confidence": float(sorted_probs[0][1]),
        "probs": {k: float(v) for k, v in sorted_probs[:5]},
    }


def _detect_language_fw(model, audio_path: str) -> dict:
    """Detect language using faster-whisper. Returns {language, confidence, probs}."""
    audio, _ = sf.read(audio_path, dtype="float32")
    detected_lang, confidence, all_probs = model.detect_language(audio=audio)
    top_5 = dict(all_probs[:5]) if len(all_probs) >= 5 else dict(all_probs)
    return {
        "language": detected_lang,
        "confidence": float(confidence),
        "probs": {k: float(v) for k, v in top_5.items()},
    }


def _survey_languages(
    model,
    audio_data: np.ndarray,
    sr: int,
    detect_language_fn,
    num_samples: int = 5,
    sample_seconds: float = LID_SURVEY_SEGMENT_SECONDS,
) -> List[str]:
    """
    Quick language survey: sample a few evenly-spaced segments across the audio,
    run LID on each, return the list of unique detected languages (top 2-3).
    Uses longer segments (15s) for reliable detection — NOT micro-segments.
    """
    total_samples = len(audio_data)
    total_duration = total_samples / sr
    sample_len = int(sample_seconds * sr)

    # Determine sample positions (evenly spaced)
    if total_duration <= sample_seconds:
        # Audio is shorter than one sample — just use the whole thing
        positions = [0]
        sample_len = total_samples
    else:
        spacing = total_samples / (num_samples + 1)
        positions = [int(spacing * (i + 1)) for i in range(num_samples)]
        # Clamp so we don't go past the end
        positions = [p for p in positions if p + sample_len <= total_samples]
        if not positions:
            positions = [0]
            sample_len = min(sample_len, total_samples)

    lang_votes = []
    for i, pos in enumerate(positions):
        segment = audio_data[pos:pos + sample_len]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, segment, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            lid = detect_language_fn(model, tmp_path)
            lang_votes.append(lid["language"])
            logger.info(
                f"  Language survey {i+1}/{len(positions)}: "
                f"{pos/sr:.1f}s-{(pos+len(segment))/sr:.1f}s "
                f"-> {lid['language']} ({lid['confidence']:.2f})"
            )
        finally:
            os.unlink(tmp_path)

    # Count votes and return unique languages that appeared
    lang_counts = Counter(lang_votes)
    # Keep languages that got at least 1 vote, sorted by frequency
    candidate_langs = [lang for lang, _ in lang_counts.most_common()]

    # Limit to top 3 to avoid trying too many languages
    candidate_langs = candidate_langs[:3]

    logger.info(f"  Language survey result: {dict(lang_counts)} -> candidates: {candidate_langs}")
    return candidate_langs


def _is_hallucination(text: str, compression_ratio: float, avg_logprob: float,
                       no_speech_prob: float) -> bool:
    """
    Detect if a segment is a Whisper hallucination.

    Thresholds aligned with Whisper's own defaults (compression_ratio_threshold=2.4,
    logprob_threshold=-1.0) to avoid false positives on legitimate speech.
    no_speech_prob is NOT used as a standalone trigger — conversational audio
    often has high no_speech_prob on real speech (turn boundaries, quiet speech).
    """
    # High compression ratio = repetition loop (Whisper's own default is 2.4)
    if compression_ratio > 2.4:
        return True
    # Very low confidence (Whisper's own default is -1.0)
    if avg_logprob < -1.0:
        return True
    # N-gram repetition detection (3-gram through 5-gram)
    # Two checks:
    # 1. Any n-gram appears >5 times total (even non-consecutive)
    # 2. Same n-gram appears 3+ times consecutively (back-to-back loop)
    # This catches real hallucination loops while ignoring rhetorical patterns
    # like "how to be X, how to be Y, how to be Z"
    words = text.strip().lower().split()
    if len(words) >= 9:
        for n in range(3, 6):
            ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
            # Check 1: >5 total occurrences
            counts = Counter(ngrams)
            if any(c > 5 for c in counts.values()):
                return True
            # Check 2: 3+ consecutive identical n-grams (back-to-back loop)
            consecutive = 1
            for i in range(1, len(ngrams)):
                if ngrams[i] == ngrams[i - 1]:
                    consecutive += 1
                    if consecutive >= 3:
                        return True
                else:
                    consecutive = 1
    return False


def _filter_hallucinations(segments: list) -> list:
    """Remove hallucinated segments and return cleaned list."""
    cleaned = []
    for seg in segments:
        if _is_hallucination(
            seg["text"], seg["compression_ratio"],
            seg["avg_logprob"], seg["no_speech_prob"]
        ):
            logger.info(
                f"    Filtered hallucination: '{seg['text'][:80]}...' "
                f"(cr={seg['compression_ratio']:.1f}, lp={seg['avg_logprob']:.2f}, "
                f"nsp={seg['no_speech_prob']:.2f})"
            )
        else:
            cleaned.append(seg)
    return cleaned


def _transcribe_multilingual(
    model,
    audio_data: np.ndarray,
    sr: int,
    candidate_languages: List[str],
    get_options,
    transcribe_chunk,
    phone_mode: bool = False,
) -> dict:
    """
    Chunk-level dual-pass multilingual transcription.

    1. Uses existing smart chunking (28s chunks) — preserves Whisper context
    2. For each chunk, transcribes with every candidate language
    3. Picks the best transcription per chunk by avg_logprob
    4. Filters hallucinated segments
    5. Stitches everything together with correct timestamps
    """
    chunks = _get_speech_chunks(audio_data, sr, phone_mode=phone_mode)
    logger.info(
        f"Multilingual: {len(chunks)} chunks, "
        f"candidate languages: {candidate_languages}"
    )

    all_segments = []
    transcripts = []
    chunk_languages = []
    prev_context = None

    for i, (start_sample, end_sample) in enumerate(chunks):
        chunk_audio = audio_data[start_sample:end_sample]
        chunk_duration = len(chunk_audio) / sr
        chunk_offset = start_sample / sr

        logger.info(
            f"  Chunk {i+1}/{len(chunks)}: {chunk_offset:.1f}s-{end_sample/sr:.1f}s "
            f"({chunk_duration:.1f}s)"
        )

        # Write chunk to temp file once (reused for all language passes)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, chunk_audio, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            best_text = ""
            best_segments = []
            best_lang = candidate_languages[0]
            best_logprob = float("-inf")

            for lang in candidate_languages:
                # Build options with forced language, NO English-biased initial_prompt
                options = get_options(lang)
                # Only pass hotwords for English — vocabulary terms are English and
                # cause hallucination loops on non-English passes (repeating "QBR SLA
                # SPICED..." or "Upsell Upsell Upsell..." on Tamil/Hindi audio)
                if lang != "en":
                    options.pop("hotwords", None)
                # Use previous transcription context instead of the default English prompt
                if prev_context:
                    options["initial_prompt"] = prev_context
                else:
                    # Remove default English prompt for forced-language transcription
                    options.pop("initial_prompt", None)

                text, _, segments = transcribe_chunk(model, tmp_path, options)

                # Filter hallucinations before scoring
                segments = _filter_hallucinations(segments)

                if not segments:
                    logger.info(f"    {lang}: no valid segments (all filtered)")
                    continue

                mean_lp = sum(s["avg_logprob"] for s in segments) / len(segments)
                logger.info(
                    f"    {lang}: {len(text)} chars, {len(segments)} segs, "
                    f"mean_logprob={mean_lp:.4f}"
                )

                if mean_lp > best_logprob:
                    best_logprob = mean_lp
                    best_text = text
                    best_segments = segments
                    best_lang = lang

            logger.info(
                f"  Chunk {i+1} winner: {best_lang} "
                f"(mean_logprob={best_logprob:.4f})"
            )

            # Check for untranscribed tail at end of chunk
            if best_segments:
                tail_segs = _retranscribe_tail(
                    model, chunk_audio, sr,
                    best_segments[-1]["end"], chunk_duration,
                    get_options, transcribe_chunk, best_lang, prev_context,
                )
                if tail_segs:
                    best_segments.extend(tail_segs)

            chunk_languages.append(best_lang)
            if best_text:
                # Rebuild text from non-hallucinated segments only
                best_text = " ".join(s["text"] for s in best_segments)
                best_text = re.sub(r"\s+", " ", best_text).strip()
                transcripts.append(best_text)
                prev_context = best_text[-200:]

            # Remap timestamps and tag with detected language
            for seg in best_segments:
                all_segments.append({
                    "id": 0,
                    "start": round(seg["start"] + chunk_offset, 2),
                    "end": round(seg["end"] + chunk_offset, 2),
                    "text": seg["text"],
                    "avg_logprob": round(seg["avg_logprob"], 4),
                    "no_speech_prob": round(seg["no_speech_prob"], 4),
                    "compression_ratio": round(seg["compression_ratio"], 2),
                    "detected_language": best_lang,
                })

        finally:
            os.unlink(tmp_path)

    # Assemble results
    full_text = " ".join(transcripts)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    if not chunk_languages:
        overall_lang = "unknown"
    elif len(set(chunk_languages)) == 1:
        overall_lang = chunk_languages[0]
    else:
        lang_counts = Counter(chunk_languages)
        overall_lang = f"mixed ({', '.join(f'{lang}:{count}' for lang, count in lang_counts.most_common())})"

    for idx, seg in enumerate(all_segments):
        seg["id"] = idx

    logger.info(
        f"Multilingual transcription complete: {len(chunks)} chunks, "
        f"{len(full_text)} chars, {len(all_segments)} segments, lang={overall_lang}"
    )

    return {
        "text": full_text,
        "language": overall_lang,
        "char_count": len(full_text),
        "segments": all_segments,
    }


# ═══════════════════════════════════════════════════════════════════
# Engine-specific: chunk transcription
# ═══════════════════════════════════════════════════════════════════

def _transcribe_chunk_ow(model, audio_path: str, options: dict) -> Tuple[str, str, list]:
    """
    Transcribe a single chunk with openai-whisper.
    Returns (text, language, raw_segments_list).
    """
    result = model.transcribe(audio_path, **options)
    text = result.get("text", "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    lang = result.get("language", "unknown")
    raw_segments = result.get("segments", [])
    # Convert to common segment format
    segments = []
    for seg in raw_segments:
        seg_text = seg.get("text", "").strip()
        if seg_text:
            segments.append({
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg_text,
                "avg_logprob": seg.get("avg_logprob", 0.0),
                "no_speech_prob": seg.get("no_speech_prob", 0.0),
                "compression_ratio": seg.get("compression_ratio", 0.0),
            })
    return text, lang, segments


def _transcribe_chunk_fw(model, audio_path: str, options: dict) -> Tuple[str, str, list]:
    """
    Transcribe a single chunk with faster-whisper.
    Returns (text, language, raw_segments_list).
    """
    segments_gen, info = model.transcribe(audio_path, **options)
    raw_segments = list(segments_gen)

    text_parts = []
    segments = []
    for seg in raw_segments:
        seg_text = seg.text.strip()
        if seg_text:
            text_parts.append(seg_text)
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg_text,
                "avg_logprob": seg.avg_logprob,
                "no_speech_prob": seg.no_speech_prob,
                "compression_ratio": seg.compression_ratio,
            })

    text = " ".join(text_parts)
    text = re.sub(r"\s+", " ", text).strip()
    lang = info.language or "unknown"
    return text, lang, segments


# ═══════════════════════════════════════════════════════════════════
# IndicTrans2: text-to-text translation for Indian languages
# ═══════════════════════════════════════════════════════════════════

# Whisper ISO 639-1 → IndicTrans2 BCP-47+script codes
INDIC_LANG_MAP = {
    "ta": "tam_Taml",  # Tamil
    "hi": "hin_Deva",  # Hindi
    "bn": "ben_Beng",  # Bengali
    "gu": "guj_Gujr",  # Gujarati
    "kn": "kan_Knda",  # Kannada
    "ml": "mal_Mlym",  # Malayalam
    "mr": "mar_Deva",  # Marathi
    "pa": "pan_Guru",  # Punjabi
    "te": "tel_Telu",  # Telugu
    "ur": "urd_Arab",  # Urdu
    "or": "ory_Orya",  # Odia
    "as": "asm_Beng",  # Assamese
    "ne": "npi_Deva",  # Nepali
    "si": "sin_Sinh",  # Sinhala
}

# Unicode range → IndicTrans2 language code (for script-based detection)
_SCRIPT_TO_INDIC = [
    (0x0B80, 0x0BFF, "tam_Taml"),  # Tamil
    (0x0900, 0x097F, "hin_Deva"),  # Devanagari (Hindi/Marathi/Nepali — default Hindi)
    (0x0980, 0x09FF, "ben_Beng"),  # Bengali / Assamese
    (0x0A80, 0x0AFF, "guj_Gujr"),  # Gujarati
    (0x0C80, 0x0CFF, "kan_Knda"),  # Kannada
    (0x0D00, 0x0D7F, "mal_Mlym"),  # Malayalam
    (0x0A00, 0x0A7F, "pan_Guru"),  # Gurmukhi (Punjabi)
    (0x0C00, 0x0C7F, "tel_Telu"),  # Telugu
    (0x0B00, 0x0B7F, "ory_Orya"),  # Odia
    (0x0D80, 0x0DFF, "sin_Sinh"),  # Sinhala
    (0x0600, 0x06FF, "urd_Arab"),  # Arabic script (Urdu)
]


def _detect_indic_lang_from_text(text: str) -> Optional[str]:
    """Detect Indian language from actual text script using Unicode ranges.

    Returns the IndicTrans2 BCP-47+script code if the text is predominantly
    in an Indian script, or None if it's not Indian (e.g. CJK, Cyrillic, Arabic non-Urdu).
    """
    if not text:
        return None
    script_counts = {}  # indic_code -> count
    for ch in text:
        cp = ord(ch)
        for start, end, code in _SCRIPT_TO_INDIC:
            if start <= cp <= end:
                script_counts[code] = script_counts.get(code, 0) + 1
                break
    if not script_counts:
        return None
    # Return the dominant script
    return max(script_counts, key=script_counts.get)


_indictrans2_model = None
_indictrans2_processor = None
_indictrans2_tokenizer = None
_indictrans2_lock = threading.Lock()


def _get_indictrans2():
    """Lazy-load IndicTrans2 model, tokenizer, and processor (singleton)."""
    global _indictrans2_model, _indictrans2_processor, _indictrans2_tokenizer
    if _indictrans2_model is None:
        with _indictrans2_lock:
            if _indictrans2_model is None:
                from IndicTransToolkit import IndicProcessor
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
                from app.audio_preprocess.config import preprocess_config as cfg

                model_name = cfg.INDICTRANS2_MODEL
                device = cfg.INDICTRANS2_DEVICE
                logger.info(f"Loading IndicTrans2 model: {model_name} on {device}")

                _indictrans2_tokenizer = AutoTokenizer.from_pretrained(
                    model_name, trust_remote_code=True,
                )
                _indictrans2_model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_name, trust_remote_code=True,
                )
                _indictrans2_model = _indictrans2_model.to(device)
                _indictrans2_model.eval()
                _indictrans2_processor = IndicProcessor(inference=True)
                logger.info(f"IndicTrans2 loaded: {model_name}")
    return _indictrans2_model, _indictrans2_processor, _indictrans2_tokenizer


def _translate_text_indictrans2(texts: list, src_lang: str) -> list:
    """Batch translate texts from an Indian language to English using IndicTrans2.

    Args:
        texts: List of source-language strings.
        src_lang: IndicTrans2 BCP-47+script code (e.g. "tam_Taml").

    Returns:
        List of English translations, same length as texts.
    """
    from app.audio_preprocess.config import preprocess_config as cfg

    model, processor, tokenizer = _get_indictrans2()
    device = cfg.INDICTRANS2_DEVICE

    batch = processor.preprocess_batch(texts, src_lang=src_lang, tgt_lang="eng_Latn")
    inputs = tokenizer(batch, truncation=True, padding="longest", return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        generated = model.generate(
            **inputs, num_beams=5, num_return_sequences=1, max_length=256,
        )

    decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
    return processor.postprocess_batch(decoded, lang="eng_Latn")


# ═══════════════════════════════════════════════════════════════════
# Translation: Whisper built-in task="translate" (→ English)
# ═══════════════════════════════════════════════════════════════════

def _is_text_actually_english(text: str) -> bool:
    """Check if segment text is actually English based on script analysis.

    Returns True if text is predominantly Latin script — meaning translation
    is unnecessary even if Whisper's language detector said non-English.
    Returns False if text has significant non-Latin characters (Tamil,
    Devanagari, etc.) — meaning translation is needed even if detector said English.
    """
    if not text:
        return True
    alpha_chars = [ch for ch in text if ch.isalpha()]
    if not alpha_chars:
        return True  # No alphabetic chars (numbers/symbols only)
    # Everything above Latin Extended-B (U+024F) is non-Latin
    non_latin = sum(1 for ch in alpha_chars if ord(ch) > 0x024F)
    # If less than 15% non-Latin, treat as English
    return (non_latin / len(alpha_chars)) < 0.15


def _translate_segment_fw(model, audio_path: str, language: str, word_timestamps: bool = True, hotwords: str = "") -> dict:
    """Translate a single audio segment to English using faster-whisper."""
    kwargs = dict(
        task="translate",
        language=language,
        beam_size=5,
        best_of=1,
        temperature=0.0,
        word_timestamps=word_timestamps,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        vad_filter=False,
    )
    # Pass hotwords so English translation output benefits from vocabulary boosting
    if hotwords:
        kwargs["hotwords"] = hotwords
    segments_gen, info = model.transcribe(audio_path, **kwargs)

    text_parts = []
    words = []
    for seg in segments_gen:
        seg_text = seg.text.strip()
        if seg_text:
            text_parts.append(seg_text)
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "score": round(w.probability, 4),
                })

    return {
        "text": " ".join(text_parts).strip(),
        "words": words,
    }


def _translate_segment_ow(model, audio_path: str, language: str, word_timestamps: bool = True) -> dict:
    """Translate a single audio segment to English using openai-whisper."""
    result = model.transcribe(
        audio_path,
        task="translate",
        language=language,
        beam_size=5,
        best_of=1,
        temperature=0,
        word_timestamps=word_timestamps,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        verbose=False,
    )

    text = result.get("text", "").strip()
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w.get("word", "").strip(),
                "start": round(w.get("start", 0), 3),
                "end": round(w.get("end", 0), 3),
                "score": round(w.get("probability", 0), 4),
            })

    return {
        "text": text,
        "words": words,
    }


def _translate_segments(
    model, audio_data: np.ndarray, sr: int, segments: list, engine: str,
    word_timestamps: bool = True, hotwords: str = "",
) -> None:
    """Translate non-English segments to English.

    Hybrid approach:
    - Indian languages (in INDIC_LANG_MAP): IndicTrans2 for accurate text,
      Whisper translate for approximate word-level timestamps.
    - Other languages: Whisper translate for both text and word timestamps.

    Uses segment-level language validation: checks the actual text script, not just
    Whisper's detected_language. Latin-script text is skipped (already English),
    non-Latin text is translated even if detected_language says 'en'.

    Modifies segments in place: adds 'translation' (str) and optionally
    'translation_words' (list) when word_timestamps=True.
    """
    # For faster-whisper, wrap translate function to pass hotwords so English
    # translation output benefits from vocabulary boosting
    if engine == "faster-whisper" and hotwords:
        translate_fn = lambda m, p, l, wt, _hw=hotwords: _translate_segment_fw(m, p, l, wt, hotwords=_hw)
    elif engine == "faster-whisper":
        translate_fn = _translate_segment_fw
    else:
        translate_fn = _translate_segment_ow
    skipped_latin = 0
    indic_count = 0
    whisper_count = 0

    # ── First pass: classify segments needing translation ──
    to_translate = []  # (index_into_segments, seg, lang)
    for seg in segments:
        lang = seg.get("detected_language", "")
        text = seg.get("text", "")

        text_is_english = _is_text_actually_english(text)
        if text_is_english:
            if lang and lang != "en":
                skipped_latin += 1
                logger.debug(
                    f"Skipping translation for segment {seg.get('id', '?')}: "
                    f"detected as '{lang}' but text is Latin script"
                )
            continue

        to_translate.append((seg, lang))

    if not to_translate:
        if skipped_latin:
            logger.info(f"Skipped {skipped_latin} segments: detected non-English but text is Latin script")
        logger.info("Translation complete: 0 segments needed translation")
        return

    # ── IndicTrans2 batch translation for Indian languages ──
    # Route by ACTUAL TEXT SCRIPT, not Whisper's detected language.
    # e.g. detected "en" but Tamil text → IndicTrans2, detected "ta" but Latin text → already skipped above.
    indic_segments_by_lang = {}  # src_lang_code -> [(seg, text)]
    non_indic_segments = []      # [(seg, lang)]

    for seg, lang in to_translate:
        text = seg.get("text", "")
        indic_code = _detect_indic_lang_from_text(text)
        if indic_code:
            indic_segments_by_lang.setdefault(indic_code, []).append((seg, text))
        else:
            non_indic_segments.append((seg, lang))

    # Batch translate Indian languages via IndicTrans2
    for src_lang, seg_text_pairs in indic_segments_by_lang.items():
        texts = [text for _, text in seg_text_pairs]
        try:
            translations = _translate_text_indictrans2(texts, src_lang)
            for i, (seg, _) in enumerate(seg_text_pairs):
                if translations[i]:
                    seg["translation"] = translations[i]
                    indic_count += 1
                    logger.info(
                        f"  IndicTrans2 segment {seg.get('id', '?')} ({src_lang}->eng_Latn): "
                        f"'{translations[i][:80]}'"
                    )
        except Exception as e:
            logger.warning(f"IndicTrans2 batch translation failed for {src_lang}: {e}")
            # Fall back to Whisper translate for these segments
            for seg, _ in seg_text_pairs:
                non_indic_segments.append((seg, seg.get("detected_language", "")))

    # ── Whisper translate for non-Indian segments (text + word timestamps) ──
    # Indian segments already have IndicTrans2 text; their original-language word
    # timestamps are in seg["words"] from ASR + ctc-forced-aligner.
    for seg, lang in non_indic_segments:
        translate_lang = lang if lang else None

        start_sample = int(seg["start"] * sr)
        end_sample = int(seg["end"] * sr)
        seg_audio = audio_data[start_sample:end_sample]

        if len(seg_audio) < int(sr * 0.3):
            logger.debug(
                f"Skipping Whisper translate for segment {seg.get('id', '?')}: "
                f"too short ({len(seg_audio)/sr:.2f}s)"
            )
            continue

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, seg_audio, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            result = translate_fn(model, tmp_path, translate_lang, word_timestamps)
            if result["text"]:
                seg["translation"] = result["text"]
                if word_timestamps and result["words"]:
                    seg["translation_words"] = []
                    for w in result["words"]:
                        seg["translation_words"].append({
                            "word": w["word"],
                            "start": round(w["start"] + seg["start"], 3),
                            "end": round(w["end"] + seg["start"], 3),
                            "score": w["score"],
                        })
                whisper_count += 1
                logger.info(
                    f"  Whisper segment {seg.get('id', '?')} ({translate_lang}->en): "
                    f"'{result['text'][:80]}'"
                )
        except Exception as e:
            logger.warning(f"Whisper translate failed for segment {seg.get('id', '?')}: {e}")
        finally:
            os.unlink(tmp_path)

    if skipped_latin:
        logger.info(f"Skipped {skipped_latin} segments: detected non-English but text is Latin script")
    logger.info(
        f"Translation complete: {indic_count} via IndicTrans2, "
        f"{whisper_count} via Whisper translate"
    )


# ═══════════════════════════════════════════════════════════════════
# Main Transcribe Entry Point
# ═══════════════════════════════════════════════════════════════════

def transcribe(
    audio_path: Path,
    model_size: str = "medium",
    device: str = "cpu",
    language: Optional[str] = None,
    engine: str = "faster-whisper",
    compute_type: str = "int8",
    multilingual: bool = False,
    lid_confidence_threshold: float = 0.8,
    lid_min_speech_ms: int = 100,
    translate_to_english: bool = False,
    align: bool = False,
    hotwords: str = "",
    phone_mode: bool = False,
    initial_prompt: Optional[str] = None,
) -> dict:
    """
    Transcribe audio file using the selected engine with smart chunking.

    For audio > 30s: splits at VAD speech boundaries, transcribes each chunk
    with per-chunk language detection (when language=None), carries context
    between chunks, and remaps segment timestamps to original audio time.

    When multilingual=True and language=None: uses fine-grained LID + dual-pass
    fallback for mixed-language audio (Approach 3).

    Returns dict with:
        - text: full transcription string
        - language: detected language (or 'mixed' if multiple detected)
        - char_count: character count
        - segments: list of {start, end, text, ...} with timestamps
    """
    logger.info(
        f"Transcribing: {audio_path} (engine={engine}, model={model_size}, "
        f"lang={language or 'auto-detect'}, multilingual={multilingual})"
    )

    if phone_mode:
        logger.info("Phone/VoIP mode active: lowered VAD thresholds, disabled no_speech_threshold")

    # Load the selected engine
    if engine == "faster-whisper":
        model = _load_faster_whisper(model_size, device, compute_type)
        # Wrap _get_options_fw to inject hotwords, phone_mode, and initial_prompt for all calls
        if hotwords:
            get_options = lambda lang, _hw=hotwords, _pm=phone_mode, _ip=initial_prompt: _get_options_fw(lang, hotwords=_hw, phone_mode=_pm, initial_prompt=_ip)
            logger.info(f"Hotword boosting active: {len(hotwords.split())} terms")
        else:
            get_options = lambda lang, _pm=phone_mode, _ip=initial_prompt: _get_options_fw(lang, phone_mode=_pm, initial_prompt=_ip)
        transcribe_chunk = _transcribe_chunk_fw
    else:
        model = _load_openai_whisper(model_size, device)
        get_options = lambda lang, _pm=phone_mode, _ip=initial_prompt: _get_options_ow(lang, phone_mode=_pm, initial_prompt=_ip)
        transcribe_chunk = _transcribe_chunk_ow

    # Read audio to check duration
    audio_data, sr = sf.read(str(audio_path), dtype="float32")
    duration = len(audio_data) / sr
    logger.info(f"Audio duration: {duration:.1f}s")

    # Multilingual mode: Language Survey + Chunk-Level Dual-Pass
    if multilingual and language is None:
        logger.info("Multilingual mode: Language Survey + Chunk-Level Dual-Pass")
        detect_fn = _detect_language_fw if engine == "faster-whisper" else _detect_language_ow

        # Survey: sample 5 x 15s segments to identify candidate languages
        candidate_langs = _survey_languages(model, audio_data, sr, detect_fn)

        if len(candidate_langs) == 1:
            logger.info(
                f"Multilingual survey: all samples detected as '{candidate_langs[0]}', "
                f"using standard single-language transcription"
            )
            # Fall through to standard path with forced language
            language = candidate_langs[0]
        else:
            result = _transcribe_multilingual(
                model, audio_data, sr, candidate_langs,
                get_options, transcribe_chunk, phone_mode=phone_mode,
            )
            if translate_to_english:
                logger.info("=== Translating non-English segments to English ===")
                _translate_segments(model, audio_data, sr, result["segments"], engine, word_timestamps=align, hotwords=hotwords)
            return result

    # Short audio: single-pass transcription
    if duration <= 30.0:
        logger.info("Short audio, using single-pass transcription")
        options = get_options(language)
        text, detected_lang, segments = transcribe_chunk(model, str(audio_path), options)

        # Filter hallucinated segments
        pre_filter_count = len(segments)
        segments = _filter_hallucinations(segments)
        if pre_filter_count != len(segments):
            logger.info(f"Filtered {pre_filter_count - len(segments)} hallucinated segments")
            text = " ".join(s["text"] for s in segments)
            text = re.sub(r"\s+", " ", text).strip()

        # Format segments
        formatted_segments = []
        for i, seg in enumerate(segments):
            formatted_segments.append({
                "id": i,
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"],
                "avg_logprob": round(seg["avg_logprob"], 4),
                "no_speech_prob": round(seg["no_speech_prob"], 4),
                "compression_ratio": round(seg["compression_ratio"], 2),
                "detected_language": detected_lang,
            })

        # Confidence-based re-transcription for short audio
        from app.audio_preprocess.config import preprocess_config as _cfg
        formatted_segments = _retranscribe_low_confidence(
            model, audio_data, sr,
            formatted_segments, get_options, transcribe_chunk, language,
            threshold=_cfg.CONFIDENCE_RETRANSCRIBE_THRESHOLD,
            retry_beam_size=_cfg.CONFIDENCE_RETRANSCRIBE_BEAM_SIZE,
            retry_best_of=_cfg.CONFIDENCE_RETRANSCRIBE_BEST_OF,
            min_region_seconds=_cfg.CONFIDENCE_RETRANSCRIBE_MIN_REGION_SECONDS,
            max_ratio=_cfg.CONFIDENCE_RETRANSCRIBE_MAX_RATIO,
        )
        text = " ".join(s["text"] for s in formatted_segments)
        text = re.sub(r"\s+", " ", text).strip()

        logger.info(f"Transcription done: {len(text)} chars, {len(formatted_segments)} segments, lang={detected_lang}")
        if translate_to_english:
            logger.info("=== Translating non-English segments to English ===")
            _translate_segments(model, audio_data, sr, formatted_segments, engine, word_timestamps=align, hotwords=hotwords)
        return {
            "text": text,
            "language": detected_lang,
            "char_count": len(text),
            "segments": formatted_segments,
        }

    # Long audio: smart chunking
    # Tier 3.2 — batched Whisper inference. Default ON as of May 17 2026 —
    # faster-whisper's BatchedInferencePipeline packs multiple chunks into
    # one GPU forward pass for ~30-50% faster ASR. Trade-off: loss of
    # inter-chunk initial_prompt context carry-over → ~1-3% absolute WER
    # increase on long calls with consistent jargon. Active only for
    # faster-whisper engine on CUDA; falls back to sequential otherwise.
    # Disable per-call with WHISPER_BATCHED_MODE=false if you hit accuracy
    # regressions on mixed-language or jargon-heavy calls.
    batched_enabled = os.getenv("WHISPER_BATCHED_MODE", "true").lower() == "true"
    if batched_enabled and engine == "faster-whisper" and device == "cuda":
        try:
            batch_size = int(os.getenv("WHISPER_BATCH_SIZE", "8"))
        except ValueError:
            batch_size = 8
        # Batched mode loses per-chunk language detection. When caller didn't
        # specify a language, default to English (configurable via
        # WHISPER_BATCHED_LANGUAGE). For mixed-language calls, callers should
        # either set this env to the dominant language OR disable batched mode.
        batched_lang = language or os.getenv("WHISPER_BATCHED_LANGUAGE", "en")
        logger.info(
            f"Tier 3.2 — batched mode enabled (batch_size={batch_size}, language={batched_lang}); "
            f"sequential context carry-over disabled"
        )
        try:
            from faster_whisper import BatchedInferencePipeline
            batched_pipeline = BatchedInferencePipeline(model=model)
            # Note: initial_prompt is supported but applies to ALL chunks identically
            # (no carry-over). Fixed per-meeting prompt is the right anchor here.
            batched_options = {
                "language": batched_lang,
                "batch_size": batch_size,
                "word_timestamps": align,
            }
            if initial_prompt:
                batched_options["initial_prompt"] = initial_prompt
            seg_iter, info = batched_pipeline.transcribe(str(audio_path), **batched_options)
            segments_list = []
            for seg in seg_iter:
                seg_dict = {
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text or "",
                    "no_speech_prob": getattr(seg, "no_speech_prob", 0.0),
                    "avg_logprob": getattr(seg, "avg_logprob", 0.0),
                    "compression_ratio": getattr(seg, "compression_ratio", 1.0),
                    "detected_language": info.language if info else (language or "en"),
                }
                if align and getattr(seg, "words", None):
                    seg_dict["words"] = [
                        {"word": w.word, "start": float(w.start), "end": float(w.end), "score": float(getattr(w, "probability", 0.0))}
                        for w in seg.words
                    ]
                segments_list.append(seg_dict)
            raw_batched_count = len(segments_list)

            # Tier 3.2 post-processing: tail re-transcribe + re-chunk at silences.
            # BatchedInferencePipeline returns ~30-sec windows as single segments,
            # skipping our sequential path's tail-recovery + smart chunking. The
            # two steps below (a) recover speech batched mode left untranscribed
            # at segment tails, (b) split the long segments at internal silences
            # so downstream stages (hallucination filter, speaker assignment, LLM
            # correction batching) see natural prosodic chunks instead of model-
            # window-sized blobs. Only runs when word timestamps are available
            # (align=True) — without them we can't redistribute words to sub-segs.
            if align:
                extended: list = []
                for seg in segments_list:
                    extended.append(seg)
                    if seg.get("words"):
                        tail_extras = _retranscribe_batched_segment_tail(
                            model, audio_data, sr, seg,
                            get_options, transcribe_chunk,
                            batched_lang, initial_prompt,
                        )
                        if tail_extras:
                            extended.extend(tail_extras)

                final_segments: list = []
                for seg in extended:
                    seg_dur = seg.get("end", 0) - seg.get("start", 0)
                    if seg_dur <= 12.0 or not seg.get("words"):
                        final_segments.append(seg)
                        continue
                    silences = _silero_silences_in_segment(
                        audio_data, sr, seg["start"], seg["end"], min_silence_ms=300
                    )
                    final_segments.extend(_rechunk_segment_at_silences(seg, silences))

                final_segments.sort(key=lambda s: s.get("start", 0))
                segments_list = final_segments
                logger.info(
                    f"Tier 3.2 post-processing: {raw_batched_count} batched segments → "
                    f"{len(extended)} after tail recovery → {len(segments_list)} after re-chunking"
                )

            # Filter hallucinations same as sequential path (now thresholds
            # work correctly because segments are sentence-scale, not 30s)
            pre_count = len(segments_list)
            segments_list = _filter_hallucinations(segments_list)
            if len(segments_list) < pre_count:
                logger.info(f"Filtered {pre_count - len(segments_list)} hallucinated batched segments")

            full_text_parts = [s.get("text", "") for s in segments_list]
            result = {
                "text": " ".join(full_text_parts).strip(),
                "language": info.language if info else (language or "en"),
                "char_count": sum(len(t) for t in full_text_parts),
                "segments": segments_list,
            }
            if translate_to_english:
                logger.info("=== Translating non-English segments to English (batched path) ===")
                _translate_segments(model, audio_data, sr, result["segments"], engine, word_timestamps=align, hotwords=hotwords)
            return result
        except ImportError as e:
            logger.warning(f"Tier 3.2 — BatchedInferencePipeline import failed; falling back to sequential: {e}")
        except Exception as e:
            logger.warning(f"Tier 3.2 — batched transcription error; falling back to sequential: {e}")

    logger.info("Long audio, using smart chunking with per-chunk processing (sequential)")
    result = _transcribe_chunked(model, audio_data, sr, language, get_options, transcribe_chunk, phone_mode=phone_mode)
    if translate_to_english:
        logger.info("=== Translating non-English segments to English ===")
        _translate_segments(model, audio_data, sr, result["segments"], engine, word_timestamps=align, hotwords=hotwords)
    return result


def _silero_silences_in_segment(audio_data: np.ndarray, sr: int,
                                  seg_start_s: float, seg_end_s: float,
                                  min_silence_ms: int = 300) -> list:
    """Tier 3.2 helper — find silence boundaries within a batched segment.

    Runs Silero VAD on the segment's audio slice and returns silences as
    (sil_start_s, sil_end_s) tuples in ABSOLUTE audio time. Used by
    `_rechunk_segment_at_silences` to split BatchedInferencePipeline's
    30-sec segments at natural prosodic pauses.
    """
    start_sample = max(0, int(seg_start_s * sr))
    end_sample = min(len(audio_data), int(seg_end_s * sr))
    seg_audio = audio_data[start_sample:end_sample]
    if len(seg_audio) < int(sr * 0.5):
        return []

    model, utils = _load_vad()
    get_speech_timestamps = utils[0]
    wav_tensor = torch.from_numpy(seg_audio)
    speech_ts = get_speech_timestamps(
        wav_tensor, model,
        sampling_rate=sr,
        threshold=0.5,
        min_speech_duration_ms=200,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=50,
    )
    if len(speech_ts) < 2:
        return []

    silences = []
    for i in range(len(speech_ts) - 1):
        sil_start_abs = speech_ts[i]["end"] / sr + seg_start_s
        sil_end_abs = speech_ts[i + 1]["start"] / sr + seg_start_s
        if (sil_end_abs - sil_start_abs) >= (min_silence_ms / 1000.0):
            silences.append((sil_start_abs, sil_end_abs))
    return silences


def _compression_ratio_estimate(text: str) -> float:
    """Rough proxy for Whisper's compression_ratio metric — token count /
    unique-token count. Used for re-derived sub-segments that don't carry
    the original Whisper metric forward."""
    import re as _re
    tokens = _re.findall(r"\w+", text.lower())
    if not tokens:
        return 1.0
    return len(tokens) / max(1, len(set(tokens)))


def _rechunk_segment_at_silences(seg: dict, silences: list) -> list:
    """Tier 3.2 helper — split a batched segment into sub-segments at silences.

    Walks the segment's `words` list and groups them by silence boundaries.
    Returns either the original segment (if no useful split points) or a
    list of sub-segments with redistributed words + recomputed compression
    ratio. Other metadata fields (no_speech_prob, avg_logprob, language) are
    inherited from the parent — they're approximate but the only signal we
    have without re-running Whisper.
    """
    words = seg.get("words")
    if not silences or not words:
        return [seg]

    split_points = [(s + e) / 2.0 for s, e in silences]
    groups: list = [[]]
    next_split_idx = 0
    for w in words:
        while next_split_idx < len(split_points) and w.get("end", 0) > split_points[next_split_idx]:
            if groups[-1]:
                groups.append([])
            next_split_idx += 1
        groups[-1].append(w)

    groups = [g for g in groups if g]
    if len(groups) <= 1:
        return [seg]

    sub_segments = []
    for group in groups:
        sub_text = " ".join(w.get("word", "") for w in group).strip()
        sub_segments.append({
            "start": group[0].get("start", seg["start"]),
            "end": group[-1].get("end", seg["end"]),
            "text": sub_text,
            "no_speech_prob": seg.get("no_speech_prob", 0.0),
            "avg_logprob": seg.get("avg_logprob", 0.0),
            "compression_ratio": _compression_ratio_estimate(sub_text),
            "detected_language": seg.get("detected_language", "en"),
            "words": group,
        })
    return sub_segments


def _retranscribe_batched_segment_tail(
    model, audio_data: np.ndarray, sr: int,
    seg: dict, get_options, transcribe_chunk,
    language: Optional[str], initial_prompt: Optional[str],
) -> list:
    """Tier 3.2 helper — recover speech that batched Whisper dropped at segment tail.

    BatchedInferencePipeline's 30-sec windows can have an internal premature-
    stop (model attention sees zero-padding and emits an end). If the
    segment's last WORD ends >1.5s before segment["end"], re-transcribe just
    the tail with `no_speech_threshold=None` and append any recovered
    segments. Mirrors `_retranscribe_tail` for sequential mode.
    """
    words = seg.get("words") or []
    if not words:
        return []
    last_word_end = float(words[-1].get("end", seg["end"]))
    tail_gap = seg["end"] - last_word_end
    if tail_gap < TAIL_MIN_GAP_SECONDS:
        return []

    tail_start = max(seg["start"], last_word_end - TAIL_OVERLAP_SECONDS)
    tail_start_sample = int(tail_start * sr)
    tail_end_sample = min(len(audio_data), int(seg["end"] * sr))
    tail_audio = audio_data[tail_start_sample:tail_end_sample]
    if len(tail_audio) < int(sr * 0.5):
        return []

    logger.info(
        f"  Tier 3.2 batched tail re-transcribe: {tail_gap:.1f}s gap in segment "
        f"[{seg['start']:.1f}-{seg['end']:.1f}s], recovering from {tail_start:.1f}s"
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        sf.write(tmp.name, tail_audio, sr, subtype="PCM_16")
        tmp_path = tmp.name

    try:
        options = get_options(language)
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if "no_speech_threshold" in options:
            options["no_speech_threshold"] = None

        _, _, tail_segments = transcribe_chunk(model, tmp_path, options)
        tail_segments = _filter_hallucinations(tail_segments)

        new_segments = []
        for ts in tail_segments:
            # Skip content already in the original segment (overlap zone)
            if ts.get("end", 0) <= (last_word_end - tail_start):
                continue
            ts["start"] = tail_start + ts.get("start", 0)
            ts["end"] = tail_start + ts.get("end", 0)
            if "words" in ts:
                for w in ts["words"]:
                    if "start" in w:
                        w["start"] = tail_start + w["start"]
                    if "end" in w:
                        w["end"] = tail_start + w["end"]
            new_segments.append(ts)
        return new_segments
    except Exception as e:
        logger.warning(f"  Tier 3.2 batched tail re-transcribe failed (non-fatal): {e}")
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _retranscribe_tail(
    model, chunk_audio: np.ndarray, sr: int,
    last_seg_end: float, chunk_duration: float,
    get_options, transcribe_chunk, language: Optional[str],
    prev_context: Optional[str],
) -> list:
    """
    Re-transcribe the untranscribed tail of a chunk when Whisper stops early.

    Whisper processes audio in 30s windows. When a chunk is shorter (e.g. 15s),
    it gets zero-padded. The model's attention sees the upcoming silence and may
    stop generating text before the actual end of speech. This function detects
    that gap and re-transcribes the missed portion.

    Returns additional segments with timestamps relative to the chunk start.
    """
    gap = chunk_duration - last_seg_end
    if gap < TAIL_MIN_GAP_SECONDS:
        return []

    # Start from a bit before the last segment ended (overlap for decoder context)
    tail_start = max(0.0, last_seg_end - TAIL_OVERLAP_SECONDS)
    tail_start_sample = int(tail_start * sr)
    tail_audio = chunk_audio[tail_start_sample:]

    if len(tail_audio) < int(sr * 0.5):  # Need at least 0.5s
        return []

    logger.info(
        f"    Tail re-transcription: {gap:.1f}s uncovered at chunk end, "
        f"re-transcribing {tail_start:.1f}s-{chunk_duration:.1f}s"
    )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        sf.write(tmp.name, tail_audio, sr, subtype="PCM_16")
        tmp_path = tmp.name

    try:
        options = get_options(language)
        if prev_context:
            options["initial_prompt"] = prev_context
        # Disable no_speech_threshold for the tail — avoids the same
        # premature-stop issue that caused the gap in the first place
        if "no_speech_threshold" in options:
            options["no_speech_threshold"] = None

        _, _, tail_segments = transcribe_chunk(model, tmp_path, options)
        tail_segments = _filter_hallucinations(tail_segments)

        # Keep segments that extend past the overlap zone
        # (a segment may start in the overlap but contain new content)
        overlap_boundary = last_seg_end - tail_start  # in tail-relative time
        new_segments = []
        for seg in tail_segments:
            if seg["end"] > overlap_boundary:
                new_segments.append({
                    **seg,
                    "start": seg["start"] + tail_start,
                    "end": seg["end"] + tail_start,
                })

        if new_segments:
            logger.info(f"    Tail recovered {len(new_segments)} segments")
        else:
            logger.info("    Tail: no new segments beyond overlap zone")

        return new_segments
    except Exception as e:
        logger.warning(f"    Tail re-transcription failed: {e}")
        return []
    finally:
        os.unlink(tmp_path)


def _rescue_gaps(
    model, audio_data: np.ndarray, sr: int,
    segments: list, chunks: List[Tuple[int, int]],
    get_options, transcribe_chunk, language: Optional[str],
) -> list:
    """
    Rescue pass: find gaps between transcribed segments where speech exists
    but Whisper produced nothing. Re-transcribe those gaps as isolated chunks.

    This catches cases where Whisper skips quiet speech in the middle of a
    louder chunk (e.g., a muffled speaker surrounded by a loud speaker).

    Returns additional segments to be merged into the final result.
    """
    if not segments or not chunks:
        return []

    rescued = []

    # Build a timeline of chunk boundaries (what audio was sent to Whisper)
    chunk_boundaries = [(s / sr, e / sr) for s, e in chunks]
    total_duration = len(audio_data) / sr

    # Sort segments by start time
    sorted_segs = sorted(segments, key=lambda s: s["start"])

    # Find gaps between consecutive segments
    gaps = []
    for i in range(len(sorted_segs) - 1):
        gap_start = sorted_segs[i]["end"]
        gap_end = sorted_segs[i + 1]["start"]
        gap_dur = gap_end - gap_start

        if gap_dur >= GAP_RESCUE_MIN_SECONDS:
            # Verify this gap is within a chunk (i.e., audio was sent to Whisper but it produced nothing)
            for cb_start, cb_end in chunk_boundaries:
                if gap_start >= cb_start and gap_end <= cb_end:
                    gaps.append((gap_start, gap_end))
                    break

    if not gaps:
        return []

    logger.info(f"Gap rescue: found {len(gaps)} gaps to re-transcribe")

    for gap_start, gap_end in gaps:
        # Add padding around the gap for decoder context
        pad = 0.5
        rescue_start = max(0.0, gap_start - pad)
        rescue_end = min(total_duration, gap_end + pad)

        start_sample = int(rescue_start * sr)
        end_sample = int(rescue_end * sr)
        rescue_audio = audio_data[start_sample:end_sample]

        if len(rescue_audio) < int(sr * 0.5):
            continue

        logger.info(
            f"  Rescue: gap {gap_start:.1f}s-{gap_end:.1f}s "
            f"(re-transcribing {rescue_start:.1f}s-{rescue_end:.1f}s)"
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, rescue_audio, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            options = get_options(language)
            # Disable no_speech_threshold — the gap exists because Whisper
            # thought it was silence on the first pass
            if "no_speech_threshold" in options:
                options["no_speech_threshold"] = None

            _, rescue_lang, rescue_segs = transcribe_chunk(model, tmp_path, options)
            rescue_segs = _filter_hallucinations(rescue_segs)

            # Keep only segments that fall within the original gap
            # (padding may produce content we already have)
            gap_relative_start = gap_start - rescue_start
            gap_relative_end = gap_end - rescue_start

            for seg in rescue_segs:
                # Segment must overlap with the gap region
                if seg["end"] > gap_relative_start and seg["start"] < gap_relative_end:
                    rescued.append({
                        "id": 0,
                        "start": round(seg["start"] + rescue_start, 2),
                        "end": round(seg["end"] + rescue_start, 2),
                        "text": seg["text"],
                        "avg_logprob": round(seg["avg_logprob"], 4),
                        "no_speech_prob": round(seg["no_speech_prob"], 4),
                        "compression_ratio": round(seg["compression_ratio"], 2),
                        "detected_language": rescue_lang,
                    })

        except Exception as e:
            logger.warning(f"  Rescue failed for gap {gap_start:.1f}-{gap_end:.1f}s: {e}")
        finally:
            os.unlink(tmp_path)

    if rescued:
        logger.info(f"Gap rescue: recovered {len(rescued)} segments from {len(gaps)} gaps")
    else:
        logger.info("Gap rescue: no new segments recovered")

    return rescued


def _retranscribe_low_confidence(
    model, audio_data: np.ndarray, sr: int,
    segments: list, get_options, transcribe_chunk,
    language: Optional[str],
    threshold: float = -0.5,
    retry_beam_size: int = 10,
    retry_best_of: int = 3,
    min_region_seconds: float = 1.0,
    max_ratio: float = 0.3,
) -> list:
    """Re-transcribe low-confidence segments with higher-quality settings.

    Identifies segments with avg_logprob below threshold, groups adjacent ones
    into regions, re-transcribes each region with beam_size=10/best_of=3, and
    keeps the result only if confidence improves.
    """
    if not segments:
        return segments

    candidates = [i for i, s in enumerate(segments) if s.get("avg_logprob", 0) < threshold]
    if not candidates:
        return segments

    total_duration = sum(s["end"] - s["start"] for s in segments)
    candidate_duration = sum(segments[i]["end"] - segments[i]["start"] for i in candidates)

    if candidate_duration < min_region_seconds:
        logger.info(f"Confidence retry: {len(candidates)} low-confidence segments but only {candidate_duration:.1f}s total — skipping")
        return segments

    if candidate_duration / total_duration > max_ratio:
        max_segs = int(len(segments) * max_ratio)
        candidates = sorted(candidates, key=lambda i: segments[i]["avg_logprob"])[:max_segs]
        candidates.sort()
        # int(len(segments) * max_ratio) can be 0 for small inputs (e.g.
        # 3 segments × 0.3 ratio = 0), leaving candidates empty. Guard
        # against indexing [0] below.
        if not candidates:
            logger.info("Confidence retry: cap reduced candidates to 0 — skipping")
            return segments
        candidate_duration = sum(segments[i]["end"] - segments[i]["start"] for i in candidates)
        logger.info(f"Confidence retry: capped to {len(candidates)} segments ({candidate_duration:.1f}s, {max_ratio:.0%} limit)")

    regions = []
    region_start = candidates[0]
    region_end = candidates[0]
    for idx in candidates[1:]:
        if idx == region_end + 1 or (segments[idx]["start"] - segments[region_end]["end"]) < 2.0:
            region_end = idx
        else:
            regions.append((region_start, region_end))
            region_start = idx
            region_end = idx
    regions.append((region_start, region_end))

    improved_count = 0
    updated_segments = list(segments)

    for region_start_idx, region_end_idx in regions:
        region_audio_start = segments[region_start_idx]["start"]
        region_audio_end = segments[region_end_idx]["end"]
        region_duration = region_audio_end - region_audio_start

        if region_duration < 0.5:
            continue

        start_sample = int(region_audio_start * sr)
        end_sample = min(int(region_audio_end * sr), len(audio_data))
        region_audio = audio_data[start_sample:end_sample]

        context_text = ""
        if region_start_idx > 0:
            prev_segs = segments[max(0, region_start_idx - 3):region_start_idx]
            context_text = " ".join(s["text"] for s in prev_segs)[-200:]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, region_audio, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            options = get_options(language)
            options["beam_size"] = retry_beam_size
            options["best_of"] = retry_best_of
            options["no_speech_threshold"] = None
            if context_text:
                options["initial_prompt"] = context_text

            _, _, new_segs = transcribe_chunk(model, tmp_path, options)
            new_segs = _filter_hallucinations(new_segs)

            if not new_segs:
                continue

            new_avg_logprob = sum(s["avg_logprob"] for s in new_segs) / len(new_segs)
            old_segs = segments[region_start_idx:region_end_idx + 1]
            old_avg_logprob = sum(s["avg_logprob"] for s in old_segs) / len(old_segs)

            if new_avg_logprob > old_avg_logprob:
                formatted = []
                for s in new_segs:
                    formatted.append({
                        "id": 0,
                        "start": round(s["start"] + region_audio_start, 2),
                        "end": round(s["end"] + region_audio_start, 2),
                        "text": s["text"],
                        "avg_logprob": round(s["avg_logprob"], 4),
                        "no_speech_prob": round(s["no_speech_prob"], 4),
                        "compression_ratio": round(s["compression_ratio"], 2),
                        "detected_language": old_segs[0].get("detected_language", "en"),
                    })

                updated_segments[region_start_idx:region_end_idx + 1] = formatted
                offset = len(formatted) - (region_end_idx - region_start_idx + 1)
                for i in range(len(regions)):
                    if regions[i][0] > region_end_idx:
                        regions[i] = (regions[i][0] + offset, regions[i][1] + offset)

                improved_count += 1
                logger.info(
                    f"  Confidence retry: region {region_audio_start:.1f}-{region_audio_end:.1f}s "
                    f"improved ({old_avg_logprob:.3f} → {new_avg_logprob:.3f})"
                )
        except Exception as e:
            logger.warning(f"  Confidence retry failed for region {region_audio_start:.1f}-{region_audio_end:.1f}s: {e}")
        finally:
            os.unlink(tmp_path)

    if improved_count > 0:
        for idx, seg in enumerate(updated_segments):
            seg["id"] = idx
        logger.info(f"Confidence retry: improved {improved_count}/{len(regions)} regions")
    else:
        logger.info(f"Confidence retry: checked {len(regions)} regions, none improved")

    return updated_segments


def _transcribe_chunked(model, audio_data: np.ndarray, sr: int, language: Optional[str],
                         get_options, transcribe_chunk, phone_mode: bool = False) -> dict:
    """Transcribe long audio using VAD-based smart chunking. Engine-agnostic."""
    chunks = _get_speech_chunks(audio_data, sr, phone_mode=phone_mode)

    all_segments = []
    transcripts = []
    detected_languages = []
    prev_context = None

    for i, (start_sample, end_sample) in enumerate(chunks):
        chunk_audio = audio_data[start_sample:end_sample]
        chunk_duration = len(chunk_audio) / sr
        chunk_offset_seconds = start_sample / sr

        logger.info(f"Chunk {i+1}/{len(chunks)}: {chunk_offset_seconds:.1f}s - {end_sample/sr:.1f}s ({chunk_duration:.1f}s)")

        # Write chunk to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, chunk_audio, sr, subtype="PCM_16")
            tmp_path = tmp.name

        try:
            options = get_options(language)
            if prev_context:
                options["initial_prompt"] = prev_context

            chunk_text, chunk_lang, chunk_segments = transcribe_chunk(model, tmp_path, options)

            # Filter hallucinated segments
            pre_filter_count = len(chunk_segments)
            chunk_segments = _filter_hallucinations(chunk_segments)
            if pre_filter_count != len(chunk_segments):
                logger.info(f"  Chunk {i+1}: filtered {pre_filter_count - len(chunk_segments)} hallucinated segments")
                # Rebuild text from non-hallucinated segments only
                chunk_text = " ".join(s["text"] for s in chunk_segments)
                chunk_text = re.sub(r"\s+", " ", chunk_text).strip()

            # Check for untranscribed tail at end of chunk
            if chunk_segments:
                tail_segs = _retranscribe_tail(
                    model, chunk_audio, sr,
                    chunk_segments[-1]["end"], chunk_duration,
                    get_options, transcribe_chunk, language, prev_context,
                )
                if tail_segs:
                    chunk_segments.extend(tail_segs)
                    chunk_text = " ".join(s["text"] for s in chunk_segments)
                    chunk_text = re.sub(r"\s+", " ", chunk_text).strip()

            if chunk_text:
                transcripts.append(chunk_text)
                detected_languages.append(chunk_lang)
                prev_context = chunk_text[-200:]
                logger.info(f"  Chunk {i+1}: {len(chunk_text)} chars, lang={chunk_lang}")
            else:
                logger.info(f"  Chunk {i+1}: empty (no speech)")

            # Remap segment timestamps to original audio position
            for seg in chunk_segments:
                remapped = {
                    "id": 0,
                    "start": round(seg["start"] + chunk_offset_seconds, 2),
                    "end": round(seg["end"] + chunk_offset_seconds, 2),
                    "text": seg["text"],
                    "avg_logprob": round(seg["avg_logprob"], 4),
                    "no_speech_prob": round(seg["no_speech_prob"], 4),
                    "compression_ratio": round(seg["compression_ratio"], 2),
                    "detected_language": chunk_lang,
                }
                if remapped["text"].strip():
                    all_segments.append(remapped)

        finally:
            os.unlink(tmp_path)

    # Gap rescue pass — re-transcribe gaps where Whisper skipped speech
    rescued_segments = _rescue_gaps(
        model, audio_data, sr,
        all_segments, chunks,
        get_options, transcribe_chunk, language,
    )
    if rescued_segments:
        all_segments.extend(rescued_segments)
        all_segments.sort(key=lambda s: s["start"])
        # Rebuild full text from all segments (including rescued)
        transcripts = [seg["text"] for seg in all_segments]

    # Confidence-based re-transcription — retry low-confidence segments with higher beam settings
    from app.audio_preprocess.config import preprocess_config as _cfg
    all_segments = _retranscribe_low_confidence(
        model, audio_data, sr,
        all_segments, get_options, transcribe_chunk, language,
        threshold=_cfg.CONFIDENCE_RETRANSCRIBE_THRESHOLD,
        retry_beam_size=_cfg.CONFIDENCE_RETRANSCRIBE_BEAM_SIZE,
        retry_best_of=_cfg.CONFIDENCE_RETRANSCRIBE_BEST_OF,
        min_region_seconds=_cfg.CONFIDENCE_RETRANSCRIBE_MIN_REGION_SECONDS,
        max_ratio=_cfg.CONFIDENCE_RETRANSCRIBE_MAX_RATIO,
    )
    transcripts = [seg["text"] for seg in all_segments]

    full_text = " ".join(transcripts)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    # Determine overall language
    if not detected_languages:
        overall_lang = "unknown"
    elif len(set(detected_languages)) == 1:
        overall_lang = detected_languages[0]
    else:
        lang_counts = Counter(detected_languages)
        overall_lang = f"mixed ({', '.join(f'{lang}:{count}' for lang, count in lang_counts.most_common())})"

    # Re-number segment IDs sequentially
    for idx, seg in enumerate(all_segments):
        seg["id"] = idx

    logger.info(
        f"Chunked transcription complete: {len(chunks)} chunks, "
        f"{len(full_text)} chars, {len(all_segments)} segments, lang={overall_lang}"
    )

    return {
        "text": full_text,
        "language": overall_lang,
        "char_count": len(full_text),
        "segments": all_segments,
    }
