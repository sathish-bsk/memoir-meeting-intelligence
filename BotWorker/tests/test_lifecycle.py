"""
Tests for meeting lifecycle orchestrator: event flow, denial / timeout handling, cleanup on error.
"""
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.models.enums import BotState, EventType
from app.models.schemas import JoinRequest
from app.services.base_bot import MeetingDeniedError
from app.services.lifecycle import run_meeting_lifecycle

MEET_URL = "https://meet.google.com/abc-defg-hij"


def make_state():
    return {
        "job_id": None,
        "state": BotState.IDLE,
        "meeting_url": None,
        "joined_at": None,
        "chunks_saved": 0,
        "transcript": None,
        "error": None,
        "task": None,
        "start_time": None,
        "stop_event": asyncio.Event(),
    }


def make_bot():
    bot = AsyncMock()
    bot.set_event_sender = MagicMock()
    bot.join_meeting = AsyncMock(return_value=True)
    bot._is_in_meeting = AsyncMock(return_value=True)
    bot.get_participant_names = AsyncMock(return_value=["Alice", "Bob"])
    return bot


def make_monitor(end_reason="meeting_ended"):
    monitor = MagicMock()
    monitor.start = AsyncMock()
    monitor.stop = AsyncMock()
    monitor.meeting_ended = asyncio.Event()
    monitor.meeting_ended.set()  # meeting ends immediately
    monitor.end_reason = end_reason
    monitor.participant_names = set()
    monitor._peak_participants = 2
    return monitor


@pytest.fixture
def mocks(tmp_path):
    """Patch every collaborator of run_meeting_lifecycle; yields them for assertions."""
    bot = make_bot()
    monitor = make_monitor()

    events = AsyncMock()
    events.__aenter__ = AsyncMock(return_value=events)
    events.__aexit__ = AsyncMock(return_value=False)

    recorder = AsyncMock()
    recorder.stop = AsyncMock(return_value=3)

    tracker = MagicMock()
    tracker.start = AsyncMock()
    tracker.stop = AsyncMock()

    storage = MagicMock()
    storage.get_merged_audio = AsyncMock(return_value=tmp_path / "j1" / "merged.wav")

    with patch("app.main.create_bot", return_value=bot) as create_bot, \
         patch("app.services.lifecycle.EventSender", return_value=events), \
         patch("app.services.lifecycle.AudioRecorder", return_value=recorder), \
         patch("app.services.lifecycle.MeetingMonitor", return_value=monitor), \
         patch("app.services.lifecycle.SpeakerTracker", return_value=tracker), \
         patch("app.services.lifecycle.create_storage", return_value=storage), \
         patch("app.services.lifecycle._wait_for_sink_input_on_bot_sink", new_callable=AsyncMock), \
         patch("app.services.lifecycle.cleanup_all", new_callable=AsyncMock, return_value=3) as cleanup, \
         patch("app.services.lifecycle.settings") as settings:
        settings.AUDIO_CAPTURE_METHOD = "system"
        settings.STORAGE_DIR = str(tmp_path)
        settings.JOIN_MEETING_TIMEOUT_SECONDS = 5
        settings.PULSE_SINK_NAME = "bot_sink"
        yield MagicMock(
            bot=bot, create_bot=create_bot, events=events, recorder=recorder,
            monitor=monitor, tracker=tracker, storage=storage, cleanup=cleanup,
            settings=settings,
        )


def sent_events(events_mock):
    return [c[0][0] for c in events_mock.send_event.call_args_list]


def event_payload(events_mock, event_type):
    for c in events_mock.send_event.call_args_list:
        if c[0][0] == event_type:
            return c[0][1] if len(c[0]) > 1 else None
    raise AssertionError(f"{event_type} not sent")


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_successful_lifecycle(self, mocks):
        state = make_state()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        assert state["error"] is None
        assert state["state"] == BotState.COMPLETED
        assert state["joined_at"] is not None

        types = sent_events(mocks.events)
        expected_order = [
            EventType.JOIN_STARTED,
            EventType.JOINED_MEETING,
            EventType.RECORDING_STARTED,
            EventType.MEETING_ENDED,
            EventType.RECORDING_COMPLETE,
            EventType.CLEANUP_COMPLETE,
        ]
        assert [t for t in types if t in expected_order] == expected_order
        assert EventType.BOT_ERROR not in types

        complete = event_payload(mocks.events, EventType.RECORDING_COMPLETE)
        assert complete["recording_path"] == "j1/merged.wav"
        assert complete["total_chunks"] == 3
        assert complete["participants"] == ["Alice", "Bob"]  # final scrape before leaving
        assert complete["peak_participants"] == 2

        mocks.bot.leave_meeting.assert_awaited_once()
        mocks.recorder.stop.assert_awaited_once()
        mocks.tracker.save_events.assert_called_once()
        mocks.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recorder_starts_before_join(self, mocks):
        """System capture pre-starts FFmpeg so its cold start overlaps navigation."""
        order = []
        mocks.recorder.start.side_effect = lambda: order.append("recorder.start")

        async def join(_url):
            order.append("bot.join_meeting")
            return True
        mocks.bot.join_meeting.side_effect = join

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), make_state(), "Test Bot")

        assert order == ["recorder.start", "bot.join_meeting"]

    @pytest.mark.asyncio
    async def test_no_chunks_skips_recording_complete(self, mocks):
        mocks.recorder.stop.return_value = 0

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), make_state(), "Test Bot")

        assert EventType.RECORDING_COMPLETE not in sent_events(mocks.events)
        mocks.storage.get_merged_audio.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kicked_sends_bot_kicked(self, mocks):
        mocks.monitor.end_reason = "bot_kicked"

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), make_state(), "Test Bot")

        assert EventType.BOT_KICKED in sent_events(mocks.events)
        assert event_payload(mocks.events, EventType.MEETING_ENDED) == {"reason": "bot_kicked"}

    @pytest.mark.asyncio
    async def test_denied_sends_bot_denied(self, mocks):
        mocks.bot.join_meeting.side_effect = MeetingDeniedError("denied")
        state = make_state()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        types = sent_events(mocks.events)
        assert EventType.BOT_DENIED in types
        assert EventType.JOINED_MEETING not in types
        assert state["error"] == "Entry denied by host"
        mocks.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lobby_timeout_sends_bot_denied(self, mocks):
        mocks.settings.JOIN_MEETING_TIMEOUT_SECONDS = 0.01

        async def stuck_in_lobby(_url):
            await asyncio.sleep(10)
        mocks.bot.join_meeting.side_effect = stuck_in_lobby
        state = make_state()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        assert event_payload(mocks.events, EventType.BOT_DENIED)["reason"] == "lobby_timeout"
        assert "not admitted" in state["error"]
        mocks.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_admitted_sends_bot_error(self, mocks):
        mocks.bot.join_meeting.return_value = False
        state = make_state()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        assert event_payload(mocks.events, EventType.BOT_ERROR) == {"error": "Not admitted"}
        assert state["error"] == "Failed to join meeting"

    @pytest.mark.asyncio
    async def test_cleanup_always_runs(self, mocks):
        mocks.bot.launch.side_effect = Exception("browser crash")
        state = make_state()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        mocks.cleanup.assert_awaited_once()
        assert state["error"] == "browser crash"
        assert event_payload(mocks.events, EventType.BOT_ERROR) == {"error": "browser crash"}
        assert sent_events(mocks.events)[-1] == EventType.CLEANUP_COMPLETE

    @pytest.mark.asyncio
    async def test_manual_stop_ends_meeting(self, mocks):
        mocks.monitor.meeting_ended = asyncio.Event()  # meeting never ends on its own
        mocks.monitor.end_reason = None
        state = make_state()
        state["stop_event"].set()

        await run_meeting_lifecycle(JoinRequest(job_id="j1", meeting_url=MEET_URL), state, "Test Bot")

        assert event_payload(mocks.events, EventType.MEETING_ENDED) == {"reason": "manual_stop"}
        assert EventType.RECORDING_COMPLETE in sent_events(mocks.events)
