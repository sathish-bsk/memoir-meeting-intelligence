"""
Meeting end detection by polling the browser for end indicators.
Also enforces max meeting duration timeout.
"""
import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import get_job_logger
from app.services.base_bot import MeetingBot


class MeetingMonitor:
    """Polls for meeting end and enforces max duration."""

    def __init__(self, job_id: str, bot: MeetingBot):
        self.job_id = job_id
        self.bot = bot
        self.logger = get_job_logger(job_id)
        self.meeting_ended = asyncio.Event()
        self.end_reason: Optional[str] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._timeout_task: Optional[asyncio.Task] = None
        self._noone_joined_task: Optional[asyncio.Task] = None
        self._anyone_joined = False
        self._peak_participants = 0
        self.participant_names: set[str] = set()  # All names seen during meeting

    async def start(self):
        """Start monitoring for meeting end."""
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._timeout_task = asyncio.create_task(self._max_duration_timeout())
        self._noone_joined_task = asyncio.create_task(self._noone_joined_timeout())
        self.logger.info("Meeting monitor started")

    async def stop(self):
        """Stop monitoring."""
        for task in [self._poll_task, self._timeout_task, self._noone_joined_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.logger.info("Meeting monitor stopped")

    async def _capture_names(self):
        """Scrape participant names from the meeting UI."""
        try:
            names = await self.bot.get_participant_names()
            if names:
                new_names = set(names) - self.participant_names
                if new_names:
                    self.participant_names.update(new_names)
                    self.logger.info(f"New participants detected: {new_names}")
        except Exception:
            pass

    async def _poll_loop(self):
        """Poll for meeting end every N seconds."""
        try:
            poll_count = 0
            while not self.meeting_ended.is_set():
                await asyncio.sleep(settings.MEETING_END_POLL_INTERVAL)
                poll_count += 1

                # Dismiss any popups periodically (every 5th cycle)
                if poll_count % 5 == 0:
                    await self.bot.dismiss_in_meeting_popups()

                # Capture participant names FIRST (every 5th cycle ~10s)
                # Must run before end detection — otherwise the loop exits
                # before names are captured when the meeting ends.
                if poll_count % 5 == 0:
                    await self._capture_names()

                # Check DOM signals (meeting ended text, removal, disconnect)
                reason = await self.bot.detect_meeting_end()
                if reason:
                    await self._capture_names()  # Final scrape before exit
                    self.end_reason = reason
                    self.meeting_ended.set()
                    self.logger.info(f"Meeting ended: {reason}")
                    return

                # Check participant count (every 2nd cycle)
                if poll_count % 2 == 0:
                    count = await self.bot.get_participant_count()
                    if count is not None:
                        if count > self._peak_participants:
                            self._peak_participants = count
                        if count > 1 and not self._anyone_joined:
                            self._anyone_joined = True
                            self.logger.info(f"Participants joined — count: {count}")
                        if self._anyone_joined and count <= 1:
                            await self._capture_names()  # Final scrape before exit
                            self.end_reason = "everyone_left"
                            self.meeting_ended.set()
                            self.logger.info(
                                f"Everyone left (count={count}, peak={self._peak_participants})"
                            )
                            return
        except asyncio.CancelledError:
            pass

    async def _noone_joined_timeout(self):
        """Leave if no one ever joins within the timeout."""
        try:
            await asyncio.sleep(settings.NOONE_JOINED_TIMEOUT_SECONDS)
            if not self.meeting_ended.is_set() and not self._anyone_joined:
                self.end_reason = "noone_joined"
                self.meeting_ended.set()
                self.logger.warning(
                    f"No one joined within {settings.NOONE_JOINED_TIMEOUT_SECONDS}s — leaving"
                )
        except asyncio.CancelledError:
            pass

    async def _max_duration_timeout(self):
        """Enforce maximum meeting duration."""
        try:
            await asyncio.sleep(settings.MEETING_MAX_DURATION_SECONDS)
            if not self.meeting_ended.is_set():
                self.end_reason = "max_duration_reached"
                self.meeting_ended.set()
                self.logger.warning(
                    f"Meeting reached max duration ({settings.MEETING_MAX_DURATION_SECONDS}s)"
                )
        except asyncio.CancelledError:
            pass
