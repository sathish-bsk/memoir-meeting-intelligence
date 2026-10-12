"""
Active speaker tracker — polls get_active_speaker() every 1s during a meeting
and records timestamped speaker events for post-meeting diarization mapping.
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.logging import get_job_logger


class SpeakerTracker:
    """Tracks active speakers during a meeting by polling the bot's UI detection."""

    def __init__(self, job_id: str, bot, recording_dir: Optional[str | Path] = None):
        self.job_id = job_id
        self.bot = bot
        self.logger = get_job_logger(job_id)
        self._events: list[dict] = []
        self._current_speaker: Optional[str] = None
        self._current_start: float = 0.0
        self._start_time: float = 0.0
        self._task: Optional[asyncio.Task] = None
        self._poll_count: int = 0
        self._none_count: int = 0
        self._debug_log: Optional[Path] = None
        if recording_dir:
            self._debug_log = Path(recording_dir) / "speaker_debug.log"
            self._debug_log.parent.mkdir(parents=True, exist_ok=True)

    def _write_debug(self, msg: str):
        """Append a debug line to the persistent log file in the recording dir."""
        if self._debug_log:
            try:
                ts = datetime.utcnow().strftime("%H:%M:%S")
                with open(self._debug_log, "a") as f:
                    f.write(f"[{ts}] {msg}\n")
            except Exception:
                pass

    async def start(self):
        """Start tracking active speakers."""
        self._start_time = time.monotonic()
        self._task = asyncio.create_task(self._track_loop())
        self.logger.info("Speaker tracker started")
        self._write_debug("Speaker tracker started")

    async def stop(self):
        """Stop tracking and finalize the last open event."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Close the last open event
        if self._current_speaker is not None:
            elapsed = time.monotonic() - self._start_time
            self._events.append({
                "speaker": self._current_speaker,
                "start": self._current_start,
                "end": round(elapsed, 1),
            })
            self._current_speaker = None

        self.logger.info(f"Speaker tracker stopped ({len(self._events)} events recorded)")
        self._write_debug(f"Speaker tracker stopped ({len(self._events)} events, {self._poll_count} polls, {self._none_count} nones)")

    async def _track_loop(self):
        """Poll get_active_speaker() every 1s and record speaker changes."""
        try:
            while True:
                await asyncio.sleep(1)
                elapsed = time.monotonic() - self._start_time
                self._poll_count += 1

                try:
                    speaker = await self.bot.get_active_speaker()
                except Exception as exc:
                    speaker = None
                    if self._poll_count <= 3:
                        self._write_debug(f"Poll #{self._poll_count}: EXCEPTION: {exc}")

                # Log diagnostics for first 10 polls and periodically after
                if speaker is None:
                    self._none_count += 1
                if self._poll_count <= 10 or self._poll_count % 30 == 0:
                    msg = (
                        f"Speaker poll #{self._poll_count}: "
                        f"detected={speaker!r}, "
                        f"events_so_far={len(self._events)}, "
                        f"none_count={self._none_count}"
                    )
                    self.logger.info(msg)
                    self._write_debug(msg)

                # One-time DOM debug dump if no speakers detected after 5 polls
                if self._poll_count == 5 and self._none_count == 5:
                    try:
                        debug_info = await self.bot.debug_active_speaker()
                        if debug_info:
                            self.logger.warning(
                                f"Active speaker detection not working — DOM debug: {debug_info}"
                            )
                            self._write_debug(f"DOM DEBUG DUMP:\n{debug_info}")
                    except Exception as exc:
                        self._write_debug(f"DOM debug failed: {exc}")

                if speaker != self._current_speaker:
                    # Close previous event
                    if self._current_speaker is not None:
                        self._events.append({
                            "speaker": self._current_speaker,
                            "start": self._current_start,
                            "end": round(elapsed, 1),
                        })
                    # Open new event
                    if speaker is not None:
                        self._current_start = round(elapsed, 1)
                        self.logger.info(f"Active speaker changed: {speaker}")
                        self._write_debug(f"Active speaker changed: {speaker}")
                    self._current_speaker = speaker

        except asyncio.CancelledError:
            pass

    def save_events(
        self,
        output_dir: str | Path,
        participants: Optional[set[str]] = None,
    ):
        """Write speaker_events.json to the output directory.

        Writes the v2 structured schema (post-migration 040):

            {
                "schema_version": 2,
                "captured_at": "...",
                "source": "bot_capture",
                "events":       [{speaker, start, end}, ...],
                "participants": [{display_name}, ...],
            }

        The `participants` arg comes from MeetingMonitor's continuously-
        observed roster (DOM participant panel). Captions only fire for
        people who actually spoke, so participants ⊇ events.speaker set
        in the typical case. If MeetingMonitor wasn't running (rare) or
        the panel was collapsed for the whole meeting, participants may
        be empty — that's fine, downstream consumers (TranscriptionBase's
        load_speaker_events_file helper) handle both arrays gracefully.

        TB consumers handle this schema AND the legacy v1 flat-array
        shape via the same loader shim, so this writer can flip without
        coordinating with TB deploys. See plan §5.3 for full rollout.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "speaker_events.json"

        participants_list = sorted(participants or set())
        doc = {
            "schema_version": 2,
            "captured_at": datetime.utcnow().isoformat() + "Z",
            "source": "bot_capture",
            "events": self._events,
            "participants": [{"display_name": n} for n in participants_list],
        }
        path.write_text(json.dumps(doc, indent=2))
        self.logger.info(
            f"Saved v2 speaker_events.json: {len(self._events)} events, "
            f"{len(participants_list)} participants → {path}"
        )
