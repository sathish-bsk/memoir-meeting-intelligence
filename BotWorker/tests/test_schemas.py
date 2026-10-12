"""
Tests for Pydantic schemas: validation, defaults, serialization.
"""
import pytest
from datetime import datetime

from app.models.enums import BotState, EventType
from app.models.schemas import (
    JoinRequest,
    StatusResponse,
    StopRequest,
    HealthResponse,
    BotEvent,
    ChunkSavedData,
    TranscriptionResult,
)


class TestJoinRequest:
    def test_required_fields(self):
        req = JoinRequest(job_id="j1", meeting_url="https://meet.google.com/abc-defg-hij")
        assert req.job_id == "j1"
        assert req.meeting_url == "https://meet.google.com/abc-defg-hij"
        assert req.bot_name is None

    def test_optional_bot_name(self):
        req = JoinRequest(job_id="j1", meeting_url="https://meet.google.com/abc-defg-hij", bot_name="My Bot")
        assert req.bot_name == "My Bot"

    def test_missing_required_fields(self):
        with pytest.raises(Exception):
            JoinRequest(job_id="j1")


class TestStatusResponse:
    def test_defaults(self):
        resp = StatusResponse()
        assert resp.state == BotState.IDLE
        assert resp.job_id is None
        assert resp.chunks_saved == 0
        assert resp.transcript is None
        assert resp.error is None

    def test_serialization(self):
        resp = StatusResponse(
            job_id="j1",
            state=BotState.IN_MEETING,
            chunks_saved=3,
        )
        data = resp.model_dump()
        assert data["state"] == "in_meeting"
        assert data["chunks_saved"] == 3


class TestStopRequest:
    def test_default_reason(self):
        req = StopRequest()
        assert req.reason == "manual_stop"

    def test_custom_reason(self):
        req = StopRequest(reason="timeout")
        assert req.reason == "timeout"


class TestHealthResponse:
    def test_defaults(self):
        resp = HealthResponse()
        assert resp.status == "ok"
        assert resp.state == BotState.IDLE


class TestBotEvent:
    def test_creation(self):
        event = BotEvent(
            job_id="j1",
            event_type=EventType.JOINED_MEETING,
        )
        assert event.job_id == "j1"
        assert event.event_type == EventType.JOINED_MEETING
        assert isinstance(event.timestamp, datetime)
        assert event.data == {}

    def test_with_data(self):
        event = BotEvent(
            job_id="j1",
            event_type=EventType.CHUNK_SAVED,
            data={"chunk_index": 5, "storage_key": "j1/chunk_0005.ogg"},
        )
        assert event.data["chunk_index"] == 5

    def test_json_serialization(self):
        event = BotEvent(
            job_id="j1",
            event_type=EventType.HEARTBEAT,
        )
        json_data = event.model_dump(mode="json")
        assert json_data["event_type"] == "heartbeat"
        assert isinstance(json_data["timestamp"], str)


class TestChunkSavedData:
    def test_creation(self):
        chunk = ChunkSavedData(
            chunk_index=0,
            storage_key="j1/chunk_0000.ogg",
            duration_seconds=30.0,
            file_size_bytes=12000,
        )
        assert chunk.chunk_index == 0
        assert chunk.file_size_bytes == 12000

    def test_defaults(self):
        chunk = ChunkSavedData(chunk_index=0, storage_key="test")
        assert chunk.duration_seconds == 0.0
        assert chunk.file_size_bytes == 0


class TestTranscriptionResult:
    def test_creation(self):
        result = TranscriptionResult(transcript="Hello world", duration_seconds=5.0)
        assert result.transcript == "Hello world"
        assert result.duration_seconds == 5.0


class TestEnums:
    def test_bot_state_values(self):
        assert BotState.IDLE == "idle"
        assert BotState.IN_MEETING == "in_meeting"
        assert BotState.TRANSCRIBING == "transcribing"
        assert BotState.COMPLETED == "completed"
        assert BotState.ERROR == "error"

    def test_event_type_values(self):
        assert EventType.JOIN_STARTED == "join_started"
        assert EventType.CHUNK_SAVED == "chunk_saved"
        assert EventType.HEARTBEAT == "heartbeat"
        assert EventType.TRANSCRIPTION_COMPLETE == "transcription_complete"
