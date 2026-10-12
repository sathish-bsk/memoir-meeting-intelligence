"""
Shared test fixtures for BotWorker tests.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, _state
from app.models.enums import BotState


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before each test."""
    _state["job_id"] = None
    _state["state"] = BotState.IDLE
    _state["meeting_url"] = None
    _state["joined_at"] = None
    _state["chunks_saved"] = 0
    _state["transcript"] = None
    _state["error"] = None
    _state["task"] = None
    _state["start_time"] = None
    _state["stop_event"] = None
    yield


@pytest.fixture
async def client():
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
