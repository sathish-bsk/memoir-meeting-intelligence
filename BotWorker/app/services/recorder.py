"""
FFmpeg segment muxer that records lossless audio from PulseAudio as 30s WAV/PCM chunks.
"""
import asyncio
import glob
import signal
from pathlib import Path
from typing import Callable, Optional, Awaitable, Set

from app.core.config import settings
from app.core.logging import get_job_logger
from app.services._audio_utils import chunk_rms_dbfs, SILENCE_RMS_DBFS


class RecorderError(Exception):
    """Error during audio recording."""
    pass


class AudioRecorder:
    """Records audio from PulseAudio sink monitor using FFmpeg segment muxer."""

    def __init__(
        self,
        job_id: str,
        chunk_dir: Path,
        on_chunk_ready: Optional[Callable[[Path, int], Awaitable[None]]] = None,
    ):
        self.job_id = job_id
        self.chunk_dir = chunk_dir
        self.on_chunk_ready = on_chunk_ready
        self.logger = get_job_logger(job_id)

        self._process: Optional[asyncio.subprocess.Process] = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._known_chunks: Set[str] = set()
        self._chunk_index: int = 0
        self._running: bool = False
        self._spawn_time: Optional[float] = None
        self._first_stderr_logged: bool = False
        self._first_audio_logged: bool = False

    async def start(self):
        """Start FFmpeg recording with segment muxer."""
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

        sink_monitor = f"{settings.PULSE_SINK_NAME}.monitor"
        chunk_pattern = str(self.chunk_dir / "chunk_%04d.wav")

        self.logger.info(
            f"Starting recorder: source={sink_monitor}, "
            f"rate=48000, channels=1, codec=pcm_s16le, "
            f"chunk_duration={settings.CHUNK_DURATION_SECONDS}s, "
            f"chunks -> {self.chunk_dir}"
        )

        # pcm_s16le matches the bot_sink internal format (s16le, set in
        # Dockerfile system.pa). PulseAudio → FFmpeg is a straight passthrough
        # with no format conversion. Reverted from pcm_f32le on 2026-05-12 —
        # the f32 chunks were 2× the size with zero-padded lower bits
        # (source samples were already s16-precision after the bot_sink
        # revert in commit 5e860b8). The merge step in storage.py handles
        # s16 → s16 WAV or s16 → FLAC encoding without any quantization
        # step, so no dither is needed.
        #
        # Mono capture (-ac 1): bot_sink delivers bit-exact dual-mono — L-R
        # difference measured at 0.0 (exact zero) on production chunks
        # 2026-05-14. Storing one channel is information-preserving and
        # halves chunk size + R2 upload bandwidth.
        cmd = [
            "ffmpeg",
            "-thread_queue_size", "1024",
            "-f", "pulse",
            "-i", sink_monitor,
            "-c:a", "pcm_s16le",
            "-ar", "48000",
            "-ac", "1",
            "-f", "segment",
            "-segment_time", str(settings.CHUNK_DURATION_SECONDS),
            "-segment_format", "wav",
            "-reset_timestamps", "1",
            chunk_pattern,
        ]

        self._spawn_time = asyncio.get_event_loop().time()
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True
        self.logger.info(
            f"FFmpeg spawned (PID: {self._process.pid}, source={sink_monitor})"
        )

        # Start chunk watcher and stderr reader
        self._watcher_task = asyncio.create_task(self._watch_chunks())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def stop(self) -> int:
        """
        Stop recording gracefully.
        Sends SIGINT to FFmpeg to finalize the current segment, then cleans up.
        Returns total chunk count.
        """
        self._running = False
        self.logger.info("Stopping recorder...")

        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    self.logger.warning("FFmpeg didn't stop gracefully, sending SIGKILL")
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass

        # Cancel watcher
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass

        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass

        # Process any remaining chunks
        await self._scan_final_chunks()

        total = len(self._known_chunks)
        self.logger.info(f"Recorder stopped. Total chunks: {total}")
        return total

    async def _watch_chunks(self):
        """Poll for new chunk files every 2 seconds."""
        try:
            while self._running:
                await asyncio.sleep(2)
                await self._process_new_chunks(exclude_latest=True)
        except asyncio.CancelledError:
            pass

    async def _process_new_chunks(self, exclude_latest: bool = True):
        """Find and process new chunk files."""
        pattern = str(self.chunk_dir / "chunk_*.wav")
        all_files = sorted(glob.glob(pattern))

        if not all_files:
            return

        # If exclude_latest, skip the last file (still being written by FFmpeg)
        files_to_process = all_files[:-1] if exclude_latest and len(all_files) > 1 else []

        for filepath in files_to_process:
            if filepath not in self._known_chunks:
                self._known_chunks.add(filepath)
                path = Path(filepath)
                chunk_index = self._chunk_index
                self._chunk_index += 1
                self._log_chunk_audio_level(path, chunk_index)
                self.logger.info(f"Chunk ready: {path.name} (index: {chunk_index})")

                if self.on_chunk_ready:
                    try:
                        await self.on_chunk_ready(path, chunk_index)
                    except Exception as e:
                        self.logger.error(f"Error in chunk callback: {e}")

    async def _scan_final_chunks(self):
        """After FFmpeg stops, process all remaining chunks."""
        pattern = str(self.chunk_dir / "chunk_*.wav")
        all_files = sorted(glob.glob(pattern))

        for filepath in all_files:
            if filepath not in self._known_chunks:
                self._known_chunks.add(filepath)
                path = Path(filepath)
                chunk_index = self._chunk_index
                self._chunk_index += 1
                self._log_chunk_audio_level(path, chunk_index)
                self.logger.info(f"Final chunk: {path.name} (index: {chunk_index})")

                if self.on_chunk_ready:
                    try:
                        await self.on_chunk_ready(path, chunk_index)
                    except Exception as e:
                        self.logger.error(f"Error in chunk callback: {e}")

    def _log_chunk_audio_level(self, path: Path, chunk_index: int):
        """Emit a one-shot log the first time we see a non-silent chunk."""
        if self._first_audio_logged:
            return
        rms_db = chunk_rms_dbfs(path)
        if rms_db <= SILENCE_RMS_DBFS:
            self.logger.info(
                f"Chunk {chunk_index} ({path.name}) appears silent: RMS={rms_db:.1f} dBFS"
            )
            return
        self._first_audio_logged = True
        elapsed = (
            asyncio.get_event_loop().time() - self._spawn_time
            if self._spawn_time is not None
            else float("nan")
        )
        self.logger.info(
            f"First non-silent audio detected at chunk {chunk_index} "
            f"({path.name}, RMS={rms_db:.1f} dBFS, "
            f"{elapsed:.1f}s after FFmpeg spawn)"
        )

    async def _read_stderr(self):
        """Read FFmpeg stderr for monitoring and crash detection."""
        try:
            while self._running and self._process:
                if self._process.stderr:
                    line = await self._process.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if not decoded:
                        continue
                    if not self._first_stderr_logged and self._spawn_time is not None:
                        self._first_stderr_logged = True
                        elapsed = asyncio.get_event_loop().time() - self._spawn_time
                        self.logger.info(
                            f"FFmpeg first stderr output ({elapsed * 1000:.0f}ms after spawn): {decoded}"
                        )
                    if any(kw in decoded.lower() for kw in ["error", "fatal", "failed"]):
                        self.logger.error(f"FFmpeg: {decoded}")
                else:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning(f"Stderr reader error: {e}")
        finally:
            if self._process:
                returncode = self._process.returncode
                if self._running and returncode not in (None, 0):
                    self.logger.error(
                        f"FFmpeg exited unexpectedly with return code {returncode}"
                    )
