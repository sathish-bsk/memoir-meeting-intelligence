"""Tests for the v2 speaker_events.json loader shim.

The shim handles both v1 (legacy flat array) and v2 (structured object)
schemas produced by writers across BotWorker, core_api tab_audio, and
the in-browser web recorder. Critical for forward + backward compat:
ALL TranscriptionBase reads route through this helper, so its
correctness is what makes mixed v1/v2 state on R2 permanently safe.

Pure-logic — no R2, no DB, no FastAPI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Put TranscriptionBase/ on sys.path so `from app.audio_preprocess ...` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_load_speaker_events_file_v1_flat_array(tmp_path: Path):
    """v1 schema (legacy flat array) returns (events, [])."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    v1 = [
        {"speaker": "Alice", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "start": 5.0, "end": 9.0},
    ]
    path.write_text(json.dumps(v1))

    events, participants = load_speaker_events_file(path)
    assert events == v1
    assert participants == []


def test_load_speaker_events_file_v2_structured(tmp_path: Path):
    """v2 schema returns events and participants from the envelope."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    v2 = {
        "schema_version": 2,
        "captured_at": "2026-05-23T10:15:00Z",
        "source": "web_capture",
        "events": [{"speaker": "Alice", "start": 0.0, "end": 5.0}],
        "participants": [{"display_name": "Alice"}, {"display_name": "Bob"}],
    }
    path.write_text(json.dumps(v2))

    events, participants = load_speaker_events_file(path)
    assert events == v2["events"]
    assert participants == v2["participants"]


def test_load_speaker_events_file_missing(tmp_path: Path):
    """Missing file → ([], []), not an exception."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    events, participants = load_speaker_events_file(tmp_path / "does_not_exist.json")
    assert events == []
    assert participants == []


def test_load_speaker_events_file_malformed_json(tmp_path: Path):
    """Malformed JSON → ([], []), not an exception."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    path.write_text("this is not valid json{")

    events, participants = load_speaker_events_file(path)
    assert events == []
    assert participants == []


def test_load_speaker_events_file_v2_empty_arrays(tmp_path: Path):
    """v2 with empty events + empty participants is the 'no captions, no
    DOM roster' case — must return ([], []) cleanly."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    v2 = {
        "schema_version": 2,
        "captured_at": "2026-05-23T10:15:00Z",
        "source": "web_capture",
        "events": [],
        "participants": [],
    }
    path.write_text(json.dumps(v2))

    events, participants = load_speaker_events_file(path)
    assert events == []
    assert participants == []


def test_load_speaker_events_file_v2_with_only_participants(tmp_path: Path):
    """v2 file with empty events but populated participants — unlocks
    Path A web recorder + caption-disabled meetings on the bot path."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    v2 = {
        "schema_version": 2,
        "captured_at": "2026-05-23T10:15:00Z",
        "source": "bot_capture",
        "events": [],
        "participants": [{"display_name": "Alice"}, {"display_name": "Bob"}],
    }
    path.write_text(json.dumps(v2))

    events, participants = load_speaker_events_file(path)
    assert events == []
    assert participants == [{"display_name": "Alice"}, {"display_name": "Bob"}]


def test_load_speaker_events_file_non_list_non_dict(tmp_path: Path):
    """Defensive: top-level JSON that's neither list nor dict → ([], [])."""
    from app.audio_preprocess.services.speaker_overlap import load_speaker_events_file

    path = tmp_path / "speaker_events.json"
    path.write_text('"a bare string"')

    events, participants = load_speaker_events_file(path)
    assert events == []
    assert participants == []
