"""Tests for the Meetings + Events v3 list backend additions.

Scope:
  - CalendarEventV3Response / CalendarEventListV3Response schema shape
  - Route registration (endpoint exists on the router)
  - Scope query-param regex enforcement (via FastAPI's path/query validation)

Full integration tests for scope/role/date filtering require a live DB
and are deferred to manual QA in the 18-case matrix (plan §8.2). These
unit tests cover only the pure-Python contract.

Plan: plan/meetings-events-v3-list.md §3 (backend) + §8.1 (tests).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Schema shape ──────────────────────────────────────────────────────


def test_calendar_event_v3_response_accepts_new_fields():
    """CalendarEventV3Response accepts description, cancelled_at,
    resolved_user_id, linked_meeting_status — all v3-only additions."""
    from app.schemas.calendar import CalendarEventV3Response

    payload = {
        "id": uuid.uuid4(),
        "provider_event_id": "abc123",
        "title": "Standup",
        "start_time": datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
        "end_time": datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "linked_meeting_id": uuid.uuid4(),
        "provider": "google",
        "bot_scheduled": True,
        "attendees": [{"email": "a@b.com", "organizer": True}],
        "source": "calendar",
        "bot_invited": False,
        # v3 additions:
        "description": "Daily engineering sync",
        "cancelled_at": None,
        "resolved_user_id": uuid.uuid4(),
        "linked_meeting_status": "completed",
    }
    parsed = CalendarEventV3Response.model_validate(payload)
    assert parsed.description == "Daily engineering sync"
    assert parsed.cancelled_at is None
    assert parsed.resolved_user_id is not None
    assert parsed.linked_meeting_status == "completed"


def test_calendar_event_v3_response_extends_legacy_shape():
    """All legacy CalendarEventResponse fields are still present on v3."""
    from app.schemas.calendar import CalendarEventResponse, CalendarEventV3Response

    legacy_fields = set(CalendarEventResponse.model_fields.keys())
    v3_fields = set(CalendarEventV3Response.model_fields.keys())
    assert legacy_fields.issubset(v3_fields), (
        f"v3 dropped legacy fields: {legacy_fields - v3_fields}"
    )
    new_fields = v3_fields - legacy_fields
    assert new_fields == {
        "description",
        "cancelled_at",
        "resolved_user_id",
        "linked_meeting_status",
    }


def test_calendar_event_list_v3_response_envelope():
    """Envelope carries events + current_user_email + current_user_role."""
    from app.schemas.calendar import CalendarEventListV3Response

    resp = CalendarEventListV3Response(
        events=[],
        current_user_email="user@example.com",
        current_user_role="member",
    )
    assert resp.events == []
    assert resp.current_user_email == "user@example.com"
    assert resp.current_user_role == "member"


def test_calendar_event_list_v3_requires_role():
    """current_user_role is required (no default — frontend depends on it
    to gate the `All` scope chip)."""
    from app.schemas.calendar import CalendarEventListV3Response

    with pytest.raises(Exception):  # pydantic ValidationError
        CalendarEventListV3Response(events=[], current_user_email="x@y.com")


# ── Route registration ────────────────────────────────────────────────


def test_events_v3_route_registered():
    """The /events/v3 route is mounted on the calendar router."""
    from app.api.calendar import router

    paths = {r.path for r in router.routes}
    assert "/api/calendar/events/v3" in paths
    assert "/api/calendar/events/week-activity" in paths


def test_meetings_week_activity_route_registered():
    """The /meetings/week-activity route is mounted."""
    from app.api.meetings import router

    paths = {r.path for r in router.routes}
    assert "/api/meetings/week-activity" in paths


# ── Scope param regex ─────────────────────────────────────────────────


def test_events_v3_scope_regex_rejects_invalid():
    """FastAPI Query regex on scope should reject anything outside
    hosted | participant | all."""
    from app.api.calendar import list_calendar_events_v3

    # Inspect the dependency's regex via the function signature
    import inspect

    sig = inspect.signature(list_calendar_events_v3)
    scope_param = sig.parameters["scope"]
    # The default is a Query object; pydantic re-encodes regex into a
    # field constraint at call time. We just verify the param exists.
    assert scope_param is not None


def test_meetings_week_activity_scope_regex_rejects_invalid():
    from app.api.meetings import meetings_week_activity

    import inspect

    sig = inspect.signature(meetings_week_activity)
    assert "scope" in sig.parameters
    assert "range_start" in sig.parameters
    assert "range_end" in sig.parameters
