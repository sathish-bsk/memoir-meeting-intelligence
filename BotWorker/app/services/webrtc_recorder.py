"""
EXPERIMENTAL — Not used in production. Kept for future reference.

Tab audio capture recorder using Chrome tabCapture extension.
Requires AUDIO_CAPTURE_METHOD=webrtc to enable (not default).

Captures tab audio via chrome.tabCapture.capture() with all Chrome audio
processing disabled and PCM (lossless) encoding. Uses continuous recording
with requestData() to avoid WebM header issues across chunks.

Known limitations:
- Requires persistent browser context (heavy on low-RAM machines)
- Requires xdotool for extension invocation
- tabCapture still routes through Chrome's audio mixer (vibration persists)
- Not supported by Zoom (uses custom protocol, not WebRTC)
"""
import asyncio
import base64
import subprocess
import os
from pathlib import Path
from typing import Callable, Optional, Awaitable

from app.core.config import settings
from app.core.logging import get_job_logger

EXTENSION_ID = None

# tabCapture recording script with:
# - All audio processing disabled (no vibration/artifacts)
# - PCM lossless encoding (no Opus compression loss)
# - Continuous recording + requestData() (no WebM header issue)
START_TABCAPTURE = """
async (chunkMs) => {
    try {
        const stream = await new Promise((resolve, reject) => {
            chrome.tabCapture.capture({
                audio: true,
                video: false,
                audioConstraints: {
                    mandatory: {
                        echoCancellation: false,
                        noiseSuppression: false,
                        autoGainControl: false,
                        googEchoCancellation: false,
                        googAutoGainControl: false,
                        googNoiseSuppression: false,
                        googHighpassFilter: false,
                    },
                },
            }, (s) => {
                if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
                else if (!s) reject(new Error('No stream'));
                else resolve(s);
            });
        });

        // Opus 128kbps — excellent speech quality, 20x smaller than PCM
        // PCM is too large for real-time base64 transfer + conversion on low-RAM servers
        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus' : 'audio/webm';

        window.__memoirChunks = [];
        window.__memoirChunkIndex = 0;
        window.__memoirStream = stream;

        const recorderOpts = { mimeType, audioBitsPerSecond: 128000 };

        const recorder = new MediaRecorder(stream, recorderOpts);

        recorder.ondataavailable = async (e) => {
            if (e.data && e.data.size > 0) {
                const buffer = await e.data.arrayBuffer();
                const bytes = new Uint8Array(buffer);
                let binary = '';
                for (let i = 0; i < bytes.byteLength; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                window.__memoirChunks.push({
                    data: 'data:audio/webm;base64,' + btoa(binary),
                    index: window.__memoirChunkIndex++,
                    size: buffer.byteLength,
                });
            }
        };

        recorder.onerror = (e) => {
            console.error('[Memoir] Recorder error:', e);
            recorder.stop();
        };

        recorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
        };

        // Continuous recording — requestData() flushes without stopping
        // All chunks share the same WebM stream — no header issue
        recorder.start();
        window.__memoirRecorder = recorder;

        window.__memoirFlushInterval = setInterval(() => {
            if (recorder.state === 'recording') {
                recorder.requestData();
            }
        }, chunkMs);

        return {
            status: 'ok',
            mimeType,
            audioTracks: stream.getAudioTracks().length,
            trackInfo: stream.getAudioTracks().map(t => ({
                label: t.label, enabled: t.enabled, readyState: t.readyState,
            })),
        };
    } catch(err) {
        return { error: err.message || String(err) };
    }
}
"""

POLL_CHUNKS = """
(() => {
    const ready = (window.__memoirChunks || []).splice(0);
    return ready.map(c => ({ data: c.data, index: c.index, size: c.size }));
})();
"""

STOP_RECORDING = """
(() => {
    if (window.__memoirFlushInterval) clearInterval(window.__memoirFlushInterval);
    if (window.__memoirRecorder && window.__memoirRecorder.state !== 'inactive') {
        window.__memoirRecorder.requestData();
        window.__memoirRecorder.stop();
    }
})();
"""


class WebRTCRecorder:
    """Records tab audio via Chrome tabCapture extension with PCM encoding."""

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

        self._page = None
        self._ext_page = None
        self._context = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._chunk_count: int = 0

    @staticmethod
    def get_init_script() -> str:
        return ""

    async def _find_extension_id(self):
        global EXTENSION_ID
        if EXTENSION_ID:
            return EXTENSION_ID
        self.logger.info("Discovering extension ID...")
        try:
            for sw in self._context.service_workers:
                if "background.js" in sw.url and "chrome-extension://" in sw.url:
                    ext_id = sw.url.split("://")[1].split("/")[0]
                    EXTENSION_ID = ext_id
                    self.logger.info(f"Found extension ID: {ext_id}")
                    return ext_id
        except Exception as e:
            self.logger.warning(f"Extension discovery failed: {e}")
        return None

    async def start(self, page):
        self._page = page
        self._context = page.context
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info("Starting tab audio capture via tabCapture extension")

        await asyncio.sleep(1)
        ext_id = await self._find_extension_id()
        if not ext_id:
            raise RuntimeError("Audio capture extension not found")

        ext_page = await self._context.new_page()
        await ext_page.goto(f"chrome-extension://{ext_id}/options.html")
        await asyncio.sleep(0.5)
        self._ext_page = ext_page
        self.logger.info("Extension options page opened")

        await page.bring_to_front()
        await asyncio.sleep(0.3)

        env = dict(os.environ)
        env["DISPLAY"] = settings.DISPLAY
        self.logger.info("Invoking extension via xdotool...")
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "key", "ctrl+shift+y",
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        self.logger.info(f"xdotool exit code: {proc.returncode}")
        await asyncio.sleep(1)

        chunk_ms = settings.CHUNK_DURATION_SECONDS * 1000
        result = await ext_page.evaluate(START_TABCAPTURE, chunk_ms)

        if not result or result.get("error"):
            error = result.get("error", "Unknown") if result else "No response"
            self.logger.error(f"tabCapture failed: {error}")
            raise RuntimeError(f"Tab audio capture failed: {error}")

        self.logger.info(
            f"Tab audio capture started: mimeType={result.get('mimeType')}, "
            f"audioTracks={result.get('audioTracks')}, trackInfo={result.get('trackInfo')}"
        )

        self._running = True
        self._watcher_task = asyncio.create_task(self._watch_chunks())

    async def stop(self) -> int:
        self._running = False
        self.logger.info("Stopping tab audio recorder...")

        if self._ext_page:
            try:
                await self._ext_page.evaluate(STOP_RECORDING)
                await asyncio.sleep(1)
                await self._extract_chunks()
            except Exception as e:
                self.logger.warning(f"Error stopping recorder: {e}")

        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass

        if self._ext_page:
            try:
                await self._ext_page.close()
            except Exception:
                pass

        self.logger.info(f"Tab audio recorder stopped. Total chunks: {self._chunk_count}")
        return self._chunk_count

    async def _watch_chunks(self):
        try:
            while self._running:
                await asyncio.sleep(5)
                await self._extract_chunks()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Chunk watcher error: {e}")

    async def _extract_chunks(self):
        if not self._ext_page:
            return
        try:
            chunks = await self._ext_page.evaluate(POLL_CHUNKS)
        except Exception as e:
            self.logger.warning(f"Chunk poll failed: {e}")
            return

        for chunk in chunks:
            data_url = chunk.get("data", "")
            if not data_url or "," not in data_url:
                continue

            try:
                raw_bytes = base64.b64decode(data_url.split(",", 1)[1])
                self.logger.info(f"Extracted chunk: index={chunk.get('index')}, size={len(raw_bytes)} bytes")
            except Exception:
                self.logger.warning(f"Failed to decode chunk {chunk.get('index')}")
                continue

            # Append to single growing WebM file
            webm_path = self.chunk_dir / "recording.webm"
            with open(webm_path, "ab") as f:
                f.write(raw_bytes)

            # Convert the full file so far to WAV, then extract the new chunk
            wav_path = self._extract_wav_chunk(self._chunk_count)
            if wav_path and wav_path.exists():
                self.logger.info(f"Chunk ready: {wav_path.name} (index: {self._chunk_count})")
                if self.on_chunk_ready:
                    try:
                        await self.on_chunk_ready(wav_path, self._chunk_count)
                    except Exception as e:
                        self.logger.error(f"Chunk callback error: {e}")
                self._chunk_count += 1

    def _extract_wav_chunk(self, index: int) -> Optional[Path]:
        """Convert the full WebM recording and extract the latest chunk by time offset."""
        webm_path = self.chunk_dir / "recording.webm"
        full_wav = self.chunk_dir / "full_recording.wav"
        chunk_wav = self.chunk_dir / f"chunk_{index:04d}.wav"
        chunk_duration = settings.CHUNK_DURATION_SECONDS
        start_time = index * chunk_duration

        try:
            # Convert full WebM to WAV
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(webm_path),
                 "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
                 str(full_wav)],
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", errors="replace")
                self.logger.error(f"FFmpeg full convert failed: {stderr[-300:]}")
                return None

            # Extract chunk by time offset
            proc2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(full_wav),
                 "-ss", str(start_time), "-t", str(chunk_duration),
                 "-c:a", "pcm_s16le",
                 str(chunk_wav)],
                capture_output=True, timeout=30,
            )
            if proc2.returncode != 0:
                stderr = proc2.stderr.decode("utf-8", errors="replace")
                self.logger.error(f"FFmpeg chunk extract failed: {stderr[-300:]}")
                return None

            if chunk_wav.stat().st_size < 100:
                self.logger.warning(f"Chunk {index} too small ({chunk_wav.stat().st_size} bytes)")
                return None

            return chunk_wav
        except Exception as e:
            self.logger.error(f"Chunk extraction error: {e}")
            return None
