"""
Localhost WebSocket server that receives PCM frames from the BotWorker
extension's offscreen document.

Wire protocol:
  1. First message: JSON {type:"hello", sampleRate, channels, format:"f32"}
     Validated server-side; mismatches set handshake_failed and unblock the
     start barrier so the recorder can fail loudly instead of waiting.
  2. Subsequent messages:
       - bytes  → interleaved Float32 PCM frames, queued for the recorder
       - text   → JSON control messages. Currently only {type:"eof"} which
                  signals the offscreen has drained its buffer and closed.
  3. WebSocket close terminates the session.

Single-client server: any second connection is rejected with a 1008 close.
There is exactly one BrowserCaptureRecorder per BotWorker process.

Backpressure: the queue is sized large enough (4096 batches ≈ 87s at the
worklet's ~47 batches/sec rate) that transient consumer hiccups absorb
without any drops. If the queue ever fills, that's a sustained stall and
we count it as a hard fault: drops are tracked in `frames_dropped`, logged
at ERROR level, and reported in the recorder's stop() summary. We do not
silently degrade the recording.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Optional

import websockets


class BrowserAudioIngest:
    """Single-client localhost WebSocket server for tab audio frames."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._server: Optional[Any] = None
        self._port: int = 0
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
        self._meta: dict = {}
        self._client_connected = asyncio.Event()
        self.hello_validated = asyncio.Event()
        self.handshake_failed: Optional[Exception] = None
        self._client_done = asyncio.Event()
        # Backpressure observability: silently dropping audio is a worse
        # failure than a noisy one. Track every dropped frame and let the
        # recorder log/report at end-of-recording so the lifecycle can mark
        # the recording unhealthy instead of producing missing-audio output
        # under no apparent failure.
        self.frames_received: int = 0
        self.frames_dropped: int = 0
        self._last_drop_log_at: float = 0.0

    @property
    def port(self) -> int:
        return self._port

    @property
    def meta(self) -> dict:
        return dict(self._meta)

    @property
    def healthy(self) -> bool:
        """False if any frames were dropped or the handshake failed.

        Lifecycle should treat this as a recording-quality signal — a healthy
        recorder produced every frame the extension sent; an unhealthy one
        had backpressure-induced drops or a handshake error and the audio
        on disk is missing samples relative to what the meeting actually
        contained.
        """
        return self.frames_dropped == 0 and self.handshake_failed is None

    @property
    def client_connected(self) -> bool:
        """True if the WebSocket client (offscreen document) has connected.
        Set BEFORE the hello message is parsed, so a True here with
        hello_validated=False indicates the WS opened but the JSON hello
        was not received or did not validate."""
        return self._client_connected.is_set()

    async def start(self) -> None:
        """Bind the server to a random localhost port and start serving."""
        self._server = await websockets.serve(
            self._handle, "127.0.0.1", 0, max_size=2**22  # 4 MiB per message
        )
        # websockets v12+: server.sockets is a list of socket.socket
        self._port = self._server.sockets[0].getsockname()[1]
        self.logger.info(
            f"BrowserAudioIngest listening on 127.0.0.1:{self._port}"
        )

    async def _handle(self, ws) -> None:
        """One-shot single-client handler. Subsequent connections are dropped."""
        if self._client_connected.is_set():
            self.logger.warning(
                "BrowserAudioIngest: rejecting duplicate client connection"
            )
            await ws.close(code=1008, reason="single-client")
            return
        self._client_connected.set()

        try:
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(hello_raw, bytes):
                hello_raw = hello_raw.decode("utf-8", errors="replace")
            meta = json.loads(hello_raw)
            if meta.get("type") != "hello":
                raise ValueError(f"first message must be hello, got {meta!r}")
            # AudioContext now runs at 44.1 kHz to match Meet's WebRTC
            # source and avoid Chrome's runtime sample-rate conversion
            # (suspected cause of audible dropouts under load).
            if meta.get("sampleRate") != 44100:
                raise ValueError(f"unexpected sampleRate: {meta!r}")
            if meta.get("channels") != 2:
                raise ValueError(f"unexpected channels: {meta!r}")
            if meta.get("format") != "f32":
                raise ValueError(f"unexpected format: {meta!r}")
            self._meta = meta
            self.hello_validated.set()
            self.logger.info(f"BrowserAudioIngest hello: {meta}")

            async for msg in ws:
                if isinstance(msg, bytes):
                    try:
                        self._queue.put_nowait(msg)
                        self.frames_received += 1
                    except asyncio.QueueFull:
                        # Sustained consumer stall — large queue means
                        # this represents tens of seconds of buffered
                        # audio and we're now unable to absorb more.
                        # Track the drop and log at ERROR (rate-limited
                        # to avoid spamming if it persists). Final tally
                        # is surfaced via frames_dropped + healthy
                        # property, which the recorder reports in stop().
                        self.frames_dropped += 1
                        now = time.monotonic()
                        if now - self._last_drop_log_at >= 1.0:
                            self.logger.error(
                                f"BrowserAudioIngest queue full, dropping "
                                f"frame (total dropped={self.frames_dropped}, "
                                f"received={self.frames_received})"
                            )
                            self._last_drop_log_at = now
                else:
                    try:
                        ctrl = json.loads(msg)
                    except Exception:
                        self.logger.warning(
                            f"BrowserAudioIngest unexpected text frame: {msg!r}"
                        )
                        continue
                    if ctrl.get("type") == "eof":
                        self.logger.info(
                            f"BrowserAudioIngest eof received "
                            f"(client batchCount={ctrl.get('batchCount')})"
                        )
                        break
        except Exception as e:
            self.handshake_failed = e
            # Unblock anyone waiting on the start barrier even on failure.
            self.hello_validated.set()
            self.logger.warning(f"BrowserAudioIngest handler error: {e}")
        finally:
            self._client_done.set()

    async def wait_for_hello(self, timeout: float) -> None:
        """Block until the client has connected AND the hello has been validated.

        Raises if the handshake failed or the timeout elapses.
        """
        try:
            await asyncio.wait_for(self.hello_validated.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"BrowserAudioIngest: no client hello within {timeout}s "
                f"(client_connected={self._client_connected.is_set()})"
            )
        if self.handshake_failed:
            raise self.handshake_failed

    async def frames(self) -> AsyncGenerator[bytes, None]:
        """Async generator over binary Float32 frames.

        Returns when the client signals EOF (or the WS closes) AND the
        in-memory queue is fully drained, so callers can iterate past stop()
        without losing the last partial chunk.
        """
        while True:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield frame
            except asyncio.TimeoutError:
                if self._client_done.is_set() and self._queue.empty():
                    return

    async def stop(self) -> None:
        """Close the server. Safe to call multiple times."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
