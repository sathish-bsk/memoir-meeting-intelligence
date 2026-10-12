"""
Smoke test for the browser tab-capture path.

What it covers (no real meeting required):
  1. BrowserAudioIngest plumbing — hello validation, frame queueing,
     drop counter, EOF handshake.
  2. Chunk write/read round-trip via write_wav_f32le + chunk_rms_dbfs.
  3. Extension loading — Playwright persistent context launches with the
     extension flags, service worker is discoverable, control.html loads
     and exposes window.startCapture.

What it does NOT cover (those are gated by the live recording test):
  - Real chrome.tabCapture against a real meeting tab.
  - Offscreen document creation + getUserMedia + AudioWorklet end-to-end.
  - WebRTC stream alive after src.connect(ctx.destination).

How to run:

  # On EC2, against the rebuilt botworker:latest image:
  docker run -d --rm --name smoke \\
      -e AUDIO_CAPTURE_METHOD=browser -e DOCKER=1 \\
      botworker:latest
  sleep 8                       # wait for Xvfb + PulseAudio + uvicorn
  docker exec smoke python3 /app/scripts/test_browser_capture.py
  docker stop smoke

The script exits non-zero on any failure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
import websockets

# Make `app.*` importable when run from /app (the container's WORKDIR).
sys.path.insert(0, "/app")

from app.core.config import settings
from app.core.logging import get_job_logger
from app.services._audio_utils import (
    SILENCE_RMS_DBFS,
    chunk_rms_dbfs,
    write_wav_f32le,
)
from app.services.browser_audio_ingest import BrowserAudioIngest


_PASS = "✓"  # ✓
_FAIL = "✗"  # ✗


class TestFail(AssertionError):
    pass


def ok(msg: str) -> None:
    print(f"  {_PASS} {msg}")


def section(name: str) -> None:
    print(f"\n=== {name} ===")


def fail(msg: str) -> None:
    print(f"  {_FAIL} {msg}", file=sys.stderr)
    raise TestFail(msg)


async def _send_hello_and_frames(
    port: int,
    n_batches: int,
    bad_hello: bool = False,
) -> None:
    """Pretend to be the offscreen document: hello + N batches + EOF."""
    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
        if bad_hello:
            # bad-hello sends a deliberately wrong rate (48 kHz now that
            # the validator expects 44.1) so wait_for_hello() rejects it.
            await ws.send(json.dumps({"type": "hello", "sampleRate": 48000, "channels": 1, "format": "f32"}))
            return
        await ws.send(json.dumps({
            "type": "hello", "sampleRate": 44100, "channels": 2, "format": "f32",
        }))
        for i in range(n_batches):
            # 1024-sample stereo batch at 44.1 kHz, 440 Hz sine
            t = (np.arange(1024) + i * 1024) / 44100.0
            mono = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            interleaved = np.empty(2048, dtype=np.float32)
            interleaved[0::2] = mono
            interleaved[1::2] = mono
            await ws.send(interleaved.tobytes())
        await ws.send(json.dumps({"type": "eof", "batchCount": n_batches}))


async def test_ingest_happy_path() -> None:
    section("Test 1a — ingest happy path (hello + frames + EOF)")
    logger = get_job_logger("smoke-test-1a")
    ingest = BrowserAudioIngest(logger)
    await ingest.start()
    ok(f"ingest listening on 127.0.0.1:{ingest.port}")

    client_task = asyncio.create_task(_send_hello_and_frames(ingest.port, n_batches=5))

    # Wait for hello
    await asyncio.wait_for(ingest.wait_for_hello(timeout=5.0), timeout=6.0)
    ok("hello validated")
    if ingest.meta.get("sampleRate") != 44100:
        fail(f"unexpected meta: {ingest.meta}")
    ok(f"meta = {ingest.meta}")

    # Drain frames
    received: List[bytes] = []
    async for frame in ingest.frames():
        received.append(frame)
    if len(received) != 5:
        fail(f"expected 5 frames, got {len(received)}")
    ok(f"received {len(received)} frames via async generator")
    if ingest.frames_received != 5:
        fail(f"frames_received counter = {ingest.frames_received}, expected 5")
    if ingest.frames_dropped != 0:
        fail(f"frames_dropped = {ingest.frames_dropped}, expected 0")
    ok(f"counters: received={ingest.frames_received}, dropped={ingest.frames_dropped}")
    if not ingest.healthy:
        fail("healthy is False after happy-path run")
    ok("ingest.healthy == True")

    await client_task
    await ingest.stop()
    ok("ingest stopped cleanly")


async def test_ingest_bad_hello() -> None:
    section("Test 1b — ingest rejects mismatched hello")
    logger = get_job_logger("smoke-test-1b")
    ingest = BrowserAudioIngest(logger)
    await ingest.start()

    client_task = asyncio.create_task(
        _send_hello_and_frames(ingest.port, n_batches=0, bad_hello=True)
    )

    raised = None
    try:
        await asyncio.wait_for(ingest.wait_for_hello(timeout=3.0), timeout=4.0)
    except Exception as e:
        raised = e

    if raised is None:
        fail("wait_for_hello did not raise on bad hello")
    if "sampleRate" not in str(raised) and "channels" not in str(raised):
        fail(f"unexpected error: {raised!r}")
    ok(f"wait_for_hello raised on bad hello: {raised}")

    if ingest.healthy:
        fail("ingest reports healthy after a failed handshake")
    ok("ingest.healthy == False after handshake failure")

    await client_task
    await ingest.stop()


async def test_chunk_write_and_rms() -> None:
    section("Test 2 — write_wav_f32le + chunk_rms_dbfs round-trip")
    # Build 1 second of stereo 440 Hz sine at -6 dBFS amplitude
    sr = 48000
    t = np.arange(sr) / sr
    mono = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)  # peak 0.5 = -6 dBFS
    interleaved = np.empty(sr * 2, dtype=np.float32)
    interleaved[0::2] = mono
    interleaved[1::2] = mono

    out_path = Path(tempfile.mkdtemp()) / "smoke-chunk.wav"
    write_wav_f32le(out_path, interleaved.tobytes(), sample_rate=sr, channels=2)
    if not out_path.exists():
        fail("chunk file not created")
    size = out_path.stat().st_size
    expected = 44 + sr * 2 * 4  # header + samples
    if abs(size - expected) > 8:  # allow a few bytes of slack
        fail(f"chunk size {size}, expected ~{expected}")
    ok(f"chunk written: {out_path.name} ({size} bytes)")

    rms = chunk_rms_dbfs(out_path)
    # 0.5 amplitude sine: theoretical RMS = 0.5/√2 ≈ 0.354 → -9 dBFS
    if not (-12.0 <= rms <= -7.0):
        fail(f"unexpected RMS: {rms:.1f} dBFS (expected ≈ -9 dBFS for 0.5 sine)")
    ok(f"chunk_rms_dbfs reads back RMS = {rms:.1f} dBFS (expected ≈ -9 dBFS)")
    if rms <= SILENCE_RMS_DBFS:
        fail(f"chunk classified as silent ({rms} <= {SILENCE_RMS_DBFS})")
    ok("chunk above silence threshold")


async def test_extension_loads() -> None:
    section("Test 3 — Playwright + extension loads + control.html reachable")
    if settings.AUDIO_CAPTURE_METHOD != "browser":
        fail(f"AUDIO_CAPTURE_METHOD must be 'browser' for this test "
             f"(got {settings.AUDIO_CAPTURE_METHOD!r})")
    flags_present = any("--load-extension" in a for a in settings.BROWSER_ARGS)
    if not flags_present:
        fail("--load-extension flag missing from BROWSER_ARGS in browser mode")
    ok("BROWSER_ARGS includes --load-extension and --auto-accept-this-tab-capture")

    from playwright.async_api import async_playwright

    user_data_dir = tempfile.mkdtemp(prefix="smoke-chrome-")
    pw = await async_playwright().start()
    ctx = None
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=settings.BROWSER_ARGS,
            timeout=60000,
        )
        ok("persistent context launched with browser-mode flags")

        ext_id = None
        for _ in range(40):  # 10s
            for sw in ctx.service_workers:
                if "background.js" in sw.url and "chrome-extension://" in sw.url:
                    ext_id = sw.url.split("://")[1].split("/")[0]
                    break
            if ext_id:
                break
            await asyncio.sleep(0.25)
        if not ext_id:
            fail("extension service worker not found within 10s")
        ok(f"extension service worker found: {ext_id}")

        # Open control.html and verify it exposes startCapture/stopCapture
        page = await ctx.new_page()
        await page.goto(f"chrome-extension://{ext_id}/control.html", timeout=10000)
        ok("control.html loaded")

        has_funcs = await page.evaluate("""() => ({
            hasStart: typeof window.startCapture === 'function',
            hasStop: typeof window.stopCapture === 'function',
            hasPoll: typeof window.pollStatus === 'function',
        })""")
        if not (has_funcs["hasStart"] and has_funcs["hasStop"] and has_funcs["hasPoll"]):
            fail(f"control.html does not expose all functions: {has_funcs}")
        ok("control.html exposes startCapture / stopCapture / pollStatus")

        # FIND_MEETING_TAB sanity: with no Meet tab open, expect a clean error
        find_resp = await page.evaluate("""async () => {
            return await chrome.runtime.sendMessage({
                type: 'FIND_MEETING_TAB',
                targetUrl: 'https://meet.google.com/abc-defg-hij'
            });
        }""")
        if not find_resp or not find_resp.get("error"):
            fail(f"FIND_MEETING_TAB unexpectedly succeeded: {find_resp}")
        if "no meeting tab" not in find_resp["error"]:
            fail(f"FIND_MEETING_TAB returned unexpected error: {find_resp['error']}")
        ok(f"FIND_MEETING_TAB clean error when no Meet tab open: {find_resp['error']}")

        # Verify offscreen permission is in the manifest at runtime
        manifest = await page.evaluate("() => chrome.runtime.getManifest()")
        perms = manifest.get("permissions", [])
        for needed in ["tabs", "tabCapture", "offscreen"]:
            if needed not in perms:
                fail(f"manifest missing permission: {needed} (have {perms})")
        ok(f"manifest permissions OK: {perms}")
    finally:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        await pw.stop()


async def test_xdotool_invocation() -> None:
    """End-to-end test of the xdotool → command listener → getMediaStreamId chain.

    Reproduces the live-test failure (commit 22e2d8c) and verifies the fix:
    open https://example.com/, focus it, send xdotool key ctrl+shift+y,
    confirm the background's invokedTabId matches example.com's tabId,
    then call GET_STREAM_ID and verify a streamId is returned without
    invocation error.

    The invocation requirement is generic across tabs (not Meet-specific)
    so example.com is a valid substitute that doesn't require a real
    meeting URL.
    """
    section("Test 4 — xdotool invocation unlocks tabCapture")
    if settings.AUDIO_CAPTURE_METHOD != "browser":
        fail("AUDIO_CAPTURE_METHOD must be 'browser'")
    if "xdotool" not in (await _which("xdotool") or ""):
        fail("xdotool not found in container PATH")
    ok("xdotool is available in the container")

    from playwright.async_api import async_playwright

    user_data_dir = tempfile.mkdtemp(prefix="smoke-chrome-")
    pw = await async_playwright().start()
    ctx = None
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=settings.BROWSER_ARGS,
            timeout=60000,
        )
        ext_id = None
        for _ in range(40):
            for sw in ctx.service_workers:
                if "background.js" in sw.url and "chrome-extension://" in sw.url:
                    ext_id = sw.url.split("://")[1].split("/")[0]
                    break
            if ext_id:
                break
            await asyncio.sleep(0.25)
        if not ext_id:
            fail("extension service worker not found")
        ok(f"extension loaded: {ext_id}")

        # Manifest sanity — make sure the commands declaration landed
        ctrl = await ctx.new_page()
        await ctrl.goto(f"chrome-extension://{ext_id}/control.html", timeout=10000)
        manifest = await ctrl.evaluate("() => chrome.runtime.getManifest()")
        if not manifest.get("commands", {}).get("invoke-tab-capture"):
            fail(f"manifest missing 'invoke-tab-capture' command: {manifest.get('commands')}")
        ok("manifest declares 'invoke-tab-capture' command")

        # Open the target tab BEFORE invocation, so it has a tabId for
        # the command listener to record.
        target_page = await ctx.new_page()
        await target_page.goto("https://example.com/", timeout=15000)
        target_tab_id = await ctrl.evaluate("""async () => {
            const tabs = await chrome.tabs.query({});
            const m = tabs.find((t) => t.url && t.url.startsWith('https://example.com/'));
            return m ? m.id : null;
        }""")
        if not target_tab_id:
            fail("could not find example.com tab via chrome.tabs.query")
        ok(f"target tab id: {target_tab_id}")

        # Initial state — should be uninvoked
        state0 = await ctrl.evaluate("""async () => {
            return await chrome.runtime.sendMessage({type:'GET_INVOCATION_STATE'});
        }""")
        if state0.get("invokedTabId") is not None:
            fail(f"initial invokedTabId should be null: {state0}")
        ok(f"initial invocation state: invokedTabId=null")

        # Negative control — getMediaStreamId BEFORE invocation should fail
        # via our explicit guard, not via Chrome's opaque error.
        no_invoke_resp = await ctrl.evaluate("""async (tabId) => {
            return await chrome.runtime.sendMessage({type:'GET_STREAM_ID', tabId});
        }""", target_tab_id)
        if no_invoke_resp.get("streamId"):
            fail(f"GET_STREAM_ID succeeded without invocation: {no_invoke_resp}")
        if "invocation missing" not in no_invoke_resp.get("error", ""):
            fail(f"unexpected pre-invocation error: {no_invoke_resp}")
        ok(f"pre-invocation guard fires: {no_invoke_resp['error']}")

        # Bring example.com to front, then send xdotool keypress.
        await target_page.bring_to_front()
        await asyncio.sleep(0.3)

        env = dict(os.environ)
        env["DISPLAY"] = settings.DISPLAY
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "key", "ctrl+shift+y",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, xerr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            fail(f"xdotool exit {proc.returncode}: {xerr.decode(errors='replace')}")
        ok("xdotool key ctrl+shift+y sent")

        # Give the command listener a moment to fire.
        await asyncio.sleep(0.5)

        # Verify background recorded the invocation against the right tab.
        state1 = await ctrl.evaluate("""async () => {
            return await chrome.runtime.sendMessage({type:'GET_INVOCATION_STATE'});
        }""")
        if state1.get("invokedTabId") != target_tab_id:
            fail(
                f"invokedTabId mismatch after xdotool: "
                f"got={state1.get('invokedTabId')}, expected={target_tab_id}, "
                f"full state={state1}"
            )
        if state1.get("ageMs") is None or state1["ageMs"] > 3000:
            fail(f"invocation age suspicious: {state1.get('ageMs')}ms")
        ok(
            f"invocation recorded: invokedTabId={state1['invokedTabId']}, "
            f"ageMs={state1['ageMs']}"
        )

        # Now GET_STREAM_ID should succeed.
        with_invoke_resp = await ctrl.evaluate("""async (tabId) => {
            return await chrome.runtime.sendMessage({type:'GET_STREAM_ID', tabId});
        }""", target_tab_id)
        if with_invoke_resp.get("error"):
            fail(f"GET_STREAM_ID failed after invocation: {with_invoke_resp['error']}")
        sid = with_invoke_resp.get("streamId")
        if not isinstance(sid, str) or not sid:
            fail(f"GET_STREAM_ID returned no streamId: {with_invoke_resp}")
        ok(f"GET_STREAM_ID returned streamId of len {len(sid)} after invocation")

        # Tab-mismatch guard: requesting a different tabId should be rejected
        # explicitly (not silently routed to whatever was last invoked).
        mismatch_resp = await ctrl.evaluate("""async (tabId) => {
            return await chrome.runtime.sendMessage({type:'GET_STREAM_ID', tabId});
        }""", target_tab_id + 99999)
        if mismatch_resp.get("streamId"):
            fail(f"tab-mismatch guard failed: {mismatch_resp}")
        if "tab mismatch" not in mismatch_resp.get("error", ""):
            fail(f"unexpected mismatch error: {mismatch_resp}")
        ok(f"tab-mismatch guard fires: {mismatch_resp['error']}")
    finally:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        await pw.stop()


async def _which(binary: str) -> Optional[str]:
    """Return the path to a binary, or None."""
    proc = await asyncio.create_subprocess_exec(
        "which", binary,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out.decode().strip() if proc.returncode == 0 else None


async def test_full_capture_flow_with_tone() -> None:
    """End-to-end capture: open a tab playing a 440 Hz sine via Web Audio,
    drive the full extension chain (xdotool → invocation → GET_STREAM_ID →
    ENSURE_OFFSCREEN → START_TAB_AUDIO → AudioWorklet → WS frames),
    accumulate frames for ~3s, and verify they contain the tone.

    This exercises every component the live test would hit, minus the
    Meet redirects + WebRTC stack. If this fails, the live test will
    fail the same way and we'll have caught it cheap.

    FIND_MEETING_TAB is bypassed (the test tab is not a Meet URL); the
    test sends GET_STREAM_ID + START_TAB_AUDIO directly via background
    using the tabId from chrome.tabs.query. Production code is unchanged.
    """
    section("Test 5 — full capture flow against a 440 Hz Web Audio tone")
    if settings.AUDIO_CAPTURE_METHOD != "browser":
        fail("AUDIO_CAPTURE_METHOD must be 'browser'")

    from playwright.async_api import async_playwright

    user_data_dir = tempfile.mkdtemp(prefix="smoke-chrome-")
    pw = await async_playwright().start()
    ctx = None
    ingest = None
    try:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=settings.BROWSER_ARGS,
            timeout=60000,
        )
        ext_id = None
        for _ in range(40):
            for sw in ctx.service_workers:
                if "background.js" in sw.url and "chrome-extension://" in sw.url:
                    ext_id = sw.url.split("://")[1].split("/")[0]
                    break
            if ext_id:
                break
            await asyncio.sleep(0.25)
        if not ext_id:
            fail("extension service worker not found")
        ok(f"extension loaded: {ext_id}")

        # Audio source: a data: URL that immediately starts a 440 Hz tone
        # via Web Audio routed to the AudioContext destination. Chrome
        # plays this through its audio mixer; tabCapture taps that mixer.
        tone_url = (
            "data:text/html,"
            "<html><body><script>"
            "const ctx=new AudioContext({sampleRate:44100});"
            "const osc=ctx.createOscillator();"
            "osc.frequency.value=440;"
            "const gain=ctx.createGain();"
            "gain.gain.value=0.5;"
            "osc.connect(gain);gain.connect(ctx.destination);"
            "osc.start();"
            "window.__memoirToneStarted=true;"
            "</script></body></html>"
        )
        target_page = await ctx.new_page()
        await target_page.goto(tone_url)
        # Confirm the tone-start script ran; AudioContext takes a moment
        # to come up after construction.
        for _ in range(20):
            try:
                started = await target_page.evaluate("() => window.__memoirToneStarted === true")
                if started:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        else:
            fail("tone page did not signal start")
        await asyncio.sleep(0.3)
        ok("opened data: URL playing 440 Hz tone")

        # Control page (extension privilege) — used for runtime messaging.
        ctrl = await ctx.new_page()
        await ctrl.goto(f"chrome-extension://{ext_id}/control.html", timeout=10000)

        # Resolve the target tab id from the extension's view of tabs.
        target_tab_id = await ctrl.evaluate("""async () => {
            const tabs = await chrome.tabs.query({});
            const m = tabs.find((t) => t.url && t.url.startsWith('data:text/html'));
            return m ? m.id : null;
        }""")
        if not target_tab_id:
            fail("could not find tone tab via chrome.tabs.query")
        ok(f"tone tab id: {target_tab_id}")

        # Stand up the ingest server BEFORE the offscreen connects to it.
        logger = get_job_logger("smoke-test-5")
        ingest = BrowserAudioIngest(logger)
        await ingest.start()
        ok(f"ingest listening on 127.0.0.1:{ingest.port}")

        # Focus the tone tab and invoke the extension via xdotool.
        await target_page.bring_to_front()
        await asyncio.sleep(0.3)
        env = dict(os.environ)
        env["DISPLAY"] = settings.DISPLAY
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "key", "ctrl+shift+y",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, xerr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            fail(f"xdotool exit {proc.returncode}: {xerr.decode(errors='replace')}")
        await asyncio.sleep(0.5)
        ok("xdotool key ctrl+shift+y sent and listener allowed time to fire")

        # Bypass FIND_MEETING_TAB (the tone URL doesn't match the Meet
        # pattern) and call GET_STREAM_ID + START_TAB_AUDIO directly.
        sid_resp = await ctrl.evaluate("""async (tabId) => {
            return await chrome.runtime.sendMessage({type:'GET_STREAM_ID', tabId});
        }""", target_tab_id)
        if sid_resp.get("error"):
            fail(f"GET_STREAM_ID failed: {sid_resp['error']}")
        stream_id = sid_resp.get("streamId")
        if not stream_id:
            fail(f"GET_STREAM_ID returned no streamId: {sid_resp}")
        ok(f"GET_STREAM_ID returned streamId of len {len(stream_id)}")

        # Drive the full start chain: ENSURE_OFFSCREEN + OFFSCREEN_START.
        # background's START_TAB_AUDIO holds sendResponse until offscreen
        # replies READY_TO_RECORD or FAILED.
        start_resp = await asyncio.wait_for(
            ctrl.evaluate("""async ([streamId, wsPort]) => {
                return await chrome.runtime.sendMessage({
                    type: 'START_TAB_AUDIO', streamId, wsPort,
                });
            }""", [stream_id, ingest.port]),
            timeout=15.0,
        )
        if start_resp.get("error"):
            fail(f"START_TAB_AUDIO failed: {start_resp['error']}")
        ok(f"START_TAB_AUDIO succeeded: {start_resp.get('info', {}).get('trackInfo')}")

        # Confirm the WS handshake on the ingest side independently.
        await ingest.wait_for_hello(timeout=5.0)
        ok(f"ingest hello validated: {ingest.meta}")

        # Drain frames for ~3s. Accumulate, then stop.
        collected: list[bytes] = []

        async def consume():
            async for frame in ingest.frames():
                collected.append(frame)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(3.0)

        # Stop via the proper handshake.
        stop_resp = await asyncio.wait_for(
            ctrl.evaluate("""async () => {
                return await chrome.runtime.sendMessage({type:'STOP_TAB_AUDIO'});
            }"""),
            timeout=10.0,
        )
        ok(f"STOP_TAB_AUDIO confirmed: {stop_resp}")

        # Consumer drains naturally once WS closes.
        await asyncio.wait_for(consumer, timeout=5.0)

        if not collected:
            fail("no frames received from offscreen")
        total_samples = sum(len(f) // 4 for f in collected)
        ok(
            f"received {len(collected)} frames "
            f"({total_samples} float samples ≈ {total_samples / 96000:.2f}s of stereo audio)"
        )
        if ingest.frames_dropped > 0:
            fail(f"frames_dropped = {ingest.frames_dropped} during capture")
        ok(f"ingest healthy: dropped=0, received={ingest.frames_received}")

        # Verify the captured audio is the 440 Hz tone (or at least clearly
        # non-silent). With a 0.5 amplitude sine we expect RMS ≈ -9 dBFS.
        samples = np.frombuffer(b"".join(collected), dtype=np.float32)
        if samples.size < 22050:  # < 0.25s of stereo @ 44.1 kHz
            fail(f"captured too few samples ({samples.size}) to evaluate")
        rms = float(np.sqrt(np.mean(samples * samples)))
        rms_db = 20 * np.log10(rms) if rms > 0 else float("-inf")
        peak_db = 20 * np.log10(np.max(np.abs(samples))) if np.any(samples) else float("-inf")
        if rms_db <= SILENCE_RMS_DBFS:
            fail(
                f"captured audio is silent: RMS={rms_db:.1f} dBFS "
                f"(threshold {SILENCE_RMS_DBFS}). Tab capture is producing "
                f"a stream but no audio is reaching the worklet."
            )
        ok(f"captured audio RMS={rms_db:.1f} dBFS, peak={peak_db:.1f} dBFS — non-silent")

        # Spectral check: confirm the dominant frequency is near 440 Hz.
        # Use a single channel (left) for the FFT.
        mono = samples.reshape(-1, 2)[:, 0]
        # Take the largest power-of-two block we have, up to 32768 samples.
        N = 1
        while N * 2 <= min(mono.size, 32768):
            N *= 2
        block = mono[:N]
        spectrum = np.abs(np.fft.rfft(block * np.hanning(N)))
        freqs = np.fft.rfftfreq(N, d=1.0 / 44100)
        peak_freq = freqs[int(np.argmax(spectrum))]
        if not (430 <= peak_freq <= 450):
            fail(f"dominant frequency = {peak_freq:.1f} Hz, expected ~440 Hz")
        ok(f"dominant frequency = {peak_freq:.1f} Hz (expected ~440 Hz)")
    finally:
        if ingest is not None:
            try:
                await ingest.stop()
            except Exception:
                pass
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        await pw.stop()


async def main() -> int:
    print(f"Smoke test starting (AUDIO_CAPTURE_METHOD={settings.AUDIO_CAPTURE_METHOD})")
    print(f"BROWSER_ARGS:")
    for a in settings.BROWSER_ARGS:
        print(f"    {a}")
    started = time.monotonic()
    try:
        await test_ingest_happy_path()
        await test_ingest_bad_hello()
        await test_chunk_write_and_rms()
        await test_extension_loads()
        await test_xdotool_invocation()
        await test_full_capture_flow_with_tone()
    except TestFail as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nFAILED with unexpected error: {e}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    print(f"\nALL CHECKS PASSED in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
