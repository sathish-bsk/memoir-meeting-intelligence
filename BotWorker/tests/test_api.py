"""
Tests for FastAPI endpoints: /health, /status, /join, /stop.
"""
from unittest.mock import patch, AsyncMock

import pytest

from app.main import _state, detect_platform
from app.models.enums import BotState


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["state"] == "idle"


@pytest.mark.asyncio
async def test_status_idle(client):
    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "idle"
    assert data["job_id"] is None
    assert data["chunks_saved"] == 0
    assert data["transcript"] is None


@pytest.mark.asyncio
async def test_status_with_active_job(client):
    _state["job_id"] = "test-123"
    _state["state"] = BotState.IN_MEETING
    _state["meeting_url"] = "https://meet.google.com/abc-defg-hij"
    _state["chunks_saved"] = 5

    resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "in_meeting"
    assert data["job_id"] == "test-123"
    assert data["chunks_saved"] == 5


@pytest.mark.asyncio
async def test_join_valid_url(client):
    with patch("app.services.lifecycle.run_meeting_lifecycle", new_callable=AsyncMock):
        resp = await client.post("/join", json={
            "job_id": "job-1",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_join_invalid_url(client):
    resp = await client.post("/join", json={
        "job_id": "job-1",
        "meeting_url": "https://example.com/meeting/123",
    })
    assert resp.status_code == 400
    assert "Unsupported meeting URL" in resp.json()["detail"]


@pytest.mark.parametrize("url,platform", [
    ("https://meet.google.com/abc-defg-hij", "google_meet"),
    ("https://zoom.us/j/123456", "zoom"),
    ("https://us02web.zoom.us/wc/join/123456", "zoom"),
    ("https://teams.microsoft.com/l/meetup-join/abc", "teams"),
    ("https://teams.live.com/meet/123", "teams"),
])
def test_detect_platform(url, platform):
    assert detect_platform(url) == platform


@pytest.mark.parametrize("url", [
    "https://example.com/meeting/123",
    "https://meet.google.com/not-a-code",
    "http://zoom.us/j/123456",  # https only
])
def test_detect_platform_rejects_unsupported(url):
    with pytest.raises(ValueError):
        detect_platform(url)


@pytest.mark.asyncio
async def test_join_busy(client):
    _state["state"] = BotState.IN_MEETING

    resp = await client.post("/join", json={
        "job_id": "job-2",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
    })
    assert resp.status_code == 409
    assert "busy" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_stop_no_active_meeting(client):
    resp = await client.post("/stop", json={"reason": "test"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_stop_active_meeting(client):
    import asyncio
    _state["state"] = BotState.IN_MEETING
    _state["job_id"] = "job-1"
    _state["stop_event"] = asyncio.Event()

    resp = await client.post("/stop", json={"reason": "manual"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopping"
    assert _state["stop_event"].is_set()
