"""Unit tests for meeting_status_cache — Redis-backed /status +
/processing-status cache.

Redis is mocked via AsyncMock (matches the convention in other core_api
tests; no live Redis or fakeredis dependency needed).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import meeting_status_cache  # noqa: E402


def _mock_db_with_t2_status(status: str | None):
    """Build an AsyncMock SQLAlchemy session whose db.execute() returns
    a result object with .mappings().first() yielding the given T2 row
    (or None for no row).

    SQLAlchemy's execute() is async but .mappings() and .first() are
    sync — AsyncMock alone would make them awaitable and break the
    code-under-test, so we hand-wire the synchronous chain.
    """
    db = AsyncMock()
    result = MagicMock()
    if status is None:
        result.mappings.return_value.first.return_value = None
    else:
        result.mappings.return_value.first.return_value = {"status": status}
    db.execute.return_value = result
    return db


def _fake_meeting(
    *,
    status: str = "joining",
    intelligence_status: str | None = "pending",
    recording_started_at: datetime | None = None,
    team_id: uuid.UUID | None = None,
    meeting_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=meeting_id or uuid.uuid4(),
        team_id=team_id or uuid.uuid4(),
        status=status,
        intelligence_status=intelligence_status,
        recording_started_at=recording_started_at,
    )


# ── publish() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_writes_hash_and_ttl_for_non_terminal():
    redis = AsyncMock()
    meeting = _fake_meeting(status="joining")

    await meeting_status_cache.publish(
        redis, meeting, crm_extraction_status="pending"
    )

    redis.hset.assert_called_once()
    args, kwargs = redis.hset.call_args
    key = args[0]
    assert key == f"meeting_status:{meeting.id}"
    mapping = kwargs["mapping"]
    assert mapping["status"] == "joining"
    assert mapping["intelligence_status"] == "pending"
    assert mapping["recording_started_at"] == ""
    assert mapping["team_id"] == str(meeting.team_id)
    assert mapping["crm_extraction_status"] == "pending"

    redis.expire.assert_called_once_with(key, meeting_status_cache.TTL_SECONDS)
    redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_publish_encodes_recording_started_at_as_iso():
    redis = AsyncMock()
    rsa = datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
    meeting = _fake_meeting(status="recording", recording_started_at=rsa)

    await meeting_status_cache.publish(
        redis, meeting, crm_extraction_status="pending"
    )

    mapping = redis.hset.call_args.kwargs["mapping"]
    assert mapping["recording_started_at"] == rsa.isoformat()


@pytest.mark.asyncio
async def test_publish_encodes_null_intelligence_status_as_empty_string():
    redis = AsyncMock()
    meeting = _fake_meeting(intelligence_status=None)

    await meeting_status_cache.publish(
        redis, meeting, crm_extraction_status="pending"
    )

    mapping = redis.hset.call_args.kwargs["mapping"]
    assert mapping["intelligence_status"] == ""


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "fast_email_only"])
@pytest.mark.asyncio
async def test_publish_deletes_key_on_terminal_status(terminal_status):
    redis = AsyncMock()
    meeting = _fake_meeting(status=terminal_status)

    await meeting_status_cache.publish(
        redis, meeting, crm_extraction_status="completed"
    )

    redis.delete.assert_called_once_with(f"meeting_status:{meeting.id}")
    redis.hset.assert_not_called()
    redis.expire.assert_not_called()


@pytest.mark.asyncio
async def test_publish_swallows_redis_errors():
    redis = AsyncMock()
    redis.hset.side_effect = ConnectionError("redis down")
    meeting = _fake_meeting()

    # Must not raise — cache failures are non-fatal
    await meeting_status_cache.publish(
        redis, meeting, crm_extraction_status="pending"
    )


# ── read() ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_returns_none_on_miss():
    redis = AsyncMock()
    redis.hgetall.return_value = {}

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
async def test_read_decodes_hash_into_typed_fields():
    redis = AsyncMock()
    team_id = str(uuid.uuid4())
    rsa = datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
    redis.hgetall.return_value = {
        "status": "recording",
        "intelligence_status": "processing",
        "recording_started_at": rsa.isoformat(),
        "team_id": team_id,
        "crm_extraction_status": "pending",
    }

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is not None
    assert result["status"] == "recording"
    assert result["intelligence_status"] == "processing"
    assert result["recording_started_at"] == rsa
    assert result["team_id"] == team_id
    assert result["crm_extraction_status"] == "pending"


@pytest.mark.asyncio
async def test_read_decodes_empty_strings_to_none():
    redis = AsyncMock()
    redis.hgetall.return_value = {
        "status": "joining",
        "intelligence_status": "",
        "recording_started_at": "",
        "team_id": str(uuid.uuid4()),
        "crm_extraction_status": "pending",
    }

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is not None
    assert result["intelligence_status"] is None
    assert result["recording_started_at"] is None


@pytest.mark.asyncio
async def test_read_returns_none_on_redis_error():
    redis = AsyncMock()
    redis.hgetall.side_effect = ConnectionError("redis down")

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
async def test_read_returns_none_when_required_field_missing():
    """Hash missing the required 'status' field — treat as miss."""
    redis = AsyncMock()
    redis.hgetall.return_value = {
        "team_id": str(uuid.uuid4()),
        "crm_extraction_status": "pending",
    }

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
async def test_read_returns_none_for_old_schema_missing_crm_extraction_status():
    """Pre-deploy 4-field hash missing crm_extraction_status — treat
    as miss so cascade_from_db backfills the new schema. Self-healing
    upgrade path."""
    redis = AsyncMock()
    redis.hgetall.return_value = {
        "status": "recording",
        "intelligence_status": "pending",
        "recording_started_at": "",
        "team_id": str(uuid.uuid4()),
        # crm_extraction_status absent — written by Phase 2 code
    }

    result = await meeting_status_cache.read(redis, uuid.uuid4())

    assert result is None


# ── invalidate() ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_deletes_key():
    redis = AsyncMock()
    meeting_id = uuid.uuid4()

    await meeting_status_cache.invalidate(redis, meeting_id)

    redis.delete.assert_called_once_with(f"meeting_status:{meeting_id}")


@pytest.mark.asyncio
async def test_invalidate_swallows_redis_errors():
    redis = AsyncMock()
    redis.delete.side_effect = ConnectionError("redis down")

    await meeting_status_cache.invalidate(redis, uuid.uuid4())


# ── _derive_crm_extraction_status() ──────────────────────────────────


@pytest.mark.asyncio
async def test_derive_crm_returns_t2_status_when_row_exists():
    """When a T2 transcription_jobs row exists, its status wins."""
    db = _mock_db_with_t2_status("processing")

    result = await meeting_status_cache._derive_crm_extraction_status(
        db, uuid.uuid4(), "completed"
    )

    assert result == "processing"


@pytest.mark.parametrize(
    "intelligence_status,expected",
    [
        ("pending", "pending"),
        ("processing", "pending"),
        ("completed", "skipped"),
        ("failed", "skipped"),
        (None, "pending"),
        ("unknown_state", "pending"),
    ],
)
@pytest.mark.asyncio
async def test_derive_crm_falls_back_to_intelligence_when_no_t2_row(
    intelligence_status, expected
):
    db = _mock_db_with_t2_status(None)  # no T2 row

    result = await meeting_status_cache._derive_crm_extraction_status(
        db, uuid.uuid4(), intelligence_status
    )

    assert result == expected


# ── crm_status_with_processing_inference (read-time rule) ──────────


def test_crm_inference_upgrades_pending_when_progress_above_zero():
    """T2 percent > 0 + cached pending → return processing."""
    stages = {"crm_sync": {"percent": 42, "label": "Extracting CRM fields"}}

    assert (
        meeting_status_cache.crm_status_with_processing_inference("pending", stages)
        == "processing"
    )


def test_crm_inference_leaves_pending_when_progress_zero():
    """No T2 progress yet → keep pending."""
    stages = {"crm_sync": {"percent": 0, "label": ""}}

    assert (
        meeting_status_cache.crm_status_with_processing_inference("pending", stages)
        == "pending"
    )


def test_crm_inference_leaves_pending_when_crm_sync_stage_absent():
    """Stages dict empty (meeting_progress hash missing) → keep pending."""
    assert (
        meeting_status_cache.crm_status_with_processing_inference("pending", {})
        == "pending"
    )


@pytest.mark.parametrize("terminal", ["completed", "failed", "skipped", "processing"])
def test_crm_inference_passes_through_non_pending_values(terminal):
    """Only pending gets upgraded — terminals and existing processing
    pass through untouched even if percent is set."""
    stages = {"crm_sync": {"percent": 100, "label": "done"}}

    assert (
        meeting_status_cache.crm_status_with_processing_inference(terminal, stages)
        == terminal
    )
