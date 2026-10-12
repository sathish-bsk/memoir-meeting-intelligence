"""
Tests for EventSender: event payloads, heartbeat, best-effort delivery.
"""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx

from app.models.enums import EventType
from app.services.events import EventSender


class TestEventSender:
    @pytest.mark.asyncio
    async def test_send_event_success(self):
        async with EventSender("job-1") as sender:
            with patch.object(sender._client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = MagicMock(status_code=200)

                await sender.send_event(EventType.JOINED_MEETING)

                mock_post.assert_called_once()
                call_kwargs = mock_post.call_args
                payload = call_kwargs.kwargs["json"]
                assert payload["job_id"] == "job-1"
                assert payload["event_type"] == "joined_meeting"
                assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_send_event_with_data(self):
        async with EventSender("job-1") as sender:
            with patch.object(sender._client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = MagicMock(status_code=200)

                await sender.send_event(
                    EventType.CHUNK_SAVED,
                    {"chunk_index": 5, "storage_key": "job-1/chunk_0005.ogg"},
                )

                payload = mock_post.call_args.kwargs["json"]
                assert payload["data"]["chunk_index"] == 5

    @pytest.mark.asyncio
    async def test_send_event_retries_on_failure(self):
        async with EventSender("job-1") as sender:
            with patch.object(sender._client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.side_effect = httpx.ConnectError("refused")

                # Should not raise — best effort
                await sender.send_event(EventType.HEARTBEAT)

                assert mock_post.call_count == 3  # 3 attempts

    @pytest.mark.asyncio
    async def test_send_event_no_raise_on_error(self):
        async with EventSender("job-1") as sender:
            with patch.object(sender._client, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = MagicMock(status_code=500)

                # Should not raise
                await sender.send_event(EventType.BOT_ERROR, {"error": "test"})

    @pytest.mark.asyncio
    async def test_heartbeat_start_stop(self):
        async with EventSender("job-1") as sender:
            with patch.object(sender, "send_event", new_callable=AsyncMock):
                await sender.start_heartbeat()
                assert sender._heartbeat_task is not None
                assert not sender._heartbeat_task.done()

                await sender.stop_heartbeat()
                assert sender._heartbeat_task.done()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with EventSender("job-1") as sender:
            assert sender._client is not None
        assert sender._client is None
