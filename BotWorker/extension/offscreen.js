// Memoir BotWorker audio capture — offscreen document
//
// Bridges the captured tab MediaStream to a localhost WebSocket
// using AudioWorklet → batched Float32 → binary WS frames.
//
// CRITICAL: src.connect(ctx.destination) keeps the captured stream
// audible at the system mixer, which prevents Chromium from muting the
// tab's WebRTC audio output. Without it, Meet/Teams stop decoding
// incoming audio after a few seconds.
//
// Stop is symmetric: we drain ws.bufferedAmount, send a JSON
// {type:"eof"} marker, then close the socket cleanly. background's
// STOP_TAB_AUDIO does not respond until we send OFFSCREEN_STOPPED.

let stream = null;
let ctx = null;
let ws = null;
let worklet = null;
let batchCount = 0;
let firstBatchAt = 0;

async function startCapture(requestId, streamId, wsPort) {
  console.log("[Memoir Offscreen] startCapture", { wsPort });
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      },
      video: false,
    });
    const trackInfo = stream.getAudioTracks().map((t) => ({
      label: t.label,
      readyState: t.readyState,
      settings: t.getSettings(),
    }));

    // Match Meet's WebRTC source rate (the track reports sampleRate: 44100
    // — see trackInfo logged from BrowserCaptureRecorder.start). Running
    // AudioContext at 48 kHz forces Chrome to resample 44.1 → 48 on every
    // frame, and resampling under headed-Xvfb load was the suspected
    // source of the 50+ audible dropouts in 150s of recording observed
    // 2026-05-07. The merge step in storage.py resamples once to 48 kHz
    // s16le for the final file — that batched conversion is glitch-free.
    ctx = new AudioContext({ sampleRate: 44100 });
    await ctx.audioWorklet.addModule("audio-worklet.js");
    const src = ctx.createMediaStreamSource(stream);
    worklet = new AudioWorkletNode(ctx, "pcm-extractor");
    src.connect(worklet);
    src.connect(ctx.destination); // CRITICAL — keeps WebRTC stream alive

    ws = new WebSocket(`ws://127.0.0.1:${wsPort}`);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", () => resolve(), { once: true });
      ws.addEventListener("error", () => reject(new Error("ws error")), {
        once: true,
      });
    });
    ws.send(
      JSON.stringify({
        type: "hello",
        sampleRate: 44100,
        channels: 2,
        format: "f32",
      })
    );

    // Worklet posts pre-batched (~21 ms = 1024 samples per channel) blocks.
    // We just forward each batch as one binary WS message → ~47 msgs/sec.
    worklet.port.onmessage = (e) => {
      const { left, right } = e.data || {};
      if (!left || !right) return;
      if (batchCount === 0) firstBatchAt = performance.now();
      batchCount++;
      const interleaved = new Float32Array(left.length * 2);
      for (let i = 0; i < left.length; i++) {
        interleaved[2 * i] = left[i];
        interleaved[2 * i + 1] = right[i];
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(interleaved.buffer);
      }
    };

    chrome.runtime.sendMessage({
      type: "OFFSCREEN_READY_TO_RECORD",
      requestId,
      info: { trackInfo, sampleRate: ctx.sampleRate },
    });
  } catch (e) {
    console.error("[Memoir Offscreen] start failed:", e.message || e);
    try {
      await stopCapture();
    } catch (_) {}
    chrome.runtime.sendMessage({
      type: "OFFSCREEN_FAILED",
      requestId,
      error: (e && (e.message || String(e))) || "unknown",
    });
  }
}

async function stopCapture(requestId) {
  // 1. Stop accepting new frames from the worklet immediately.
  if (worklet) {
    worklet.port.onmessage = null;
    try { worklet.disconnect(); } catch (_) {}
  }
  // 2. Drain WebSocket: send EOF marker, wait for bufferedAmount → 0.
  if (ws) {
    try {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "eof", batchCount }));
        const drainStart = performance.now();
        while (ws.bufferedAmount > 0 && performance.now() - drainStart < 5000) {
          await new Promise((r) => setTimeout(r, 10));
        }
      }
      ws.close(1000, "stop");
      const closeStart = performance.now();
      while (ws.readyState !== WebSocket.CLOSED && performance.now() - closeStart < 2000) {
        await new Promise((r) => setTimeout(r, 10));
      }
    } catch (e) {
      console.warn("[Memoir Offscreen] ws drain/close error:", e.message || e);
    }
  }
  // 3. Tear down media after the socket is closed so no late frames are lost.
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctx) {
    try { await ctx.close(); } catch (_) {}
  }
  const finalCount = batchCount;
  stream = ctx = ws = worklet = null;
  if (requestId !== undefined) {
    chrome.runtime.sendMessage({
      type: "OFFSCREEN_STOPPED",
      requestId,
      framesSent: finalCount,
    });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "OFFSCREEN_START") {
    startCapture(msg.requestId, msg.streamId, msg.wsPort);
    return false;
  }
  if (msg.type === "OFFSCREEN_STOP") {
    // Async; reply via OFFSCREEN_STOPPED — background holds STOP_TAB_AUDIO's
    // sendResponse until that arrives.
    stopCapture(msg.requestId);
    return false;
  }
  if (msg.type === "OFFSCREEN_STATUS") {
    sendResponse({
      streamActive: (stream && stream.active) || false,
      ctxState: (ctx && ctx.state) || "none",
      wsState: (ws && ws.readyState) ?? -1,
      batchCount,
      firstBatchAt,
    });
    return true;
  }
});

// Notify background that the script has loaded (separate from
// READY_TO_RECORD which only fires after capture is fully running).
chrome.runtime.sendMessage({ type: "OFFSCREEN_LOADED" });
