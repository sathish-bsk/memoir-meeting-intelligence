// Memoir BotWorker audio capture — offscreen document
// Captures tab audio via getUserMedia with chromeMediaSource: "tab"
// Stores chunks in memory for extraction via page.evaluate

let recorder = null;
let stream = null;
let chunks = [];
let chunkIndex = 0;

console.log("[Memoir Offscreen] Loaded");

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

async function startRecording(streamId, chunkMs) {
    console.log("[Memoir Offscreen] startRecording called, streamId:", streamId ? streamId.substring(0, 20) + "..." : "null", "chunkMs:", chunkMs);
    try {
        console.log("[Memoir Offscreen] Calling getUserMedia with chromeMediaSource: tab...");
        stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                mandatory: {
                    chromeMediaSource: "tab",
                    chromeMediaSourceId: streamId,
                },
            },
            video: false,
        });

        const audioTracks = stream.getAudioTracks();
        console.log("[Memoir Offscreen] getUserMedia success:", audioTracks.length, "audio track(s)");
        for (const t of audioTracks) {
            console.log("[Memoir Offscreen] Track:", t.label, "enabled:", t.enabled, "readyState:", t.readyState);
            const settings = t.getSettings();
            console.log("[Memoir Offscreen] Track settings:", JSON.stringify(settings));
        }

        const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus"
            : "audio/webm";
        console.log("[Memoir Offscreen] Using mimeType:", mimeType);

        chunks = [];
        chunkIndex = 0;

        recorder = new MediaRecorder(stream, {
            mimeType,
            audioBitsPerSecond: 128000,
        });

        recorder.ondataavailable = async (e) => {
            if (e.data && e.data.size > 0) {
                const buffer = await e.data.arrayBuffer();
                const base64 = arrayBufferToBase64(buffer);
                chunks.push({
                    data: "data:audio/webm;base64," + base64,
                    index: chunkIndex++,
                    size: buffer.byteLength,
                });
                console.log("[Memoir Offscreen] Chunk", chunkIndex - 1, "ready:", buffer.byteLength, "bytes");
            }
        };

        recorder.onerror = (e) => {
            console.error("[Memoir Offscreen] Recorder error:", e);
            recorder.stop();
        };

        recorder.onstop = () => {
            console.log("[Memoir Offscreen] Recorder stopped");
            if (stream) {
                stream.getTracks().forEach((t) => t.stop());
            }
        };

        recorder.start(chunkMs);
        console.log("[Memoir Offscreen] MediaRecorder started, state:", recorder.state);
    } catch (err) {
        console.error("[Memoir Offscreen] Failed to start:", err.message || err);
        console.error("[Memoir Offscreen] Error stack:", err.stack);
    }
}

function stopRecording() {
    console.log("[Memoir Offscreen] stopRecording called, recorder state:", recorder ? recorder.state : "null");
    if (recorder && recorder.state !== "inactive") {
        recorder.stop();
    }
    recorder = null;
    stream = null;
    console.log("[Memoir Offscreen] Recording stopped, total chunks produced:", chunkIndex);
}

function pollChunks() {
    const ready = chunks.splice(0);
    return ready;
}

function getStatus() {
    return {
        recorderState: recorder ? recorder.state : "none",
        pendingChunks: chunks.length,
        totalProduced: chunkIndex,
        streamActive: stream ? stream.active : false,
    };
}

// Listen for messages from background service worker
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    console.log("[Memoir Offscreen] Message received:", msg.type);

    if (msg.type === "OFFSCREEN_START_RECORDING") {
        console.log("[Memoir Offscreen] Start recording request, streamId present:", !!msg.streamId);
        startRecording(msg.streamId, msg.chunkMs || 30000);
    } else if (msg.type === "OFFSCREEN_STOP_RECORDING") {
        stopRecording();
    } else if (msg.type === "POLL_CHUNKS") {
        sendResponse(pollChunks());
        return true;
    } else if (msg.type === "GET_STATUS") {
        sendResponse(getStatus());
        return true;
    }
});

// Notify background that offscreen is ready
console.log("[Memoir Offscreen] Sending OFFSCREEN_READY to background");
chrome.runtime.sendMessage({ type: "OFFSCREEN_READY" }).catch((e) => {
    console.warn("[Memoir Offscreen] Failed to send OFFSCREEN_READY:", e.message);
});
