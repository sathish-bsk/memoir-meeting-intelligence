// Memoir BotWorker audio capture — background service worker
//
// Coordinates between the control page (driven by Python via Playwright)
// and the offscreen document that does the actual capture. The two
// distinct ready signals are:
//   - OFFSCREEN_LOADED          → script has parsed
//   - OFFSCREEN_READY_TO_RECORD → getUserMedia + AudioContext + AudioWorklet
//                                  + WebSocket open + hello sent
// Only the second one unblocks START_TAB_AUDIO. Stop is symmetric: STOP_TAB_AUDIO
// withholds its sendResponse until OFFSCREEN_STOPPED arrives, which the
// offscreen sends after draining ws.bufferedAmount and closing the socket.

console.log("[Memoir BG] Background service worker started");

let offscreenReady = false;
let pendingStart = null;          // queued OFFSCREEN_START until OFFSCREEN_LOADED
const startWaiters = new Map();   // requestId -> { resolve }
const stopWaiters = new Map();    // requestId -> { resolve }
let nextRequestId = 1;

// MV3 chrome.tabCapture.getMediaStreamId rejects with "Extension has not
// been invoked for the current page" unless the extension was invoked on
// that tab via toolbar click, command, or popup. The only mechanism that
// works in headless Chrome under Xvfb is a real OS keypress matching the
// command shortcut declared in manifest.json. Python sends that keypress
// via xdotool right before calling startCapture; the listener below
// records the invocation so GET_STREAM_ID can validate it explicitly
// rather than letting Chrome surface an opaque error.
let invokedTabId = null;
let invokedAt = 0;
const INVOCATION_TTL_MS = 10000;

chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== "invoke-tab-capture") {
    console.warn("[Memoir BG] unexpected command:", command);
    return;
  }
  if (!tab || !tab.id) {
    console.warn("[Memoir BG] invoke-tab-capture fired without tab info:", tab);
    return;
  }
  invokedTabId = tab.id;
  invokedAt = Date.now();
  console.log("[Memoir BG] invocation recorded", { tabId: tab.id, url: tab.url });
});

// chrome.tabs.query({url}) accepts match patterns, not exact URLs (see
// https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/tabs/query),
// so query with broad patterns and filter by normalized URL equality in JS.
const MEETING_URL_PATTERNS = ["*://meet.google.com/*"];

async function ensureOffscreen() {
  // chrome.offscreen.hasDocument() is the MV3 API; some Chromium builds
  // expose it as a callback instead of a promise — wrap defensively.
  let has = false;
  try {
    has = await chrome.offscreen.hasDocument();
  } catch (_) {
    has = false;
  }
  if (has) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Capture meeting tab audio for transcription",
  });
}

function normalizeMeetUrl(raw) {
  try {
    const u = new URL(raw);
    return u.origin + u.pathname;
  } catch (_) {
    return null;
  }
}

function findMeetingTab(targetUrl, sendResponse) {
  if (!targetUrl) {
    sendResponse({ error: "FIND_MEETING_TAB requires a targetUrl" });
    return;
  }
  const wantNorm = normalizeMeetUrl(targetUrl);
  if (!wantNorm) {
    sendResponse({ error: `FIND_MEETING_TAB: targetUrl is not a valid URL: ${targetUrl}` });
    return;
  }
  // Query each pattern and union results. Patterns are extension match
  // patterns, not URL strings — they cannot include query/fragment.
  let pending = MEETING_URL_PATTERNS.length;
  let collected = [];
  let answered = false;
  MEETING_URL_PATTERNS.forEach((pat) => {
    chrome.tabs.query({ url: pat }, (tabs) => {
      collected = collected.concat(tabs || []);
      pending -= 1;
      if (pending !== 0 || answered) return;
      answered = true;
      const matches = collected.filter(
        (t) => normalizeMeetUrl(t.url) === wantNorm
      );
      if (!matches.length) {
        sendResponse({
          error:
            `no meeting tab with normalized URL ${wantNorm} ` +
            `(saw ${collected.length} candidate tab(s))`,
        });
        return;
      }
      if (matches.length > 1) {
        sendResponse({
          error: `${matches.length} tabs share normalized URL ${wantNorm}; refusing to guess`,
        });
        return;
      }
      sendResponse({ tabId: matches[0].id, url: matches[0].url });
    });
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "FIND_MEETING_TAB") {
    findMeetingTab(msg.targetUrl, sendResponse);
    return true;
  }
  if (msg.type === "GET_INVOCATION_STATE") {
    sendResponse({
      invokedTabId,
      invokedAt,
      ageMs: invokedAt ? Date.now() - invokedAt : null,
      ttlMs: INVOCATION_TTL_MS,
    });
    return false;
  }
  if (msg.type === "GET_STREAM_ID") {
    // Validate explicitly. Without an invocation matching this tabId
    // within TTL, Chrome will reject getMediaStreamId with an opaque
    // "Extension has not been invoked" message — surface it as a
    // distinguishable invocation error instead.
    if (invokedTabId === null) {
      sendResponse({
        error: "tabCapture invocation missing — no command has fired yet",
        invocation: { invokedTabId: null, ageMs: null },
      });
      return false;
    }
    if (invokedTabId !== msg.tabId) {
      sendResponse({
        error: `tabCapture invocation tab mismatch (invoked=${invokedTabId}, requested=${msg.tabId})`,
        invocation: { invokedTabId, ageMs: Date.now() - invokedAt },
      });
      return false;
    }
    const ageMs = Date.now() - invokedAt;
    if (ageMs > INVOCATION_TTL_MS) {
      sendResponse({
        error: `tabCapture invocation expired (age=${ageMs}ms, ttl=${INVOCATION_TTL_MS}ms)`,
        invocation: { invokedTabId, ageMs },
      });
      return false;
    }
    chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId }, (streamId) => {
      const err = chrome.runtime.lastError;
      if (err) {
        sendResponse({
          error: `Chrome runtime: ${err.message}`,
          invocation: { invokedTabId, ageMs },
        });
      } else {
        sendResponse({ streamId, invocation: { invokedTabId, ageMs } });
      }
    });
    return true;
  }
  if (msg.type === "START_TAB_AUDIO") {
    (async () => {
      const requestId = nextRequestId++;
      try {
        await ensureOffscreen();
        startWaiters.set(requestId, { resolve: sendResponse });
        const send = () =>
          chrome.runtime.sendMessage({
            type: "OFFSCREEN_START",
            requestId,
            streamId: msg.streamId,
            wsPort: msg.wsPort,
          });
        if (offscreenReady) send();
        else pendingStart = send;
        // sendResponse held until OFFSCREEN_READY_TO_RECORD or OFFSCREEN_FAILED
      } catch (e) {
        startWaiters.delete(requestId);
        sendResponse({ error: e.message || String(e) });
      }
    })();
    return true;
  }
  if (msg.type === "STOP_TAB_AUDIO") {
    const requestId = nextRequestId++;
    stopWaiters.set(requestId, { resolve: sendResponse });
    chrome.runtime.sendMessage({ type: "OFFSCREEN_STOP", requestId });
    // sendResponse held until OFFSCREEN_STOPPED
    return true;
  }
  if (msg.type === "OFFSCREEN_LOADED") {
    offscreenReady = true;
    if (pendingStart) { pendingStart(); pendingStart = null; }
    return false;
  }
  if (msg.type === "OFFSCREEN_READY_TO_RECORD") {
    const w = startWaiters.get(msg.requestId);
    if (w) {
      startWaiters.delete(msg.requestId);
      w.resolve({ ok: true, info: msg.info });
    }
    return false;
  }
  if (msg.type === "OFFSCREEN_FAILED") {
    const w = startWaiters.get(msg.requestId);
    if (w) {
      startWaiters.delete(msg.requestId);
      w.resolve({ error: msg.error });
    }
    return false;
  }
  if (msg.type === "OFFSCREEN_STOPPED") {
    const w = stopWaiters.get(msg.requestId);
    if (w) {
      stopWaiters.delete(msg.requestId);
      w.resolve({ ok: true, framesSent: msg.framesSent });
    }
    return false;
  }
  if (msg.type === "GET_STATUS") {
    chrome.runtime.sendMessage({ type: "OFFSCREEN_STATUS" }, (r) => sendResponse(r));
    return true;
  }
});
