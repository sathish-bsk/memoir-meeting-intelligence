"""
Word-level forced alignment service using ctc-forced-aligner.

Uses an ONNX-based MMS model to align ASR transcript text against audio,
producing word-level timestamps with ~50ms accuracy.

Architecture:
    - Singleton model loading with thread lock (same pattern as VAD, Whisper, Pyannote)
    - Runs AFTER transcription as an optional post-processing step
    - Per-segment alignment: each segment aligned with its own detected language
    - Supports 1,130 languages via MMS (including Tamil, Hindi, English)
    - Uses ONNX inference (CPU) — no torch dependency for alignment
"""
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("audio_preprocess")

# ── Singleton model state ──
_onnx_session = None
_tokenizer = None
_alignment_lock = threading.Lock()

# ── ISO 639-1 (Whisper) → ISO 639-3 (MMS / ctc-forced-aligner) ──
LANG_MAP_1_TO_3 = {
    "en": "eng", "ta": "tam", "hi": "hin", "te": "tel", "kn": "kan",
    "ml": "mal", "mr": "mar", "bn": "ben", "gu": "guj", "pa": "pan",
    "ur": "urd", "fr": "fra", "de": "deu", "es": "spa", "ja": "jpn",
    "zh": "cmn", "ar": "ara", "ko": "kor", "pt": "por", "it": "ita",
    "ru": "rus", "nl": "nld", "pl": "pol", "tr": "tur", "vi": "vie",
    "th": "tha", "id": "ind", "ms": "msa",
}

# Languages that use Latin script (no romanization needed)
_LATIN_SCRIPT_LANGS = {
    "eng", "fra", "deu", "spa", "por", "ita", "nld", "pol", "tur",
    "vie", "ind", "msa",
}

# Unicode script name → ISO 639-3 (auto-detected via unicodedata.name)
_SCRIPT_TO_LANG = {
    "TAMIL": "tam",
    "DEVANAGARI": "hin",
    "BENGALI": "ben",
    "GUJARATI": "guj",
    "GURMUKHI": "pan",
    "TELUGU": "tel",
    "KANNADA": "kan",
    "MALAYALAM": "mal",
    "ARABIC": "ara",
    "THAI": "tha",
    "CJK": "cmn",
    "HANGUL": "kor",
    "HIRAGANA": "jpn",
    "KATAKANA": "jpn",
    "CYRILLIC": "rus",
}


def _detect_script_lang(text: str) -> str:
    """Detect language from Unicode script when Whisper misidentifies.

    Uses unicodedata.name() to extract the script name from the first
    non-Latin character. Works for any Unicode script automatically.

    Returns ISO 639-3 code if non-Latin script found, empty string otherwise.
    """
    for ch in text:
        if not ch.isalpha() or ord(ch) <= 127:
            continue
        try:
            char_name = unicodedata.name(ch, "")
        except ValueError:
            continue
        # Script name is the first word(s) before "LETTER"/"SIGN"/"VOWEL" etc.
        # e.g. "TAMIL LETTER KA" → "TAMIL", "CJK UNIFIED IDEOGRAPH" → "CJK"
        script = char_name.split()[0] if char_name else ""
        lang = _SCRIPT_TO_LANG.get(script, "")
        if lang:
            return lang
        # Non-Latin but unmapped script — still needs romanization
        return "eng"
    return ""


def _load_alignment_model(device: str = "cpu"):
    """
    Lazy singleton loader for the ctc-forced-aligner ONNX model.
    Returns (onnx_session, tokenizer) or (None, None) if not installed.

    Uses CUDAExecutionProvider when device="cuda" and onnxruntime-gpu is installed.
    """
    global _onnx_session, _tokenizer

    if _onnx_session is not None:
        return _onnx_session, _tokenizer

    with _alignment_lock:
        if _onnx_session is not None:
            return _onnx_session, _tokenizer

        try:
            import onnxruntime
            from ctc_forced_aligner import (
                ensure_onnx_model,
                MODEL_URL,
                Tokenizer,
            )

            # Use default model path (same as AlignmentSingleton)
            model_path = os.path.join(
                os.path.expanduser("~"), "ctc_forced_aligner", "model.onnx"
            )

            logger.info(f"Loading ctc-forced-aligner ONNX model from {model_path} (device={device})")

            # Download model if not present
            ensure_onnx_model(model_path, MODEL_URL)

            # Create ONNX inference session with GPU support if available
            if device == "cuda" and "CUDAExecutionProvider" in onnxruntime.get_available_providers():
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                logger.info("ctc-forced-aligner: using CUDA execution provider")
            else:
                providers = ["CPUExecutionProvider"]
                if device == "cuda":
                    logger.warning("ctc-forced-aligner: CUDA requested but CUDAExecutionProvider not available, falling back to CPU")

            _onnx_session = onnxruntime.InferenceSession(model_path, providers=providers)
            _tokenizer = Tokenizer()

            logger.info(f"ctc-forced-aligner ONNX model loaded ({_onnx_session.get_providers()})")
            return _onnx_session, _tokenizer

        except ImportError:
            logger.warning(
                "ctc-forced-aligner not installed. "
                "Word-level alignment will be skipped. "
                "Install with: pip install ctc-forced-aligner"
            )
            return None, None
        except Exception as e:
            logger.error(f"Failed to load alignment model: {e}", exc_info=True)
            return None, None


def _clean_text_for_alignment(text: str, lang_639_3: str, needs_romanize: bool) -> tuple:
    """
    Clean text to prevent assertion crashes in ctc-forced-aligner.

    Removes words that text_normalize strips to empty (numbers, symbols)
    and strips non-vocabulary characters (hyphens, etc.) for Latin scripts.

    Returns:
        (clean_text, word_mapping) where word_mapping is a list of
        (original_word, clean_word_index_or_None) for every original word.
        clean_word_index is the index into the clean word list, or None
        if the word was dropped (e.g. pure numbers).
    """
    from ctc_forced_aligner import text_normalize

    original_words = text.split()
    clean_words = []
    word_mapping = []

    for orig_word in original_words:
        normalized = text_normalize(orig_word.strip(), lang_639_3).strip()
        if not normalized:
            word_mapping.append((orig_word, None))
            continue
        if not needs_romanize:
            vocab_safe = re.sub(r"[^a-z']", "", normalized)
            if vocab_safe:
                word_mapping.append((orig_word, len(clean_words)))
                clean_words.append(vocab_safe)
            else:
                word_mapping.append((orig_word, None))
        else:
            word_mapping.append((orig_word, len(clean_words)))
            clean_words.append(orig_word)

    return ' '.join(clean_words), word_mapping


def _filter_empty_tokens(tokens_starred: list, text_starred: list):
    """
    Safety net: remove <star>+token pairs where the token is empty
    or contains only non-vocab chars after preprocessing.
    """
    filtered_tokens = []
    filtered_text = []
    for i in range(0, len(tokens_starred), 2):
        if i + 1 < len(tokens_starred):
            token = tokens_starred[i + 1]
            if token.strip() and re.sub(r"[^a-z' ]", "", token).strip():
                filtered_tokens.extend([tokens_starred[i], token])
                filtered_text.extend([text_starred[i], text_starred[i + 1]])
    return filtered_tokens, filtered_text


def _normalize_score(raw_score: float, start: float, end: float) -> float:
    """
    Normalize cumulative log-probability score to 0-1 range.

    Scores from ctc-forced-aligner are summed log-probs across frames.
    Convert to average per-frame probability with exp().
    """
    import numpy as np

    duration_s = max(end - start, 0.02)
    num_frames = max(duration_s / 0.02, 1)  # ~20ms per CTC frame
    avg_log_prob = raw_score / num_frames
    normalized = float(np.exp(avg_log_prob))
    return round(min(1.0, max(0.0, normalized)), 3)


def align_segments(
    audio_path: Path,
    segments: List[Dict],
    device: str = "cpu",
    batch_size: int = 16,
) -> List[Dict]:
    """
    Align each segment's text against its audio portion with the correct language.
    Handles mixed-language audio (e.g., English + Tamil) by aligning per-segment.

    Each segment is aligned independently:
    1. Extract just that segment's audio portion (by start/end timestamps)
    2. Use the segment's detected_language for correct romanization
    3. Offset word timestamps back to full-audio time

    Args:
        audio_path: Path to the processed audio file.
        segments: ASR segments with start, end, text, and optionally detected_language.
        device: 'cpu' or 'cuda'.
        batch_size: Batch size for emission generation.

    Returns:
        The same segments list, with each segment gaining a "words" key.
    """
    session, tokenizer = _load_alignment_model(device)
    if session is None:
        for seg in segments:
            seg["words"] = []
        return segments

    try:
        from ctc_forced_aligner import (
            load_audio,
            generate_emissions,
            preprocess_text,
            get_alignments,
            get_spans,
            postprocess_results,
        )

        # Load full audio once (16kHz mono numpy array)
        audio_waveform = load_audio(str(audio_path))
        sr = 16000  # ctc-forced-aligner uses 16kHz

        total_words = 0

        for seg_idx, seg in enumerate(segments):
            seg["words"] = []
            text = seg.get("text", "").strip()
            if not text:
                continue

            seg_dur = seg["end"] - seg["start"]
            lang = seg.get("detected_language", "en")
            logger.info(
                f"Aligning segment {seg_idx + 1}/{len(segments)} "
                f"({seg_dur:.1f}s, lang={lang}): "
                f"\"{text[:50]}{'...' if len(text) > 50 else ''}\""
            )

            # Detect language for this segment
            if not lang or lang == "unknown":
                lang = "en"
            lang_639_3 = LANG_MAP_1_TO_3.get(lang, "eng")
            needs_romanize = lang_639_3 not in _LATIN_SCRIPT_LANGS

            # Override: if text has non-Latin chars but Whisper said Latin language,
            # detect actual script and force romanization
            if not needs_romanize:
                script_lang = _detect_script_lang(text)
                if script_lang:
                    lang_639_3 = script_lang
                    needs_romanize = True
                    logger.info(
                        f"  Script override: Whisper said '{lang}' but text has "
                        f"non-Latin chars → using '{lang_639_3}' with romanization"
                    )

            # Pre-clean text and get mapping from original → clean words
            clean_text, word_mapping = _clean_text_for_alignment(text, lang_639_3, needs_romanize)
            if not clean_text.strip():
                continue

            # Extract audio portion for this segment
            start_sample = max(0, int(seg["start"] * sr))
            end_sample = min(len(audio_waveform), int(seg["end"] * sr))
            if end_sample <= start_sample:
                continue
            seg_audio = audio_waveform[start_sample:end_sample]

            # Need minimum audio length for ONNX inference
            if len(seg_audio) < int(sr * 0.1):  # at least 100ms
                continue

            try:
                with _alignment_lock:
                    tokens_starred, text_starred = preprocess_text(
                        clean_text,
                        romanize=needs_romanize,
                        language=lang_639_3,
                    )

                    # Safety net: filter empty/bad tokens
                    tokens_starred, text_starred = _filter_empty_tokens(
                        tokens_starred, text_starred
                    )
                    if not tokens_starred:
                        continue

                    emissions, stride = generate_emissions(
                        session, seg_audio, batch_size=batch_size
                    )

                    aligned_segs, scores, blank_token = get_alignments(
                        emissions, tokens_starred, tokenizer
                    )

                    spans = get_spans(tokens_starred, aligned_segs, blank_token)

                    word_timestamps = postprocess_results(
                        text_starred, spans, stride, scores
                    )

                # Build aligned word list (clean words with timestamps)
                aligned_words = []
                for w in word_timestamps:
                    aligned_words.append({
                        "word": w.get("text", w.get("word", "")),
                        "start": w.get("start", 0.0),
                        "end": w.get("end", 0.0),
                        "score": w.get("score", 0.0),
                    })

                # Map timestamps back to original words (restoring numbers)
                for orig_word, clean_idx in word_mapping:
                    if clean_idx is not None and clean_idx < len(aligned_words):
                        aw = aligned_words[clean_idx]
                        seg["words"].append({
                            "word": orig_word.strip(".,!?;:\"'").strip() or orig_word,
                            "start": round(seg["start"] + aw["start"], 3),
                            "end": round(seg["start"] + aw["end"], 3),
                            "score": _normalize_score(aw["score"], aw["start"], aw["end"]),
                        })
                    else:
                        # Dropped word (pure number/symbol) — interpolate timestamp
                        # from previous and next aligned words
                        prev_end = seg["words"][-1]["end"] if seg["words"] else seg["start"]
                        # Find next aligned word's start
                        next_start = None
                        for _, next_idx in word_mapping:
                            if next_idx is not None and next_idx < len(aligned_words):
                                nw = aligned_words[next_idx]
                                ns = round(seg["start"] + nw["start"], 3)
                                if ns > prev_end:
                                    next_start = ns
                                    break
                        if next_start is None:
                            next_start = seg["end"]
                        seg["words"].append({
                            "word": orig_word.strip(".,!?;:\"'").strip() or orig_word,
                            "start": prev_end,
                            "end": next_start,
                            "score": 0.0,
                        })

                # Merge ordinal fragments: a dropped number ("22") followed by
                # a suffix ("nd", "st", "th", "rd") should be one word ("22nd").
                _ordinal_suffixes = {"st", "nd", "rd", "th"}
                _merged_words = []
                for _wi, _w in enumerate(seg["words"]):
                    if (_merged_words
                            and _w.get("word", "").lower() in _ordinal_suffixes
                            and _merged_words[-1].get("score", 1.0) == 0.0
                            and _merged_words[-1].get("word", "").isdigit()):
                        _merged_words[-1]["word"] = _merged_words[-1]["word"] + _w["word"]
                        _merged_words[-1]["end"] = _w["end"]
                    else:
                        _merged_words.append(_w)
                seg["words"] = _merged_words

                total_words += len(seg["words"])

            except Exception as e:
                seg_preview = text[:40] + "..." if len(text) > 40 else text
                logger.warning(
                    f"Alignment failed for segment {seg_idx + 1} "
                    f"({lang}, '{seg_preview}'): {e}"
                )
                continue

        logger.info(
            f"Per-segment alignment complete: {total_words} words "
            f"across {len(segments)} segments"
        )
        return segments

    except Exception as e:
        logger.error(f"Alignment failed: {e}", exc_info=True)
        for seg in segments:
            seg["words"] = []
        return segments
