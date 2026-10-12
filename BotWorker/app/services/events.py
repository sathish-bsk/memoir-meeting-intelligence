"""
Fire-and-forget event sender with periodic heartbeats.
Sends events to the backend API for status tracking.
"""
import asyncio
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_job_logger
from app.models.enums import EventType
from app.models.schemas import BotEvent


class EventSender:
    """Sends bot events to the backend. Best-effort, never raises."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.logger = get_job_logger(job_id)
        self._client: Optional[httpx.AsyncClient] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop_heartbeat()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_event(self, event_type: EventType, data: dict[str, Any] | None = None):
        """Send an event to the backend. Best-effort with 2 retries.

        Per-attempt timing instrumentation — added 2026-05-14 to find why
        MEETING_ENDED's send_event was observed taking 5.2 s in production.
        Each attempt logs its individual POST latency; backoff sleeps log
        their own duration. Compare attempt latencies to identify whether
        the slow case is one slow POST (server-side) or retry storm.
        """
        event = BotEvent(
            job_id=self.job_id,
            event_type=event_type,
            timestamp=datetime.utcnow(),
            data=data or {},
        )

        url = f"{settings.BACKEND_URL}/api/bots/events"
        _t_call_start = time.monotonic()

        for attempt in range(3):
            _t_attempt_start = time.monotonic()
            try:
                if not self._client:
                    return
                resp = await self._client.post(url, json=event.model_dump(mode="json"))
                _t_attempt_end = time.monotonic()
                if resp.status_code < 400:
                    self.logger.info(
                        f"[EVENT_TIMING] {event_type.value} attempt={attempt + 1} "
                        f"status={resp.status_code} took="
                        f"{(_t_attempt_end - _t_attempt_start) * 1000:.0f}ms "
                        f"total={(_t_attempt_end - _t_call_start) * 1000:.0f}ms"
                    )
                    return
                self.logger.warning(
                    f"[EVENT_TIMING] {event_type.value} attempt={attempt + 1} "
                    f"FAIL status={resp.status_code} took="
                    f"{(_t_attempt_end - _t_attempt_start) * 1000:.0f}ms"
                )
            except Exception as e:
                _t_attempt_end = time.monotonic()
                self.logger.warning(
                    f"[EVENT_TIMING] {event_type.value} attempt={attempt + 1} "
                    f"EXCEPTION took="
                    f"{(_t_attempt_end - _t_attempt_start) * 1000:.0f}ms: {e}"
                )

            if attempt < 2:
                _backoff = 1 * (attempt + 1)
                self.logger.info(
                    f"[EVENT_TIMING] {event_type.value} backoff "
                    f"sleep={_backoff}s before retry"
                )
                await asyncio.sleep(_backoff)

    async def start_heartbeat(self):
        """Start periodic heartbeat events."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Heartbeat started")

    async def stop_heartbeat(self):
        """Stop the heartbeat task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Heartbeat stopped")

    async def _heartbeat_loop(self):
        """Send heartbeat events at configured interval."""
        try:
            while True:
                await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
                await self.send_event(EventType.HEARTBEAT)
        except asyncio.CancelledError:
            pass
