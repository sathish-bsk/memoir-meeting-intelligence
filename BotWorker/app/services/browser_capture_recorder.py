"""
BrowserCaptureRecorder — receive PCM audio from the BotWorker extension's
offscreen document over a localhost WebSocket and write 30s chunks to disk.

Public surface matches AudioRecorder:
  - start()
  - stop() -> int  (total chunk count)
  - on_chunk_ready(path, index) callback fired per chunk

Driving the extension uses the message-passing handshake documented in
plan/browser-capture-implementation-plan.md:

    BrowserCaptureRecorder.start()
      ├─ ingest.start()                  # bind 127.0.0.1:<random>
      ├─ open chrome-extension://<id>/control.html
      ├─ window.startCapture({targetUrl, wsPort}) on the control page
      │   └─ background.findMeetTab → getStreamId → START_TAB_AUDIO
      │       └─ ensureOffscreen → OFFSCREEN_START
      │           └─ getUserMedia → AudioContext → AudioWorklet → WS open + hello
      │               └─ OFFSCREEN_READY_TO_RECORD ──► resolves START_TAB_AUDIO
      ├─ ingest.wait_for_hello()         # Python-side confirmation
      └─ asyncio.create_task(_consume()) # frames flowing
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np

from app.core.config import settings
from app.core.logging import get_job_logger
from app.services._audio_utils import (
    SILENCE_RMS_DBFS,
    chunk_rms_dbfs,
    write_wav_f32le,
)
from app.services.browser_audio_ingest import BrowserAudioIngest


class BrowserCaptureRecorder:
    """Records meeting audio via Chromium tab capture + offscreen AudioWorklet."""

    def __init__(
        self,
        job_id: str,
        chunk_dir: Path,
        page,  # playwright Page bound to the meeting tab
        on_chunk_ready: Optional[Callable[[Path, int], Awaitable[None]]] = None,
    ):
        self.job_id = job_id
        self.chunk_dir = Path(chunk_dir)
        self.page = page
        self.on_chunk_ready = on_chunk_ready
        self.logger = get_job_logger(job_id)
        self.ingest = BrowserAudioIngest(self.logger)

        self._control_page = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._chunk_index: int = 0
        self._first_audio_logged: bool = False
        self._spawn_time: Optional[float] = None
        self._stopped: bool = False

    @property
    def healthy(self) -> bool:
        """Mirror BrowserAudioIngest.healthy. False means the recording is
        missing samples (queue drops) or the handshake was incomplete."""
        return self.ingest.healthy

    async def _invoke_extension(self) -> None:
        """Satisfy MV3 tabCapture's user-invocation requirement.

        chrome.tabCapture.getMediaStreamId rejects with "Extension has not
        been invoked for the current page" unless the extension was
        invoked on that tab via toolbar click, command, or popup. The
        only mechanism that works in headless Chrome under Xvfb is a real
        OS keypress matching the command shortcut declared in
        manifest.json. We send Ctrl+Shift+Y via xdotool while the meeting
        tab has focus; the background.js command listener records the
        invocation and GET_STREAM_ID validates it before calling Chrome.
        """
        await self.page.bring_to_front()
        # Give the X server a moment to update focus before xdotool fires.
        await asyncio.sleep(0.3)

        env = dict(os.environ)
        env["DISPLAY"] = settings.DISPLAY  # ":99" inside the BotWorker container
        self.logger.info("Invoking extension via xdotool key ctrl+shift+y")
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "key", "ctrl+shift+y",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "xdotool not installed in container — cannot invoke extension"
            ) from e
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("xdotool timed out (5s)")
        if proc.returncode != 0:
            raise RuntimeError(
                f"xdotool exit {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()}"
            )
        self.logger.info("xdotool keypress sent")

        # Give Chrome's command listener a moment to fire and record the
        # invocation. Without this gap, GET_STREAM_ID can race the listener
        # and report "invocation missing" even though the keypress landed.
        await asyncio.sleep(0.5)

    async def _find_extension_id(self) -> str:
        """Discover the loaded extension's ID by scanning service workers.

        The extension's background.js shows up as a service worker URL of the
        form `chrome-extension://<id>/background.js` once Chromium has loaded
        the extension. Persistent context launch is required for this to work.
        """
        ctx = self.page.context
        for _ in range(40):  # ~10s total at 0.25s
            for sw in ctx.service_workers:
                if "background.js" in sw.url and "chrome-extension://" in sw.url:
                    return sw.url.split("://")[1].split("/")[0]
            await asyncio.sleep(0.25)
        raise RuntimeError("extension service worker not found within 10s")

    async def start(self) -> None:
        """Bring up the entire browser-capture stack.

        Returns only when BOTH conditions are met:
          1. Extension reports OFFSCREEN_READY_TO_RECORD (capture truly started)
          2. ingest.wait_for_hello() succeeds (WS connected, hello validated)

        Either failure mode raises before frames start being consumed.
        """
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self._spawn_time = asyncio.get_event_loop().time()

        # 1. Bind the localhost ingest socket BEFORE the extension can connect.
        await self.ingest.start()

        # 2. Discover extension and open control surface.
        ext_id = await self._find_extension_id()
        self.logger.info(
            f"BrowserCaptureRecorder: extension {ext_id}, "
            f"ingest port {self.ingest.port}"
        )
        self._control_page = await self.page.context.new_page()
        await self._control_page.goto(f"chrome-extension://{ext_id}/control.html")

        # 3. Trigger the extension's command listener with an OS-level
        #    keypress while the meeting tab has focus. This satisfies
        #    Chrome MV3 tabCapture's user-invocation requirement; without
        #    it, the next call would fail with "Extension has not been
        #    invoked for the current page".
        try:
            await self._invoke_extension()
        except Exception as e:
            await self.ingest.stop()
            raise RuntimeError(f"extension invocation failed: {e}")

        # 4. Drive startCapture. Tab discovery is by EXACT URL match —
        #    we pass `self.page.url` (Playwright's view of the live tab),
        #    background filters tabs by normalized origin+pathname.
        target_url = self.page.url
        if not target_url:
            await self.ingest.stop()
            raise RuntimeError("BrowserCaptureRecorder: page URL is empty")

        # 45s budget for the full extension chain: control.startCapture →
        # background.FIND_MEETING_TAB + GET_STREAM_ID → ensureOffscreen +
        # OFFSCREEN_START → getUserMedia + AudioContext + AudioWorklet +
        # WebSocket open + hello → OFFSCREEN_READY_TO_RECORD → unblock
        # control's sendMessage. On a loaded persistent context (Meet's
        # heavy UI right after admission, extension cold start) the IPC
        # round-trip can take 15-30s; 15s timed out at 06:23:44 UTC even
        # though capture itself was working (hello arrived just before
        # the cutoff). 45s buys margin without slowing genuine failures
        # — the dedicated TimeoutError branch below tells us exactly
        # which barrier was missed when it does fire.
        try:
            ext_result = await asyncio.wait_for(
                self._control_page.evaluate(
                    "async (args) => await window.startCapture(args)",
                    {"targetUrl": target_url, "wsPort": self.ingest.port},
                ),
                timeout=45.0,
            )
        except asyncio.TimeoutError:
            diag = (
                f"ws_connected={self.ingest.client_connected}, "
                f"hello_validated={self.ingest.hello_validated.is_set()}, "
                f"frames_received={self.ingest.frames_received}, "
                f"ext_result=<not returned>"
            )
            await self.ingest.stop()
            raise RuntimeError(
                f"browser capture start timed out after 45s ({diag})"
            )
        except Exception as e:
            await self.ingest.stop()
            raise RuntimeError(
                f"browser capture failed to start: {type(e).__name__}: {e}"
            )

        # 4. Independent confirmation on the ingest side.
        try:
            await self.ingest.wait_for_hello(timeout=5.0)
        except Exception as e:
            try:
                await self._control_page.evaluate("async () => await window.stopCapture()")
            except Exception:
                pass
            await self.ingest.stop()
            raise RuntimeError(f"browser capture hello not received: {e}")

        self.logger.info(
            f"BrowserCaptureRecorder: ready  "
            f"ext_info={ext_result.get('info')}  "
            f"ingest_meta={self.ingest.meta}"
        )

        # Capture is running — return focus to the meeting tab so Meet's
        # UI stays foreground-priority and subsequent UI ops (caption
        # toggle, participant panel) run at full speed. Without this the
        # control page can remain the active tab and Meet's UI gets
        # background-tab treatment, causing those ops to take 30-90s
        # instead of ~5s.
        try:
            await self.page.bring_to_front()
        except Exception as e:
            self.logger.warning(f"could not refocus meeting page: {e}")

        self._consumer_task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """Drain frames from the ingest queue, slice into chunk-sized blocks."""
        # 30s of stereo audio = 30 * 44100 frames per channel.
        # Source rate is 44.1 kHz (matches Meet's WebRTC track to avoid
        # Chrome's runtime resampling). The merge step in storage.py
        # resamples once to 48 kHz s16le for the final merged.wav.
        target_samples_per_chan = settings.CHUNK_DURATION_SECONDS * 44100
        # Each "interleaved sample" in our buffer counts a single Float32
        # value, regardless of channel. Two channels means we need
        # 2 * target_samples_per_chan values for one chunk.
        target_values = target_samples_per_chan * 2

        buf: list[np.ndarray] = []
        values_held: int = 0
        try:
            async for frame in self.ingest.frames():
                arr = np.frombuffer(frame, dtype=np.float32)
                buf.append(arr)
                values_held += arr.size
                while values_held >= target_values:
                    full = np.concatenate(buf)
                    take = full[:target_values]
                    rest = full[target_values:]
                    buf = [rest] if rest.size else []
                    values_held = rest.size
                    await self._write_chunk(take)
            # Drain any trailing partial chunk after the WS closes.
            if values_held > 0:
                await self._write_chunk(np.concatenate(buf))
        except asyncio.CancelledError:
            self.logger.info("BrowserCaptureRecorder consumer cancelled")
            raise
        except Exception as e:
            self.logger.error(f"BrowserCaptureRecorder consumer error: {e}")

    async def _write_chunk(self, interleaved_f32: np.ndarray) -> None:
        """Write a chunk as IEEE float32 WAV.

        Mirrors AudioRecorder's pcm_f32le format (commit 042a225) so the
        downstream merge is the single dithered float→s16 conversion point.
        """
        path = self.chunk_dir / f"chunk_{self._chunk_index:04d}.wav"
        write_wav_f32le(
            path,
            interleaved_f32.astype(np.float32, copy=False).tobytes(),
            sample_rate=44100,
            channels=2,
        )
        rms_db = chunk_rms_dbfs(path)
        if not self._first_audio_logged and rms_db > SILENCE_RMS_DBFS:
            self._first_audio_logged = True
            elapsed = (
                asyncio.get_event_loop().time() - self._spawn_time
                if self._spawn_time is not None
                else float("nan")
            )
            self.logger.info(
                f"First non-silent audio detected at chunk {self._chunk_index} "
                f"({path.name}, RMS={rms_db:.1f} dBFS, "
                f"{elapsed:.1f}s after spawn)"
            )
        elif not self._first_audio_logged:
            self.logger.info(
                f"Chunk {self._chunk_index} ({path.name}) appears silent: "
                f"RMS={rms_db:.1f} dBFS"
            )
        self.logger.info(
            f"Chunk ready: {path.name} (index: {self._chunk_index})"
        )

        chunk_index = self._chunk_index
        self._chunk_index += 1
        if self.on_chunk_ready:
            try:
                await self.on_chunk_ready(path, chunk_index)
            except Exception as e:
                self.logger.error(f"chunk callback: {e}")

    async def stop(self) -> int:
        """Symmetric shutdown. No timed sleeps — relies on extension EOF +
        ingest queue drain to terminate naturally."""
        if self._stopped:
            return self._chunk_index
        self._stopped = True
        self.logger.info("BrowserCaptureRecorder stopping...")

        # 1. Drive stopCapture and wait for the extension to confirm.
        #    background withholds sendResponse until OFFSCREEN_STOPPED arrives.
        try:
            if self._control_page:
                stop_result = await asyncio.wait_for(
                    self._control_page.evaluate(
                        "async () => await window.stopCapture()"
                    ),
                    timeout=10.0,
                )
                self.logger.info(f"stopCapture confirmed: {stop_result}")
        except Exception as e:
            self.logger.warning(f"stopCapture timed out / failed: {e}")

        # 2. Consumer drains naturally — frames() returns once the WS closes
        #    AND the queue is empty. The EOF marker + clean WS close from
        #    offscreen guarantees this terminates without a timer.
        if self._consumer_task is not None:
            try:
                await asyncio.wait_for(self._consumer_task, timeout=10.0)
            except asyncio.TimeoutError:
                self.logger.warning(
                    "BrowserCaptureRecorder consumer did not drain in 10s; cancelling"
                )
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except (asyncio.CancelledError, Exception):
                    pass

        await self.ingest.stop()
        if self._control_page is not None:
            try:
                await self._control_page.close()
            except Exception:
                pass
            self._control_page = None

        # Final ingest health summary. Drops mean the recording is short
        # of audio relative to what the meeting contained — log loudly so
        # the lifecycle (and humans triaging) cannot miss it.
        if self.ingest.frames_dropped > 0:
            self.logger.error(
                f"BrowserCaptureRecorder UNHEALTHY: dropped "
                f"{self.ingest.frames_dropped} frame(s) of "
                f"{self.ingest.frames_received + self.ingest.frames_dropped} "
                f"total — recording is missing audio under backpressure"
            )
        else:
            self.logger.info(
                f"BrowserCaptureRecorder healthy: received "
                f"{self.ingest.frames_received} frames, 0 dropped"
            )

        self.logger.info(
            f"BrowserCaptureRecorder stopped. Total chunks: {self._chunk_index}"
        )
        return self._chunk_index
