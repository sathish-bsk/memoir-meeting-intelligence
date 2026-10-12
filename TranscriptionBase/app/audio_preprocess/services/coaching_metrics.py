"""
Phase 7A: Coaching metrics computed directly from diarization + transcript data.

Self-contained module — zero imports from pipeline/transcribe/audio.
Designed for future extraction into post-processing microservice (Phase 9).

Takes pure dicts (segments with speaker labels + timestamps), returns metrics dict.
No LLM, no model — just math over existing data.
"""

import re
from typing import Optional

# Filler words to detect (word-boundary matched)
FILLER_WORDS = [
    "um", "uh", "uh-huh", "uh huh", "umm", "uhh",
    "like", "you know", "basically", "actually", "honestly",
    "literally", "right", "sort of", "kind of", "i mean",
    "so yeah", "you see", "well",
]

# Compile patterns once — match whole words only
_FILLER_PATTERNS = {
    word: re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
    for word in FILLER_WORDS
}

# Question detection pattern
_QUESTION_PATTERN = re.compile(r"[^.!?]*\?")


def _get_speaker_role(
    speaker_label: str,
    rep_speaker: Optional[str] = None,
) -> str:
    """
    Determine if a speaker is 'rep' or 'customer'.
    Default heuristic: SPEAKER_00 = rep, all others = customer.
    Override with rep_speaker param.
    """
    if not speaker_label:
        return "rep"  # No speaker labels → treat as single speaker
    if rep_speaker:
        return "rep" if speaker_label == rep_speaker else "customer"
    return "rep" if speaker_label == "SPEAKER_00" else "customer"


def compute_talk_ratio(
    segments: list[dict],
    rep_speaker: Optional[str] = None,
) -> dict:
    """
    Compute talk ratio from segment durations per speaker.

    Returns:
        {
            "rep_percentage": float,
            "customer_percentage": float,
            "assessment": "optimal" | "rep_dominant" | "customer_dominant"
        }
    """
    rep_time = 0.0
    customer_time = 0.0

    has_speakers = any(seg.get("speaker") for seg in segments)

    for seg in segments:
        speaker = seg.get("speaker")
        duration = seg.get("end", 0) - seg.get("start", 0)
        if duration <= 0:
            continue

        role = _get_speaker_role(speaker, rep_speaker)
        if role == "rep":
            rep_time += duration
        else:
            customer_time += duration

    total = rep_time + customer_time
    if total == 0:
        return {
            "rep_percentage": 0.0,
            "customer_percentage": 0.0,
            "assessment": "no_speakers" if not has_speakers else "optimal",
        }

    rep_pct = round((rep_time / total) * 100, 1)
    cust_pct = round((customer_time / total) * 100, 1)

    if not has_speakers:
        assessment = "no_speakers"
    elif rep_pct > 55:
        assessment = "rep_dominant"
    elif rep_pct < 35:
        assessment = "customer_dominant"
    else:
        assessment = "optimal"

    return {
        "rep_percentage": rep_pct,
        "customer_percentage": cust_pct,
        "assessment": assessment,
    }


def compute_longest_monologue(
    segments: list[dict],
    rep_speaker: Optional[str] = None,
) -> dict:
    """
    Find the longest uninterrupted speech by a single speaker.
    A monologue = consecutive segments from the same speaker.

    Returns:
        {
            "longest_monologue_seconds": float,
            "longest_monologue_speaker": str | None
        }
    """
    if not segments:
        return {"longest_monologue_seconds": 0.0, "longest_monologue_speaker": None}

    longest_duration = 0.0
    longest_speaker = None

    current_speaker = None
    current_start = 0.0
    current_end = 0.0

    for seg in segments:
        speaker = seg.get("speaker")
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        if speaker == current_speaker:
            # Extend the current monologue
            current_end = end
        else:
            # Check if previous monologue is the longest
            if current_speaker is not None:
                duration = current_end - current_start
                if duration > longest_duration:
                    longest_duration = duration
                    longest_speaker = current_speaker

            # Start new monologue
            current_speaker = speaker
            current_start = start
            current_end = end

    # Check the last monologue
    if current_speaker is not None:
        duration = current_end - current_start
        if duration > longest_duration:
            longest_duration = duration
            longest_speaker = current_speaker

    return {
        "longest_monologue_seconds": round(longest_duration, 2),
        "longest_monologue_speaker": longest_speaker,
    }


def count_questions(
    segments: list[dict],
    rep_speaker: Optional[str] = None,
) -> dict:
    """
    Count questions asked by each speaker role.
    Detection: sentences ending in '?'

    Returns:
        {"rep": int, "customer": int}
    """
    rep_count = 0
    customer_count = 0

    for seg in segments:
        speaker = seg.get("speaker")
        text = seg.get("text", "")
        if not text:
            continue

        questions = _QUESTION_PATTERN.findall(text)
        count = len(questions)

        role = _get_speaker_role(speaker, rep_speaker)
        if role == "rep":
            rep_count += count
        else:
            customer_count += count

    return {"rep": rep_count, "customer": customer_count}


def count_filler_words(
    segments: list[dict],
    rep_speaker: Optional[str] = None,
) -> dict:
    """
    Count filler words per speaker with per-word breakdown.

    Returns:
        {
            "rep": int (total),
            "customer": int (total),
            "rep_details": {"um": 5, "like": 3, ...}
        }
    """
    rep_total = 0
    customer_total = 0
    rep_details: dict[str, int] = {}

    for seg in segments:
        speaker = seg.get("speaker")
        text = seg.get("text", "")
        if not text:
            continue

        role = _get_speaker_role(speaker, rep_speaker)

        for word, pattern in _FILLER_PATTERNS.items():
            matches = pattern.findall(text)
            count = len(matches)
            if count == 0:
                continue

            if role == "rep":
                rep_total += count
                rep_details[word] = rep_details.get(word, 0) + count
            else:
                customer_total += count

    return {
        "rep": rep_total,
        "customer": customer_total,
        "rep_details": rep_details,
    }


def count_interruptions(
    segments: list[dict],
    overlap_threshold: float = 0.5,
) -> int:
    """
    Count interruptions: when speaker B starts before speaker A ends.
    Only counts overlaps > overlap_threshold seconds as real interruptions
    (shorter overlaps are natural turn-taking).

    Returns: int (count of interruptions)
    """
    if len(segments) < 2:
        return 0

    count = 0
    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        prev_speaker = prev.get("speaker")
        curr_speaker = curr.get("speaker")

        # Only count when speakers differ
        if prev_speaker == curr_speaker or not prev_speaker or not curr_speaker:
            continue

        prev_end = prev.get("end", 0)
        curr_start = curr.get("start", 0)

        overlap = prev_end - curr_start
        if overlap > overlap_threshold:
            count += 1

    return count


def compute_response_time(
    segments: list[dict],
    max_gap: float = 10.0,
) -> float:
    """
    Compute average response time between speaker turns.
    Excludes gaps > max_gap seconds (likely hold/mute).
    Excludes overlaps (negative gaps).

    Returns: float (average seconds)
    """
    if len(segments) < 2:
        return 0.0

    gaps = []
    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        prev_speaker = prev.get("speaker")
        curr_speaker = curr.get("speaker")

        # Only measure gaps between different speakers
        if prev_speaker == curr_speaker or not prev_speaker or not curr_speaker:
            continue

        gap = curr.get("start", 0) - prev.get("end", 0)

        # Exclude overlaps and long silences
        if 0 < gap <= max_gap:
            gaps.append(gap)

    if not gaps:
        return 0.0

    return round(sum(gaps) / len(gaps), 2)


def compute_silence_percentage(
    segments: list[dict],
    total_duration: float,
) -> float:
    """
    Compute percentage of call duration not covered by any segment.

    Returns: float (0-100)
    """
    if total_duration <= 0 or not segments:
        return 0.0

    covered = 0.0
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if end > start:
            covered += end - start

    # Covered can exceed total_duration due to overlaps
    silence = max(0.0, total_duration - covered)
    return round((silence / total_duration) * 100, 1)


def compute_coaching_metrics(
    segments: list[dict],
    total_duration: float,
    rep_speaker: Optional[str] = None,
) -> dict:
    """
    Orchestrator — computes all coaching metrics from segments.

    Args:
        segments: List of segment dicts with speaker, start, end, text
        total_duration: Total call duration in seconds
        rep_speaker: Speaker label for the rep (default: SPEAKER_00)

    Returns:
        Dict matching CoachingMetrics schema
    """
    talk_ratio = compute_talk_ratio(segments, rep_speaker)
    monologue = compute_longest_monologue(segments, rep_speaker)
    questions = count_questions(segments, rep_speaker)
    fillers = count_filler_words(segments, rep_speaker)
    interruptions = count_interruptions(segments)
    response_time = compute_response_time(segments)
    silence_pct = compute_silence_percentage(segments, total_duration)

    return {
        "talk_ratio": talk_ratio,
        "longest_monologue_seconds": monologue["longest_monologue_seconds"],
        "longest_monologue_speaker": monologue["longest_monologue_speaker"],
        "question_count": questions,
        "filler_word_count": fillers,
        "interruption_count": interruptions,
        "average_response_time_seconds": response_time,
        "silence_percentage": silence_pct,
    }
