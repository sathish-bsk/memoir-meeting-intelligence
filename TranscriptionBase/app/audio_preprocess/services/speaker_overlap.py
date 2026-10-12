"""
Speaker overlap mapping — maps diarization labels (SPEAKER_00, etc.) to real
display names using time-overlap between diarization segments and active
speaker events captured by the BotWorker.

Pure math module — no ML deps, no DB access.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_speaker_events_file(path: Path | str) -> tuple[list[dict], list[dict]]:
    """Load speaker_events.json, returning (events, participants).

    Handles both schema versions written by writers across BotWorker,
    core_api tab_audio finalizers, and the in-browser web recorder:

      v1 (legacy, all writers pre-migration-040):
          flat JSON array of {speaker, start, end} dicts. Participants
          information is NOT carried in the file (lived elsewhere — in
          MeetingParticipant DB rows and transcription_jobs.params).

      v2 (current, post-migration-040):
          structured object with schema_version=2:
            {
              "schema_version": 2,
              "captured_at": "...",
              "source": "bot_capture" | "web_capture",
              "events":       [{speaker, start, end}, ...],
              "participants": [{display_name}, ...],
            }

    Degrades gracefully:
      - missing file → ([], [])
      - malformed JSON → ([], [])
      - any other read error → ([], [])

    Mixed v1/v2 state on R2 is permanently safe — this helper is the
    single decode site for the file across the TranscriptionBase pipeline.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return [], []

    if isinstance(data, list):
        # v1 flat array — filter to dict entries only so downstream
        # consumers can safely call evt.get("speaker") without an
        # AttributeError on stray strings/numbers
        return [e for e in data if isinstance(e, dict)], []

    if isinstance(data, dict):
        # v2 structured object
        events = data.get("events") or []
        participants = data.get("participants") or []
        # Defensive: ensure list types (v2 spec calls for lists; tolerate
        # writer bugs by coercing non-lists to empty rather than raising)
        if not isinstance(events, list):
            events = []
        if not isinstance(participants, list):
            participants = []
        return events, participants

    # Unknown shape — return empty tuples rather than raise
    return [], []


def _build_overlap_matrix(
    segments: list[dict],
    speaker_events: list[dict],
) -> dict[str, dict[str, float]]:
    """Build a time-overlap matrix: diarization labels x speaker names.

    For each diarization segment and speaker event, computes the overlap
    duration in seconds. Returns {label: {name: total_overlap_seconds}}.
    """
    matrix: dict[str, dict[str, float]] = {}

    for seg in segments:
        label = seg.get("speaker")
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        if not label or seg_end <= seg_start:
            continue

        if label not in matrix:
            matrix[label] = {}

        for event in speaker_events:
            name = event.get("speaker")
            ev_start = event.get("start", 0)
            ev_end = event.get("end", 0)
            if not name or ev_end <= ev_start:
                continue

            # Compute overlap
            overlap_start = max(seg_start, ev_start)
            overlap_end = min(seg_end, ev_end)
            overlap = overlap_end - overlap_start

            if overlap > 0:
                matrix[label][name] = matrix[label].get(name, 0) + overlap

    return matrix


def _greedy_assign(
    matrix: dict[str, dict[str, float]],
    threshold: float = 0.3,
) -> dict[str, str]:
    """Greedily assign each diarization label to the speaker name with
    the most overlap, if the overlap ratio exceeds the threshold.

    The ratio is: overlap_with_best_name / total_overlap_for_label.
    """
    assignments: dict[str, str] = {}
    used_names: set[str] = set()

    # Sort labels by total overlap (descending) for more confident assignments first
    label_totals = {
        label: sum(names.values())
        for label, names in matrix.items()
    }
    sorted_labels = sorted(label_totals, key=label_totals.get, reverse=True)

    for label in sorted_labels:
        names = matrix[label]
        if not names:
            continue

        # Sort candidates by overlap, skip already-assigned names
        sorted_names = sorted(names.items(), key=lambda x: x[1], reverse=True)

        for name, overlap in sorted_names:
            if name in used_names:
                continue

            total = label_totals[label]
            ratio = overlap / total if total > 0 else 0

            if ratio >= threshold:
                assignments[label] = name
                used_names.add(name)
                break

    return assignments


def compute_speaker_name_map(
    segments: list[dict],
    speaker_events: list[dict],
) -> dict[str, str]:
    """Public entry point: compute diarization label -> display name mapping.

    Args:
        segments: Diarization segments with "speaker", "start", "end" keys.
        speaker_events: Active speaker events with "speaker", "start", "end" keys.

    Returns:
        Mapping of diarization labels to display names, e.g.:
        {"SPEAKER_00": "John Smith", "SPEAKER_01": "Jane Doe"}
    """
    if not segments or not speaker_events:
        return {}

    matrix = _build_overlap_matrix(segments, speaker_events)
    if not matrix:
        return {}

    assignments = _greedy_assign(matrix, threshold=0.3)

    logger.info(
        f"Speaker overlap mapping: {len(assignments)} labels assigned "
        f"from {len(matrix)} labels and {len(speaker_events)} events"
    )

    return assignments
