"""Tests for the web recorder Phase 1 backend additions (core_api).

Scope:
  - tab_audio._build_v2_speaker_events_doc — pure-function shape test
  - webhooks._email_platform_label — source-aware platform label helper

TranscriptionBase's load_speaker_events_file is exercised separately in
TranscriptionBase/tests/test_speaker_events_loader.py because the two
services share a top-level `app` package name and can't safely sit on
sys.path together.

All tests are pure-logic — no DB, no R2, no FastAPI client. Integration
tests for /complete + /web-recorder-cancel + sweeper require a live
Postgres and are exercised manually via dev.sh.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Put core_api/ on sys.path so `from app.api ...` resolves correctly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_build_v2_speaker_events_doc_shape():
    """Writer envelope contains all required v2 fields with correct types."""
    from app.api.tab_audio import _build_v2_speaker_events_doc

    events = [
        {"speaker": "Alice", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "start": 5.0, "end": 9.0},
    ]
    participants = [{"display_name": "Alice"}, {"display_name": "Bob"}, {"display_name": "Charlie"}]

    doc = _build_v2_speaker_events_doc(events, participants, "bot_capture")

    assert doc["schema_version"] == 2
    assert doc["source"] == "bot_capture"
    assert doc["events"] == events
    assert doc["participants"] == participants
    # captured_at should be ISO-8601 parseable
    parsed = datetime.fromisoformat(doc["captured_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_build_v2_speaker_events_doc_empty():
    """Empty events + empty participants should still produce a valid v2 doc."""
    from app.api.tab_audio import _build_v2_speaker_events_doc

    doc = _build_v2_speaker_events_doc([], [], "web_capture")
    assert doc["schema_version"] == 2
    assert doc["source"] == "web_capture"
    assert doc["events"] == []
    assert doc["participants"] == []


def test_email_platform_label_web_recorder():
    """webhooks._email_platform_label maps web_recorder source → 'Web Recorder'
    even with NULL meeting_url (post-migration 040)."""
    from app.api.webhooks import _email_platform_label

    class FakeMeeting:
        source = "web_recorder"
        meeting_url = None

    assert _email_platform_label(FakeMeeting()) == "Web Recorder"


def test_email_platform_label_upload_no_url():
    """Upload source with NULL meeting_url returns 'Upload', not 'session' or 'local'."""
    from app.api.webhooks import _email_platform_label

    class FakeMeeting:
        source = "upload"
        meeting_url = None

    assert _email_platform_label(FakeMeeting()) == "Upload"


def test_email_platform_label_legacy_bot_with_url():
    """Bot/calendar meetings with real URLs fall through to host parsing
    (preserves existing email behavior)."""
    from app.api.webhooks import _email_platform_label

    class FakeMeeting:
        source = "manual"
        meeting_url = "https://meet.google.com/abc-defg-hij"

    assert _email_platform_label(FakeMeeting()) == "meet.google.com"


def test_email_platform_label_null_everything():
    """No source + no URL → None (graceful, doesn't crash email templates)."""
    from app.api.webhooks import _email_platform_label

    class FakeMeeting:
        source = None
        meeting_url = None

    assert _email_platform_label(FakeMeeting()) is None


def test_email_platform_label_zoom_cloud_no_host_parse():
    """zoom_cloud / teams_cloud return friendly labels, not URL-parsed gibberish."""
    from app.api.webhooks import _email_platform_label

    class ZoomCloud:
        source = "zoom_cloud"
        meeting_url = "some-zoom-recording-uuid"  # not a real URL

    class TeamsCloud:
        source = "teams_cloud"
        meeting_url = None

    assert _email_platform_label(ZoomCloud()) == "Zoom Cloud"
    assert _email_platform_label(TeamsCloud()) == "Teams Cloud"


def test_email_platform_label_link_upload():
    """link_upload returns 'Link Upload', not the URL host."""
    from app.api.webhooks import _email_platform_label

    class FakeMeeting:
        source = "link_upload"
        meeting_url = "https://example.com/recording.mp4"

    assert _email_platform_label(FakeMeeting()) == "Link Upload"
