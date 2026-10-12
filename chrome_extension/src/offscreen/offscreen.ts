/**
 * Offscreen document — tab audio capture in two upload modes:
 * - separate: independent tab + mic tracks
 * - mixed: one client-side mixed track from tab + mic via Web Audio
 */

const FLUSH_INTERVAL_MS = 30 * 1000; // 30 seconds

let tabRecorder: MediaRecorder | null = null;
let micRecorder: MediaRecorder | null = null;
let mixedRecorder: MediaRecorder | null = null;
let tabStream: MediaStream | null = null;
let micStream: MediaStream | null = null;
let mixedStream: MediaStream | null = null;
let audioContext: AudioContext | null = null;

let tabChunks: Blob[] = [];
let micChunks: Blob[] = [];
let mixedChunks: Blob[] = [];
let allTabChunks: Blob[] = [];
let allMicChunks: Blob[] = [];
let allMixedChunks: Blob[] = [];

let recorderMimeType = "audio/webm;codecs=opus";
let chunkSeq = 0;
let flushTimer: ReturnType<typeof setInterval> | null = null;
let chunkStartTime = 0;
let recordingStartTime = 0;
let isFlushing = false;
let hasMic = false;
let uploadMode: "separate" | "mixed" = "separate";

async function sha256hex(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

/**
 * Create a MediaRecorder that pushes blobs into `chunks` (per-flush) and
 * `allChunks` (full-file fallback buffer).
 */
function createRecorder(stream: MediaStream, chunks: Blob[], allChunks: Blob[]): MediaRecorder {
  const recorder = new MediaRecorder(stream, {
    mimeType: recorderMimeType,
    audioBitsPerSecond: 128000,
  });
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) {
      chunks.push(event.data);
      allChunks.push(event.data);
    }
  };
  return recorder;
}

/**
 * Stop a MediaRecorder and wait for onstop.
 */
async function stopRecorderSafe(recorder: MediaRecorder): Promise<void> {
  if (recorder.state === "inactive") return;
  await new Promise<void>((resolve) => {
    recorder.onstop = () => resolve();
    recorder.stop();
  });
}

async function sendChunk(
  track: "tab" | "mic" | "mixed",
  blob: Blob,
  seq: number,
  startMs: number,
  durationMs: number,
  isFinal: boolean
): Promise<void> {
  const sha256 = await sha256hex(blob);
  const buffer = await blob.arrayBuffer();
  const base64 = arrayBufferToBase64(buffer);

  console.log(
    `[Memoir Offscreen] Sending ${track} seq=${seq} (${isFinal ? "FINAL" : "periodic"}, ${buffer.byteLength} bytes, ${durationMs}ms)`
  );

  try {
    await chrome.runtime.sendMessage({
      type: isFinal ? "TAB_AUDIO_FINAL_CHUNK" : "TAB_AUDIO_CHUNK_READY",
      track,
      seq,
      base64Audio: base64,
      startMs,
      durationMs,
      sha256,
      size: buffer.byteLength,
    });
  } catch (err) {
    console.error(`[Memoir Offscreen] ${track} seq=${seq} sendMessage FAILED:`, err);
  }
}

/**
 * Flush current buffered chunks according to the active upload mode.
 */
async function flushChunks(isFinal: boolean): Promise<void> {
  if (isFlushing) return;
  isFlushing = true;

  try {
    if (uploadMode === "mixed") {
      if (mixedRecorder && mixedRecorder.state === "recording") {
        mixedRecorder.requestData();
      }
      await new Promise((r) => setTimeout(r, 200));
      if (isFinal && mixedRecorder && mixedRecorder.state !== "inactive") {
        await stopRecorderSafe(mixedRecorder);
        await new Promise((r) => setTimeout(r, 100));
      }
    } else {
      // Stop tab recorder (standalone WebM per flush)
      if (tabRecorder && tabRecorder.state !== "inactive") {
        await stopRecorderSafe(tabRecorder);
      }

      // Mic recorder: always requestData() first to force buffered audio out.
      if (micRecorder && micRecorder.state === "recording") {
        micRecorder.requestData();
      }

      // Let pending ondataavailable events settle (requestData + tab stop)
      await new Promise((r) => setTimeout(r, 200));

      // Stop mic recorder if final
      if (isFinal && micRecorder && micRecorder.state !== "inactive") {
        await stopRecorderSafe(micRecorder);
        await new Promise((r) => setTimeout(r, 100));
      }
    }

    const seq = chunkSeq++;
    const startMs = chunkStartTime;
    const durationMs = Date.now() - recordingStartTime - startMs;
    chunkStartTime = Date.now() - recordingStartTime;

    if (uploadMode === "mixed") {
      const mixedData = mixedChunks.splice(0);
      console.log(
        `[Memoir Offscreen] Flush seq=${seq} final=${isFinal}: mixed=${mixedData.length} blobs, ` +
        `mixedRecorder.state=${mixedRecorder?.state}`
      );
      if (mixedData.length > 0) {
        const mixedBlob = new Blob(mixedData, { type: "audio/webm;codecs=opus" });
        await sendChunk("mixed", mixedBlob, seq, startMs, durationMs, isFinal);
      } else {
        console.warn(`[Memoir Offscreen] No mixed data for seq=${seq}`);
      }
    } else {
      const tabData = tabChunks.splice(0);
      const micData = micChunks.splice(0);

      console.log(
        `[Memoir Offscreen] Flush seq=${seq} final=${isFinal}: ` +
        `tab=${tabData.length} blobs, mic=${micData.length} blobs, ` +
        `micRecorder.state=${micRecorder?.state}, micStream.active=${micStream?.active}`
      );

      // Send tab chunk
      if (tabData.length > 0) {
        const tabBlob = new Blob(tabData, { type: "audio/webm;codecs=opus" });
        await sendChunk("tab", tabBlob, seq, startMs, durationMs, isFinal);
      }

      // Send mic chunk
      if (micData.length > 0) {
        const micBlob = new Blob(micData, { type: "audio/webm;codecs=opus" });
        await sendChunk("mic", micBlob, seq, startMs, durationMs, isFinal);
      } else if (hasMic) {
        console.warn(`[Memoir Offscreen] No mic data for seq=${seq}`);
      }

      // Restart tab recorder only (mic stays running)
      if (!isFinal && tabStream) {
        try {
          tabRecorder = createRecorder(tabStream, tabChunks, allTabChunks);
          tabRecorder.start(15000);
        } catch (e) {
          console.warn("[Memoir Offscreen] Tab recorder restart failed:", e);
        }
        console.log("[Memoir Offscreen] Tab recorder restarted (mic continues)");
      }
    }
  } finally {
    isFlushing = false;
  }
}

async function listAudioInputs(): Promise<void> {
  try {
    let devices = await navigator.mediaDevices.enumerateDevices();
    let inputs = devices.filter((d) => d.kind === "audioinput");

    // If labels are empty, attempt a temporary mic capture to unlock labels.
    if (inputs.some((d) => !d.label)) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        stream.getTracks().forEach((t) => t.stop());
        devices = await navigator.mediaDevices.enumerateDevices();
        inputs = devices.filter((d) => d.kind === "audioinput");
      } catch {
        // Permission may be denied; return best-effort anonymous device list.
      }
    }

    const mapped = inputs.map((d, idx) => ({
      deviceId: d.deviceId,
      label: d.label || `Microphone ${idx + 1}`,
      isDefault: d.deviceId === "default",
    }));
    chrome.runtime.sendMessage({ type: "OFFSCREEN_AUDIO_INPUTS", devices: mapped }).catch(() => {});
  } catch (err) {
    console.warn("[Memoir Offscreen] Failed to list audio inputs:", err);
    chrome.runtime.sendMessage({ type: "OFFSCREEN_AUDIO_INPUTS", devices: [] }).catch(() => {});
  }
}

async function startRecording(
  streamId: string | null,
  micDeviceId?: string,
  requestedMode: "separate" | "mixed" = "separate"
): Promise<void> {
  // Ad-hoc / mic-only path: streamId omitted → skip tab capture, force
  // "mixed" mode (single source = mic). The page's chunk-uploader and
  // backend already accept track="mixed" with mic-only content; same
  // shape as the /record web-recorder pre-handoff flow.
  const micOnly = !streamId;
  try {
    uploadMode = micOnly ? "mixed" : requestedMode;

    // 1. Capture tab audio (skipped when streamId is null — mic-only path).
    if (!micOnly) {
      tabStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // @ts-ignore — Chrome-specific constraints
          mandatory: {
            chromeMediaSource: "tab",
            chromeMediaSourceId: streamId,
            googDisableLocalEcho: false,
          },
        },
        video: false,
      });
    } else {
      tabStream = null;
    }

    // 2. Capture local microphone
    try {
      const micAudioConstraints: MediaTrackConstraints = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
      };
      if (micDeviceId) {
        micAudioConstraints.deviceId = { exact: micDeviceId };
      }

      micStream = await navigator.mediaDevices.getUserMedia({
        audio: micAudioConstraints,
        video: false,
      });
      // If selected device is unavailable, retry using default mic.
    } catch (micErr) {
      if (micDeviceId) {
        console.warn(`[Memoir Offscreen] Selected mic unavailable (${micDeviceId}); retrying with default mic`, micErr);
        try {
          micStream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: false,
            },
            video: false,
          });
        } catch (fallbackErr) {
          console.warn("[Memoir Offscreen] Mic access denied — tab-only recording:", fallbackErr);
          micStream = null;
          hasMic = false;
        }
      } else {
        console.warn("[Memoir Offscreen] Mic access denied — tab-only recording:", micErr);
        micStream = null;
        hasMic = false;
      }
    }

    // Mic-only path with no mic stream = nothing to record. Bail.
    if (micOnly && !micStream) {
      throw new Error("Microphone unavailable — cannot start ad-hoc recording");
    }

    if (micStream) {
      hasMic = true;
      const settings = micStream.getAudioTracks()[0]?.getSettings();
      if (settings?.deviceId) {
        console.log(`[Memoir Offscreen] Using mic deviceId=${settings.deviceId}`);
      }
      console.log("[Memoir Offscreen] Mic access granted — dual-track recording");
    } else {
      hasMic = false;
    }

    // 3. MIME type
    recorderMimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";

    // 4. Init state
    recordingStartTime = Date.now();
    chunkStartTime = 0;
    chunkSeq = 0;
    tabChunks = [];
    micChunks = [];
    mixedChunks = [];
    allTabChunks = [];
    allMicChunks = [];
    allMixedChunks = [];
    isFlushing = false;

    // 5. Start recording according to upload mode.
    if (uploadMode === "mixed") {
      audioContext = new AudioContext();
      const destination = audioContext.createMediaStreamDestination();
      if (tabStream) {
        const tabSource = audioContext.createMediaStreamSource(tabStream);
        tabSource.connect(destination);
        tabSource.connect(audioContext.destination); // play tab audio through speakers
      }
      if (micStream) {
        const micSource = audioContext.createMediaStreamSource(micStream);
        micSource.connect(destination);
      }
      mixedStream = destination.stream;
      mixedRecorder = createRecorder(mixedStream, mixedChunks, allMixedChunks);
      mixedRecorder.start(1000);
    } else {
      // Play tab audio through speakers so user can hear remote participant
      if (tabStream) {
        audioContext = new AudioContext();
        const tabSource = audioContext.createMediaStreamSource(tabStream);
        tabSource.connect(audioContext.destination);

        tabRecorder = createRecorder(tabStream, tabChunks, allTabChunks);
        tabRecorder.start(15000);
      }

      if (micStream) {
        micRecorder = createRecorder(micStream, micChunks, allMicChunks);
        micRecorder.start(1000);
      }
    }

    // 6. Periodic flush
    flushTimer = setInterval(() => {
      flushChunks(false);
    }, FLUSH_INTERVAL_MS);

    console.log(`[Memoir Offscreen] Recording started (mode=${uploadMode}, mic: ${hasMic ? "yes" : "no"})`);
  } catch (err) {
    console.error("[Memoir Offscreen] Failed to start recording:", err);
    chrome.runtime.sendMessage({
      type: "TAB_AUDIO_ERROR",
      error: err instanceof Error ? err.message : "Failed to capture tab audio",
    });
  }
}

async function stopRecording(): Promise<void> {
  if (flushTimer) {
    clearInterval(flushTimer);
    flushTimer = null;
  }

  // If a periodic flush is in flight, wait for it so final flush isn't skipped.
  while (isFlushing) {
    await new Promise((r) => setTimeout(r, 25));
  }

  const hadMic = hasMic;
  await flushChunks(true);

  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  if (tabStream) {
    tabStream.getTracks().forEach((t) => t.stop());
    tabStream = null;
  }

  tabRecorder = null;
  micRecorder = null;
  mixedRecorder = null;
  mixedStream = null;
  if (audioContext) {
    await audioContext.close().catch(() => {});
    audioContext = null;
  }
  hasMic = false;
  console.log("[Memoir Offscreen] Recording stopped");

  chrome.runtime.sendMessage({
    type: "OFFSCREEN_STOPPED",
    hasMic: hadMic,
    finalSeq: Math.max(0, chunkSeq - 1),
  }).catch(() => {});
}

async function getFullFile(): Promise<void> {
  const primaryChunks = uploadMode === "mixed" ? allMixedChunks : allTabChunks;
  const tabBlob = new Blob(primaryChunks, { type: "audio/webm;codecs=opus" });
  const tabBuffer = await tabBlob.arrayBuffer();
  const tabBase64 = arrayBufferToBase64(tabBuffer);

  let micBase64: string | undefined;
  if (uploadMode === "separate" && allMicChunks.length > 0) {
    const micBlob = new Blob(allMicChunks, { type: "audio/webm;codecs=opus" });
    const micBuffer = await micBlob.arrayBuffer();
    micBase64 = arrayBufferToBase64(micBuffer);
  }

  chrome.runtime.sendMessage({
    type: "OFFSCREEN_FULL_FILE_READY",
    tabBase64,
    micBase64,
  });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "OFFSCREEN_START_RECORDING") {
    startRecording(msg.streamId, msg.micDeviceId, msg.uploadMode ?? "separate").catch((err) => {
      console.error("[Memoir Offscreen] startRecording failed:", err);
      chrome.runtime.sendMessage({ type: "TAB_AUDIO_ERROR", error: err?.message ?? "getUserMedia failed" }).catch(() => {});
    });
  } else if (msg.type === "OFFSCREEN_STOP_RECORDING") {
    stopRecording().catch(() => {});
  } else if (msg.type === "OFFSCREEN_LIST_AUDIO_INPUTS") {
    listAudioInputs().catch(() => {});
  } else if (msg.type === "OFFSCREEN_GET_FULL_FILE") {
    getFullFile();
  }
});

chrome.runtime.sendMessage({ type: "OFFSCREEN_READY" }).catch(() => {});
