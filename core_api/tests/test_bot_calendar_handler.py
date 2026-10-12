"""Unit tests for bot_calendar_handler — focuses on the pure logic of
ownership resolution and the dispatch-reset reschedule/cancel rules.

DB interactions are mocked (no live Postgres needed).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make `app.*` imports resolvable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bot_calendar_handler import (  # noqa: E402
    BOT_INVITE_EMAIL,
    PRE_DISPATCH_STATUSES,
    IN_FLIGHT_STATUSES,
    RESCHEDULE_TIME_THRESHOLD_SECONDS,
    _bot_in_attendees,
    apply_dispatch_reset_if_needed,
    is_bot_calendar_connection,
)


# ── Pure helpers ─────────────────────────────────────────────────────


class TestBotInAttendees:
    def test_returns_true_when_bot_email_present(self):
        attendees = [
            {"email": "alice@example.com"},
            {"email": BOT_INVITE_EMAIL},
            {"email": "bob@example.com"},
        ]
        assert _bot_in_attendees(attendees) is True

    def test_case_insensitive(self):
        attendees = [{"email": BOT_INVITE_EMAIL.upper()}]
        assert _bot_in_attendees(attendees) is True

    def test_trims_whitespace(self):
        attendees = [{"email": f"  {BOT_INVITE_EMAIL}  "}]
        assert _bot_in_attendees(attendees) is True

    def test_returns_false_when_absent(self):
        attendees = [{"email": "alice@example.com"}, {"email": "bob@example.com"}]
        assert _bot_in_attendees(attendees) is False

    def test_handles_none(self):
        assert _bot_in_attendees(None) is False

    def test_handles_empty(self):
        assert _bot_in_attendees([]) is False

    def test_skips_non_dict_entries(self):
        attendees = ["not a dict", None, {"email": BOT_INVITE_EMAIL}]
        assert _bot_in_attendees(attendees) is True


class TestIsBotCalendarConnection:
    def test_true_when_provider_matches(self):
        conn = SimpleNamespace(provider="bot_calendar")
        assert is_bot_calendar_connection(conn) is True

    def test_false_for_google(self):
        conn = SimpleNamespace(provider="google")
        assert is_bot_calendar_connection(conn) is False

    def test_false_for_outlook(self):
        conn = SimpleNamespace(provider="outlook")
        assert is_bot_calendar_connection(conn) is False


# ── Dispatch-reset logic ─────────────────────────────────────────────


@pytest.fixture
def now_utc():
    return datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)


def _make_cal_event(*, bot_scheduled, sequence, start_time, linked_meeting_id=None):
    """Build a CalendarEvent stand-in object."""
    return SimpleNamespace(
        bot_scheduled=bot_scheduled,
        ics_sequence=sequence,
        start_time=start_time,
        linked_meeting_id=linked_meeting_id,
        bot_retry_count=0,
        cancelled_at=None,
    )


def _make_meeting(*, status, bot_job_id=None):
    return SimpleNamespace(
        status=status,
        bot_job_id=bot_job_id,
        failure_reason=None,
    )


@pytest.mark.asyncio
async def test_no_reset_when_unscheduled_and_not_cancelled(now_utc):
    """Case (a) from plan §4.8 — pre-dispatch event, simple update."""
    cal_event = _make_cal_event(
        bot_scheduled=False, sequence=1, start_time=now_utc + timedelta(hours=2)
    )
    stop_bot = AsyncMock()
    result = await apply_dispatch_reset_if_needed(
        db=MagicMock(),
        cal_event=cal_event,
        new_sequence=2,
        new_start_time=now_utc + timedelta(hours=3),  # rescheduled
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is False
    assert cal_event.bot_scheduled is False  # untouched
    stop_bot.assert_not_called()


@pytest.mark.asyncio
async def test_reset_when_scheduled_and_time_shifted(now_utc):
    """Case (b) — rescheduled after dispatch, before meeting starts."""
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=1, start_time=now_utc + timedelta(hours=2),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="scheduled")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=2,
        new_start_time=now_utc + timedelta(hours=4),  # 2h later
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is True
    assert cal_event.bot_scheduled is False
    assert cal_event.bot_retry_count == 0
    assert cal_event.linked_meeting_id is None
    assert meeting.status == "failed"
    assert meeting.failure_reason == "calendar_reschedule"
    # status='scheduled' is pre-dispatch — stop_bot should NOT fire.
    stop_bot.assert_not_called()


@pytest.mark.asyncio
async def test_reset_calls_stop_bot_when_in_flight(now_utc):
    """If meeting is already joining/waiting, stop the bot too."""
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=1, start_time=now_utc + timedelta(minutes=5),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="waiting_in_lobby", bot_job_id="job-abc")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=2,
        new_start_time=now_utc + timedelta(hours=1),
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is True
    stop_bot.assert_awaited_once_with("job-abc")
    assert meeting.status == "failed"
    assert meeting.failure_reason == "calendar_reschedule"


@pytest.mark.asyncio
async def test_no_reset_when_meeting_already_recording(now_utc):
    """Case (c) — meeting is in-progress or done; reschedule is a no-op."""
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=1, start_time=now_utc - timedelta(minutes=5),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="recording", bot_job_id="job-abc")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=2,
        new_start_time=now_utc + timedelta(hours=1),
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    # Reset DID happen on cal_event flags (organizer rescheduled away
    # from this meeting time), but the meeting row stayed alone.
    assert result is True
    stop_bot.assert_not_called()
    assert meeting.status == "recording"  # untouched
    assert cal_event.bot_scheduled is False  # eligible for fresh dispatch


@pytest.mark.asyncio
async def test_cancellation_sets_cancelled_at(now_utc):
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=1, start_time=now_utc + timedelta(hours=2),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="pending")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=2,
        new_start_time=cal_event.start_time,  # time didn't shift, but cancelled
        new_status="cancelled",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is True
    assert cal_event.cancelled_at is not None
    assert meeting.status == "failed"
    assert meeting.failure_reason == "calendar_cancelled"


@pytest.mark.asyncio
async def test_no_reset_when_only_title_changes(now_utc):
    """Sequence didn't bump, time didn't shift — a title/attendee
    update should NOT trigger the reset path."""
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=2, start_time=now_utc + timedelta(hours=2),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="scheduled")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=2,  # same
        new_start_time=cal_event.start_time + timedelta(seconds=30),  # < threshold
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is False
    assert cal_event.bot_scheduled is True  # untouched
    stop_bot.assert_not_called()


@pytest.mark.asyncio
async def test_reschedule_threshold(now_utc):
    """Time shifts under RESCHEDULE_TIME_THRESHOLD_SECONDS should NOT
    trigger reset (sub-minute drift between ICS sources)."""
    cal_event = _make_cal_event(
        bot_scheduled=True, sequence=2, start_time=now_utc + timedelta(hours=2),
        linked_meeting_id="meeting-1",
    )
    meeting = _make_meeting(status="scheduled")
    db = MagicMock()
    db.get = AsyncMock(return_value=meeting)
    stop_bot = AsyncMock()

    result = await apply_dispatch_reset_if_needed(
        db=db,
        cal_event=cal_event,
        new_sequence=None,
        new_start_time=cal_event.start_time
        + timedelta(seconds=RESCHEDULE_TIME_THRESHOLD_SECONDS - 1),
        new_status="confirmed",
        orchestrator_stop_bot=stop_bot,
    )
    assert result is False
