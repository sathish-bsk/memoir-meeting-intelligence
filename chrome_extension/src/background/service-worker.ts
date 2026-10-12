import type { ExtensionMessage, ExtensionResponse, ExtensionState, AuthState, MeetingState, AudioInputDevice, UploadAudioMode, MeetingTabInfo } from "../shared/types";
import {
  ALARM_TOKEN_REFRESH,
  ALARM_POLL_MEETING,
  MEETING_POLL_MINUTES,
  WEB_BASE,
  ZUSTAND_AUTH_KEY,
} from "../shared/constants";
import { saveAuth, loadAuth, clearAuth, refreshToken } from "./token-manager";
import { IDLE_STATE, transition, saveMeetingState, loadMeetingState } from "./state-machine";
import * as api from "./api-client";

// ─── In-memory state (re-hydrated from storage on wake) ───────────

let authState: AuthState | null = null;
let meetingState: MeetingState = IDLE_STATE;

// Tab audio state (in-memory only — also persisted to storage)
let captionEvents: Array<{ speaker: string; start: number; end: number }> = [];
let consecutiveChunkFailures = 0;
const MAX_CHUNK_FAILURES = 5;
const MIC_DEVICE_PREFS_KEY = "memoir-mic-device-prefs";
const UPLOAD_MODE_PREFS_KEY = "memoir-upload-mode-prefs";
let selectedMicDeviceId: string | undefined;
let selectedUploadMode: UploadAudioMode = "separate";

/**
 * Captured meeting URLs per tab — used to recover the real Teams meeting URL
 * before the SPA rewrites it to /v2/.
 * Key: tab ID, Value: original meeting URL (e.g. /meet/ABC?p=...)
 */
const capturedMeetingUrls = new Map<number, string>();

// Teams meeting URL patterns (the real joinable ones, not /v2/)
const TEAMS_MEETING_URL_RE = /teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\//;

async function hydrate() {
  authState = await loadAuth();
  meetingState = await loadMeetingState();
  selectedMicDeviceId = await loadSelectedMicDevice();
  selectedUploadMode = await loadSelectedUploadMode();
  updateBadge();

  // If the SW was terminated mid-meeting, the in-memory setInterval is dead
  // and only the 30 s chrome.alarms backup poll survives. Re-arm fast polling
  // on wake when we're still in an active meeting state. The immediate
  // pollMeetingStatus() removes the residual 1.5 s timer lag for non-alarm
  // wake paths (e.g. waking via a runtime message or webNavigation event).
  // For alarm-driven wakes the alarm handler will also call pollMeetingStatus
  // ~ms later — both calls are idempotent (the second sees state already
  // transitioned and short-circuits).
  if (
    meetingState.state === "joining" ||
    meetingState.state === "starting_to_record" ||
    meetingState.state === "recording"
  ) {
    // SW-termination recovery: poll immediately to re-establish the
    // correct state. If we're in "starting_to_record", let the poll
    // observe meeting.recording_started_at — it'll flip us to
    // "recording" when the signal is present, or keep us in
    // "starting_to_record" (with a fresh fallback timer) if not.
    startFastPolling();
    void pollMeetingStatus();
  }
}

// Hydrate on service worker start
hydrate();

// ─── Capture Teams meeting URLs before SPA rewrites ───────────────
// Teams loads /meet/ABC?p=... then client-side redirects to /v2/.
// webNavigation fires BEFORE the redirect, so we capture the real URL.

const TEAMS_URL_STORAGE_KEY = "memoir-teams-meeting-url";

function captureTeamsUrl(tabId: number, url: string) {
  capturedMeetingUrls.set(tabId, url);
  // Also persist to storage so the content script can read it as fallback
  chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: url });
}

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return; // only main frame
  if (TEAMS_MEETING_URL_RE.test(details.url)) {
    captureTeamsUrl(details.tabId, details.url);
    console.log(`[Memoir] Captured Teams meeting URL for tab ${details.tabId}: ${details.url}`);
  }
  // Capture Zoom password from landing pages
  if (/zoom\.us\/(s|j)\/\d+/.test(details.url)) {
    captureZoomPassword(details.tabId, details.url);
  }
});

// onBeforeNavigate fires even earlier than onCommitted
chrome.webNavigation.onBeforeNavigate.addListener((details) => {
  if (details.frameId !== 0) return;
  if (TEAMS_MEETING_URL_RE.test(details.url)) {
    captureTeamsUrl(details.tabId, details.url);
  }
  if (/zoom\.us\/(s|j)\/\d+/.test(details.url)) {
    captureZoomPassword(details.tabId, details.url);
  }
});

// ─── Capture Zoom meeting password from landing page URLs ─────────
// Zoom landing pages (/s/, /j/) have the password in ?pwd=... but when
// redirecting to /wc/MEETING_ID/start, the pwd is often stripped.
// We capture it early and re-attach when detecting the active meeting.

const capturedZoomPasswords = new Map<number, string>(); // tabId → pwd

function captureZoomPassword(tabId: number, url: string) {
  try {
    const u = new URL(url);
    const pwd = u.searchParams.get("pwd");
    if (pwd) {
      capturedZoomPasswords.set(tabId, pwd);
      console.log(`[Memoir] Captured Zoom pwd for tab ${tabId}`);
    }
  } catch {}
}

function attachZoomPassword(tabId: number, url: string): string {
  const pwd = capturedZoomPasswords.get(tabId);
  if (!pwd) return url;
  try {
    const u = new URL(url);
    if (!u.searchParams.has("pwd")) {
      u.searchParams.set("pwd", pwd);
      return u.toString();
    }
  } catch {}
  return url;
}

// ─── Meeting URL patterns for background-based detection ─────────
// Patterns are now (capturing group → meeting code) so the title carries
// the URL-derived identifier (e.g. "Meet bjx-gxtp-sjs", "Zoom 1234567890")
// instead of the generic platform name. Matches the rule used by
// detector.ts:getMeetingTitle so the two code paths produce consistent
// titles regardless of which one fires first.
const MEETING_URL_PATTERNS: { pattern: RegExp; platform: "google_meet" | "zoom" | "teams" }[] = [
  { pattern: /meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/, platform: "google_meet" },
  // Only /wc/DIGITS is an active Zoom web meeting.
  // /s/ and /j/ are landing pages ("Join from browser" / "Join from app").
  { pattern: /zoom\.us\/wc\/(\d+)/, platform: "zoom" },
  { pattern: /teams\.(?:live|microsoft)\.com\/(?:meet|l\/meetup-join)\/([^?#/]+)/, platform: "teams" },
];

function detectMeetingFromUrl(url: string): { platform: "google_meet" | "zoom" | "teams"; title: string } | null {
  for (const { pattern, platform } of MEETING_URL_PATTERNS) {
    const m = url.match(pattern);
    if (m) {
      const code = m[1];
      const prefixes: Record<string, string> = {
        google_meet: "Meet",
        zoom: "Zoom",
        teams: "Teams",
      };
      const title = code ? `${prefixes[platform]} ${code}` : `${prefixes[platform]} Meeting`;
      return { platform, title };
    }
  }
  return null;
}

// tabs.onUpdated catches URL changes including pushState/replaceState
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url) {
    // Capture Teams meeting URLs
    if (TEAMS_MEETING_URL_RE.test(changeInfo.url)) {
      captureTeamsUrl(tabId, changeInfo.url);
    }

    // Capture Zoom password from landing page URLs (/s/, /j/)
    if (/zoom\.us\/(s|j)\/\d+/.test(changeInfo.url)) {
      captureZoomPassword(tabId, changeInfo.url);
    }

    // Background-based meeting detection: if we see a meeting URL and we're
    // idle / completed, transition to "detected". Also re-detect when the
    // SAME tab navigates to a DIFFERENT meeting URL while we're already in
    // "detected" — without this branch, a user who retries after a failed
    // recording (which transitions back to "detected" preserving the old
    // meetingUrl) and then opens a new Meet would record against the
    // stale URL. The first-test URL never gets overwritten because the
    // detection guard below originally only ran in idle/completed states.
    const detected = detectMeetingFromUrl(changeInfo.url);
    if (detected) {
      const isFreshDetection =
        meetingState.state === "idle" || meetingState.state === "completed";
      const isUrlChangeWhileDetected =
        meetingState.state === "detected" &&
        meetingState.tabId === tabId &&
        meetingState.meetingUrl !== changeInfo.url;
      if (isFreshDetection || isUrlChangeWhileDetected) {
        // For Zoom: attach captured password if the URL doesn't have it
        let meetingUrl = changeInfo.url;
        if (detected.platform === "zoom") {
          meetingUrl = attachZoomPassword(tabId, meetingUrl);
        }
        console.log(
          `[Memoir] Meeting ${isFreshDetection ? "detected" : "URL changed"} from tab URL: ${meetingUrl}`
        );
        transitionTo("detected", {
          platform: detected.platform,
          meetingUrl,
          meetingTitle: detected.title,
          tabId,
        });
        // Also inject content script for in-page UI (floating button)
        if (isFreshDetection) {
          chrome.scripting.executeScript({
            target: { tabId },
            files: [`content-${detected.platform === "google_meet" ? "meet" : detected.platform}.js`],
          }).catch(() => {});
        }
      }
    }

    // Update Zoom meeting URL if we get a better one (with pwd).
    // Zoom SPA changes URL in two steps: first /join?fromPWA=1 (no pwd),
    // then /start?fromPWA=1&pwd=... — we must capture the second URL.
    if (
      meetingState.tabId === tabId &&
      meetingState.platform === "zoom" &&
      (meetingState.state === "detected" || meetingState.state === "joining") &&
      /zoom\.us\/wc\/\d+/.test(changeInfo.url)
    ) {
      const newUrl = changeInfo.url;
      const hasPwd = newUrl.includes("pwd=");
      const currentHasPwd = meetingState.meetingUrl?.includes("pwd=") ?? false;
      if (hasPwd && !currentHasPwd) {
        console.log(`[Memoir] Updated Zoom meeting URL with pwd: ${newUrl}`);
        meetingState.meetingUrl = newUrl;
        saveMeetingState(meetingState).catch(() => {});
      }
    }

    // Detect when user navigates away from the meeting page
    if (meetingState.tabId === tabId && meetingState.state !== "idle" && meetingState.state !== "completed") {
      const url = changeInfo.url;
      const isStillMeeting =
        /meet\.google\.com\/[a-z]/.test(url) ||
        /zoom\.us\/wc\/\d+/.test(url) ||
        /teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\//.test(url);
      if (!isStillMeeting) {
        console.log(`[Memoir] Meeting tab navigated away: ${url}`);
        if (meetingState.state === "recording") {
          if (meetingState.recordingMode === "tab_audio") {
            // Trigger tab audio finalize flow
            finalizeTabAudio().catch((err) => console.error("[Memoir] Tab audio finalize error:", err));
          } else {
            transitionTo("completed");
            stopPolling();
            stopFastPolling();
          }
        } else if (meetingState.state === "joining") {
          transitionTo("completed");
          stopPolling();
          stopFastPolling();
        } else if (meetingState.state === "finalizing_upload") {
          // Don't interrupt an in-progress upload — let it complete
          console.log("[Memoir] Tab navigated but finalizing upload — leaving state alone");
        } else {
          transitionTo("idle");
        }
      }
    }
  }
});

// Clean up when tab closes — also reset meeting state if it was the meeting tab
chrome.tabs.onRemoved.addListener((tabId) => {
  capturedMeetingUrls.delete(tabId);
  capturedZoomPasswords.delete(tabId);
  if (meetingState.tabId === tabId && meetingState.state !== "idle" && meetingState.state !== "completed") {
    console.log(`[Memoir] Meeting tab closed`);
    if (meetingState.state === "recording") {
      if (meetingState.recordingMode === "tab_audio") {
        // Best-effort finalize on tab close
        finalizeTabAudio().catch(() => {});
      } else {
        transitionTo("completed");
        stopPolling();
        stopFastPolling();
      }
    } else if (meetingState.state === "joining" || meetingState.state === "starting_to_record") {
      transitionTo("completed");
      stopPolling();
      stopFastPolling();
    } else if (meetingState.state === "finalizing_upload") {
      // Let the ongoing finalization complete — don't reset state
      console.log("[Memoir] Tab closed but finalizing upload — leaving finalization running");
    } else {
      transitionTo("idle");
    }
  }
});

// ─── Badge management ─────────────────────────────────────────────

let badgeDurationTimer: ReturnType<typeof setInterval> | null = null;

function formatBadgeDuration(startedAt: number): string {
  const secs = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  // Chrome action badge fits ~4 chars; MM:SS up to 99min, then Hh.
  if (m < 100) return `${m}:${String(s).padStart(2, "0")}`;
  return `${Math.floor(m / 60)}h`;
}

function tickRecordingBadge() {
  if (!meetingState.recordingStartedAt) return;
  chrome.action.setBadgeText({ text: formatBadgeDuration(meetingState.recordingStartedAt) });
  chrome.action.setBadgeBackgroundColor({ color: "#E5484D" });
}

function updateBadge() {
  const s = meetingState.state;

  if (badgeDurationTimer) {
    clearInterval(badgeDurationTimer);
    badgeDurationTimer = null;
  }

  if (s === "recording") {
    // Live MM:SS ticker instead of static "REC"
    tickRecordingBadge();
    badgeDurationTimer = setInterval(tickRecordingBadge, 1000);
  } else if (s === "joining" || s === "starting_to_record" || s === "finalizing_upload") {
    chrome.action.setBadgeText({ text: "..." });
    chrome.action.setBadgeBackgroundColor({ color: "#5C5CE0" });
  } else if (s === "detected") {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setBadgeBackgroundColor({ color: "#5C5CE0" });
  } else if (s === "completed") {
    chrome.action.setBadgeText({ text: " \u2713 " });
    chrome.action.setBadgeBackgroundColor({ color: "#22C55E" });
  } else {
    chrome.action.setBadgeText({ text: "" });
  }
}

// ─── Transition helper ────────────────────────────────────────────

let completedResetTimer: ReturnType<typeof setTimeout> | null = null;

// Tracks the 1.5 s setTimeout that fires the "starting_to_record" →
// "recording" transition. Held at module scope (not inside the poll
// handler) so we can cancel it from any exit path — see the centralized
// clear in transitionTo() below.
let startingTransitionTimer: ReturnType<typeof setTimeout> | null = null;

function clearStartingTransitionTimer() {
  if (startingTransitionTimer) {
    clearTimeout(startingTransitionTimer);
    startingTransitionTimer = null;
  }
}

async function transitionTo(
  to: MeetingState["state"],
  updates?: Partial<MeetingState>
) {
  const from = meetingState.state;

  // Clear any pending completed→idle timer
  if (completedResetTimer) {
    clearTimeout(completedResetTimer);
    completedResetTimer = null;
  }

  // Centralized cleanup for the 1.5 s "starting_to_record" → "recording"
  // timer. Any transition AWAY from starting_to_record (failure,
  // completion, tab close, manual reset, etc.) cancels the pending
  // timer here — so the per-branch code never has to remember. The
  // timer's own callback also nulls its handle before transitioning,
  // making this clear a harmless no-op on the happy path.
  if (from === "starting_to_record" && to !== "starting_to_record") {
    clearStartingTransitionTimer();
  }

  meetingState = transition(meetingState, to, updates);
  await saveMeetingState(meetingState);
  updateBadge();
  // Stop the fast 1.5s status poll once we reach steady-state
  // recording; restart it if we ever rewind into joining/starting.
  tunePollIntervalForState(to);

  // Auto-reset "completed" to "idle" after 5 seconds
  if (to === "completed") {
    completedResetTimer = setTimeout(async () => {
      if (meetingState.state === "completed") {
        meetingState = transition(meetingState, "idle");
        await saveMeetingState(meetingState);
        updateBadge();
      }
      completedResetTimer = null;
    }, 5000);
  }
}

// ─── Start / stop meeting polling (for bot recordings) ────────────
// chrome.alarms minimum is ~30s — too slow for "joining" state.
// Use setInterval for fast polling while service worker is alive,
// keep chrome.alarms as reliable backup (service worker can be killed).
//
// Cadence is adaptive (see pickPollInterval): the 1.5 s rate exists
// only to minimize the gap on the joined_meeting → recording_started
// transition (FFmpeg cold-start window, ~1-2 s). The rest of the
// "joining" window — orchestrator queue, container boot, Chromium
// nav, and the human-gated lobby wait — has no value in sub-second
// detection. We downshift to 5 s there.

let fastPollTimer: ReturnType<typeof setInterval> | null = null;
let fastPollIntervalMs: number | null = null;

const POLL_FAST_MS = 1500;
const POLL_SLOW_MS = 5000;

function startPolling() {
  chrome.alarms.create(ALARM_POLL_MEETING, { periodInMinutes: MEETING_POLL_MINUTES });
  applyPollCadence();
}

function stopPolling() {
  chrome.alarms.clear(ALARM_POLL_MEETING);
  stopFastPolling();
}

/** Pick the polling interval based on the local meeting state and the
 *  last server-side status we observed via /status. Returns null when
 *  the fast loop should not run at all (steady-state recording —
 *  chrome.alarms 30 s backup handles meeting-ended).
 *
 *  Rationale:
 *  - starting_to_record: FFmpeg cold-start window (~1-2 s) — we want
 *    sub-second detection of recording_started_at landing.
 *  - joining + server status is "recording" (rsa=null): joined_meeting
 *    already landed, recording_started imminent — same as above.
 *  - joining + server status is anything else (or unknown): we're
 *    either pre-bot_joining, navigating, or parked in the lobby. The
 *    lobby is human-gated (host clicks Admit) and can be 0 s to 5 min.
 *    1.5 s polling there fires ~200 useless GETs over a 5 min lobby
 *    wait. 5 s gives identical user experience.
 *  - recording: fast loop stopped entirely (chrome.alarms backup
 *    catches meeting_ended at 30 s).
 */
function pickPollInterval(): number | null {
  // Stop-requested override: if the user clicked Stop within the last
  // 60 s, force fast polling so we catch the recording → processing
  // status flip ASAP (drives the floating button from "Uploading
  // recording..." back to "Record with Memoir"). Without this, the
  // recording-state default of `null` (alarm-only) means a 30 s wait
  // for chrome.alarms to fire. The clear happens in pollMeetingStatus
  // the moment we observe a non-recording server status.
  if (
    meetingState.stopRequestedAt &&
    Date.now() - meetingState.stopRequestedAt < 60_000
  ) {
    return POLL_FAST_MS;
  }
  if (meetingState.state === "recording") return null;
  if (meetingState.state === "starting_to_record") return POLL_FAST_MS;
  if (meetingState.state === "joining") {
    // Server has flipped to "recording" but our local state hasn't
    // caught up yet (the very next poll will move us to
    // starting_to_record). Stay fast for that one tick.
    if (meetingState.lastSeenServerStatus === "recording") return POLL_FAST_MS;
    return POLL_SLOW_MS;
  }
  return null;
}

/** Apply the cadence picked by pickPollInterval(). Idempotent — if the
 *  interval is already at the target value, leaves the timer alone
 *  instead of resetting the clock every tick. */
function applyPollCadence() {
  const target = pickPollInterval();
  if (target === null) {
    stopFastPolling();
    return;
  }
  if (fastPollIntervalMs === target && fastPollTimer) return;
  stopFastPolling();
  fastPollIntervalMs = target;
  fastPollTimer = setInterval(() => {
    pollMeetingStatus();
  }, target);
}

/** Back-compat wrapper for sites that called startFastPolling() directly. */
function startFastPolling() {
  applyPollCadence();
}

/** Called by transitionTo after every local-state change. */
function tunePollIntervalForState(_state: string) {
  applyPollCadence();
}

function stopFastPolling() {
  if (fastPollTimer) {
    clearInterval(fastPollTimer);
    fastPollTimer = null;
  }
  fastPollIntervalMs = null;
}

async function pollMeetingStatus() {
  if (!meetingState.meetingId) return;
  if (meetingState.recordingMode === "tab_audio") return; // tab audio doesn't poll
  if (
    meetingState.state !== "joining" &&
    meetingState.state !== "starting_to_record" &&
    meetingState.state !== "recording"
  ) {
    stopPolling();
    stopFastPolling();
    return;
  }

  try {
    const _t_poll_start = Date.now();
    // Lightweight polling endpoint — single SELECT, ~5 ms response. Was
    // previously calling api.getMeeting() which fans out to 5-6 SELECTs
    // and under sustained polling load saturated the DB connection pool
    // (observed 2026-05-14: 15-18 s connection holds, webhook handlers
    // stalling for 14 s). The status endpoint returns the only three
    // fields this loop actually needs.
    const meeting = await api.getMeetingStatus(meetingState.meetingId);
    const _poll_ms = Date.now() - _t_poll_start;
    if (meetingState.state === "joining" || meetingState.state === "starting_to_record") {
      console.log(
        `[POLL] status=${meeting.status} recording_started_at=${meeting.recording_started_at ?? "null"} ` +
        `local_state=${meetingState.state} poll_latency=${_poll_ms}ms cadence=${fastPollIntervalMs ?? "off"}ms`
      );
    }

    // Record what we saw so pickPollInterval() can downshift during
    // the lobby wait (status="waiting_in_lobby") and upshift the moment
    // the bot transitions to "recording" (joined_meeting webhook landed,
    // FFmpeg about to come online). Persist via transition() so a SW
    // termination doesn't lose it.
    if (meetingState.lastSeenServerStatus !== meeting.status) {
      meetingState = { ...meetingState, lastSeenServerStatus: meeting.status };
      await saveMeetingState(meetingState);
      applyPollCadence();
    }

    // Clear stopRequestedAt once the server confirms we've left the
    // recording state. The next applyPollCadence call below (or the
    // state-transition branch handlers) will pick a normal cadence
    // again. Defensive 60 s ceiling already in pickPollInterval — this
    // is just the happy-path clear.
    if (
      meetingState.stopRequestedAt &&
      meeting.status !== "recording"
    ) {
      meetingState = { ...meetingState, stopRequestedAt: undefined };
      saveMeetingState(meetingState).catch(() => {});
    }

    if (meeting.status === "recording" && !meeting.recording_started_at &&
        (meetingState.state === "joining" || meetingState.state === "starting_to_record")) {
      // Backend says bot is admitted but recording_started webhook has
      // not landed yet — meaning the bot's FFmpeg is still spinning up.
      // Show "Starting to record..." and WAIT for the signal. The
      // previous timer-based version flipped to "Stop Recording" after
      // 1.5 s regardless, losing the first 1-2 s of audio because the
      // user starts speaking before the recorder is actually capturing.
      // We arm a 30 s fallback timer below in case the signal never
      // arrives (e.g., recording_started webhook lost or rejected) so
      // the UI doesn't get stuck on "Starting to record..." forever.
      if (meetingState.state === "joining") {
        await transitionTo("starting_to_record");
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "starting_to_record",
            mode: "bot",
          }).catch(() => {});
        }
        // Arm fallback timer (idempotent — clear any existing one first).
        clearStartingTransitionTimer();
        startingTransitionTimer = setTimeout(async () => {
          startingTransitionTimer = null;
          if (meetingState.state !== "starting_to_record") return;
          console.warn(
            "[Memoir] recording_started signal didn't arrive within 30s; flipping anyway"
          );
          await transitionTo("recording", { recordingStartedAt: Date.now() });
          if (meetingState.tabId) {
            chrome.tabs.sendMessage(meetingState.tabId, {
              type: "RECORDING_STATUS_UPDATE",
              state: "recording",
              mode: "bot",
            }).catch(() => {});
          }
        }, 30000);
      }
      // else: already in starting_to_record — nothing to do, just keep polling
    } else if (meeting.status === "recording" && meeting.recording_started_at &&
               (meetingState.state === "joining" || meetingState.state === "starting_to_record")) {
      // The signal we've been waiting for — bot's recorder is actually
      // capturing audio. Flip to "Stop Recording". Centralized clear in
      // transitionTo() cancels the fallback timer.
      await transitionTo("recording", { recordingStartedAt: Date.now() });
      if (meetingState.tabId) {
        chrome.tabs.sendMessage(meetingState.tabId, {
          type: "RECORDING_STATUS_UPDATE",
          state: "recording",
          mode: "bot",
        }).catch(() => {});
      }
    } else if (meeting.status === "failed") {
      // Bot failed — go back to detected so user can retry
      stopPolling();
      await transitionTo("detected", {
        meetingId: undefined,
        recordingStartedAt: undefined,
      });
      // Notify content script to show record button again
      if (meetingState.tabId) {
        chrome.tabs.sendMessage(meetingState.tabId, {
          type: "RECORDING_STATUS_UPDATE",
          state: "stopped",
          meetingId: undefined,
        }).catch(() => {});
      }
    } else if (meeting.status === "processing") {
      // The bot has been ejected from the meeting (either via manual
      // STOP_RECORDING or because the meeting ended naturally on the
      // bot side). Flip the button to "Record with Memoir" so the user
      // can record again if they're still in the meeting. We do NOT go
      // to "completed" here because the user is likely still in the
      // Meet tab — if they later leave the meeting, the detector
      // sees the "ended" page and hides the button on its own. If
      // they record again, a fresh meetingId is created.
      stopPolling();
      await transitionTo("detected", {
        meetingId: undefined,
        recordingStartedAt: undefined,
        recordingMode: undefined,
        uploadSessionId: undefined,
      });
      if (meetingState.tabId) {
        chrome.tabs.sendMessage(meetingState.tabId, {
          type: "RECORDING_STATUS_UPDATE",
          state: "stopped",
          meetingId: meetingState.meetingId,
        }).catch(() => {});
      }
    } else if (meeting.status === "completed") {
      // Full pipeline done (transcription + intelligence). Only fires
      // for meetings whose status transitions all the way through to
      // "completed" while the extension is still polling — rare in
      // practice because the manual-stop path moves to "processing"
      // first and we stop polling there.
      await transitionTo("completed");
      stopPolling();
      if (meetingState.tabId) {
        chrome.tabs.sendMessage(meetingState.tabId, {
          type: "RECORDING_STATUS_UPDATE",
          state: "completed",
          meetingId: meetingState.meetingId,
        }).catch(() => {});
      }
    }
  } catch (err) {
    console.error("[Memoir] Poll error:", err);
  }
}

// ─── Keep-alive port from content script ──────────────────────────
// MV3 service workers get terminated after ~30 s of inactivity, which
// kills the in-memory 1.5 s setInterval used for meeting polling. The
// content script opens a port named "memoir-keepalive" while in
// "joining"/"recording" state — an open port keeps the SW alive
// (documented MV3 escape hatch). We don't need to send/receive any
// messages on it; merely accepting and holding the reference until the
// content script disconnects is enough. Chrome force-disconnects ports
// at the 5 min hard cap; the content script reconnects on disconnect.
const KEEPALIVE_PORT_NAME = "memoir-keepalive";

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== KEEPALIVE_PORT_NAME) return;
  port.onDisconnect.addListener(() => {
    // No-op. Reconnection is driven by the content script.
  });
});

// ─── Alarm handler ────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === ALARM_TOKEN_REFRESH) {
    if (authState) {
      const refreshed = await refreshToken(authState);
      if (refreshed) {
        authState = refreshed;
      } else {
        // Token expired — clear auth
        authState = null;
        await clearAuth();
        updateBadge();
      }
    }
  } else if (alarm.name === ALARM_POLL_MEETING) {
    await pollMeetingStatus();
  }
});

// ─── Sync auth to open Memoir web tabs ────────────────────────────

async function syncAuthToWeb(auth: AuthState) {
  const tabs = await chrome.tabs.query({ url: [`${WEB_BASE}/*`] });
  for (const tab of tabs) {
    if (tab.id) {
      chrome.tabs.sendMessage(tab.id, {
        type: "AUTH_SYNC_TO_WEB",
        accessToken: auth.accessToken,
        activeTeamId: auth.activeTeamId,
        activeTeamName: auth.activeTeamName,
        user: auth.user,
      } satisfies ExtensionMessage).catch(() => {});
    }
  }
}

function getMicPreferenceScopeKey(): string {
  if (!authState) return "anon";
  return `${authState.user.id}:${authState.activeTeamId}`;
}

async function loadSelectedMicDevice(): Promise<string | undefined> {
  const data = await chrome.storage.local.get(MIC_DEVICE_PREFS_KEY);
  const prefs = (data[MIC_DEVICE_PREFS_KEY] ?? {}) as Record<string, string>;
  const value = prefs[getMicPreferenceScopeKey()];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

async function loadSelectedUploadMode(): Promise<UploadAudioMode> {
  const data = await chrome.storage.local.get(UPLOAD_MODE_PREFS_KEY);
  const prefs = (data[UPLOAD_MODE_PREFS_KEY] ?? {}) as Record<string, UploadAudioMode>;
  const value = prefs[getMicPreferenceScopeKey()];
  return value === "mixed" ? "mixed" : "separate";
}

async function saveSelectedUploadMode(mode: UploadAudioMode): Promise<void> {
  const data = await chrome.storage.local.get(UPLOAD_MODE_PREFS_KEY);
  const prefs = (data[UPLOAD_MODE_PREFS_KEY] ?? {}) as Record<string, UploadAudioMode>;
  prefs[getMicPreferenceScopeKey()] = mode;
  selectedUploadMode = mode;
  await chrome.storage.local.set({ [UPLOAD_MODE_PREFS_KEY]: prefs });
}

async function saveSelectedMicDevice(deviceId: string | null): Promise<void> {
  const data = await chrome.storage.local.get(MIC_DEVICE_PREFS_KEY);
  const prefs = (data[MIC_DEVICE_PREFS_KEY] ?? {}) as Record<string, string>;
  const scopeKey = getMicPreferenceScopeKey();
  if (deviceId && deviceId.length > 0) {
    prefs[scopeKey] = deviceId;
    selectedMicDeviceId = deviceId;
  } else {
    delete prefs[scopeKey];
    selectedMicDeviceId = undefined;
  }
  await chrome.storage.local.set({ [MIC_DEVICE_PREFS_KEY]: prefs });
}

async function listAudioInputDevices(): Promise<AudioInputDevice[]> {
  // Mic permission is handled by the offscreen document's getUserMedia
  // fallback in listAudioInputs() — Chrome shows its native permission
  // prompt when needed instead of opening a separate extension page.
  await ensureOffscreen();
  return new Promise<AudioInputDevice[]>((resolve) => {
    const timeout = setTimeout(() => {
      chrome.runtime.onMessage.removeListener(listener);
      resolve([]);
    }, 5000);
    const listener = (msg: any) => {
      if (msg.type !== "OFFSCREEN_AUDIO_INPUTS") return;
      clearTimeout(timeout);
      chrome.runtime.onMessage.removeListener(listener);
      resolve(Array.isArray(msg.devices) ? msg.devices : []);
    };
    chrome.runtime.onMessage.addListener(listener);
    chrome.runtime.sendMessage({ type: "OFFSCREEN_LIST_AUDIO_INPUTS" }).catch(() => {
      clearTimeout(timeout);
      chrome.runtime.onMessage.removeListener(listener);
      resolve([]);
    });
  });
}

// ─── Tab Audio: offscreen document lifecycle ───────────────────────

let offscreenCreated = false;

async function ensureOffscreen(): Promise<void> {
  if (offscreenCreated) return;
  // Check if already exists (e.g. after service worker restart)
  const existing = await (chrome.offscreen as any).hasDocument?.().catch(() => false);
  if (existing) {
    offscreenCreated = true;
    return;
  }

  // Wait for the offscreen doc to signal it's ready before returning.
  // createDocument completes when the HTML is created, but the JS may not have
  // registered its listeners yet — sending OFFSCREEN_START_RECORDING too early
  // means the message is lost and MediaRecorder never starts.
  const readyPromise = new Promise<void>((resolve) => {
    const timeout = setTimeout(resolve, 3000); // safety: don't wait forever
    const listener = (msg: any) => {
      if (msg.type === "OFFSCREEN_READY") {
        clearTimeout(timeout);
        chrome.runtime.onMessage.removeListener(listener);
        resolve();
      }
    };
    chrome.runtime.onMessage.addListener(listener);
  });

  await (chrome.offscreen as any).createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "Capture tab audio for meeting recording and play back tab audio",
  });

  await readyPromise; // wait for offscreen.ts to send OFFSCREEN_READY
  offscreenCreated = true;
}

async function closeOffscreen(): Promise<void> {
  if (!offscreenCreated) return;
  await (chrome.offscreen as any).closeDocument().catch(() => {});
  offscreenCreated = false;
}

// ─── Tab Audio: base64 → ArrayBuffer helper ──────────────────────

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

// ─── Tab Audio: chunk upload with retry ───────────────────────────

interface PendingChunk {
  seq: number;
  blob: Blob;
  startMs: number;
  durationMs: number;
  sha256: string;
  size: number;
  track?: "tab" | "mic" | "mixed";
}

const pendingChunks: PendingChunk[] = [];
let inFlightChunkUploads = 0;
let offscreenStopResolve: (() => void) | null = null;

async function waitForInFlightUploadsToDrain(timeoutMs = 15000): Promise<void> {
  const startedAt = Date.now();
  while (inFlightChunkUploads > 0) {
    if (Date.now() - startedAt > timeoutMs) {
      console.warn(`[Memoir] Timed out waiting for in-flight chunk uploads to drain (${inFlightChunkUploads} pending)`);
      return;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
}

async function uploadChunkWithRetry(chunk: PendingChunk, retries = 2): Promise<boolean> {
  if (!meetingState.meetingId || !meetingState.uploadSessionId) return false;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      await api.uploadTabAudioChunk(
        meetingState.meetingId,
        meetingState.uploadSessionId,
        chunk.seq,
        chunk.blob,
        { sha256: chunk.sha256, startMs: chunk.startMs, durationMs: chunk.durationMs, size: chunk.size, track: chunk.track }
      );
      return true;
    } catch (err) {
      const label = chunk.track ? `${chunk.track}/${chunk.seq}` : `${chunk.seq}`;
      console.warn(`[Memoir] Chunk ${label} upload attempt ${attempt + 1}/${retries + 1} failed:`, err instanceof Error ? err.message : err);
      if (attempt < retries) {
        // Exponential backoff with jitter
        await new Promise((r) => setTimeout(r, (1000 * Math.pow(2, attempt)) + Math.random() * 500));
      }
    }
  }
  return false;
}

// ─── Tab Audio: finalize flow ─────────────────────────────────────

async function finalizeTabAudio(reason: "manual_stop" | "meeting_ended" = "meeting_ended"): Promise<void> {
  if (meetingState.state !== "recording" || meetingState.recordingMode !== "tab_audio") return;

  const meetingId = meetingState.meetingId;
  const sessionId = meetingState.uploadSessionId;
  if (!meetingId || !sessionId) {
    await transitionTo("detected", { meetingId: undefined, uploadSessionId: undefined, recordingStartedAt: undefined });
    return;
  }

  // Transition to finalizing
  await transitionTo("finalizing_upload");
  if (meetingState.tabId) {
    chrome.tabs.sendMessage(meetingState.tabId, {
      type: "RECORDING_STATUS_UPDATE",
      state: "finalizing_upload",
    }).catch(() => {});
  }

  // Stop offscreen recording — wait for OFFSCREEN_STOPPED acknowledgement.
  const offscreenStoppedPromise = new Promise<void>((resolve) => {
    offscreenStopResolve = () => {
      resolve();
      offscreenStopResolve = null;
    };
    // Safety timeout: resolve after 15s even if no stop ack arrives
    setTimeout(() => {
      if (offscreenStopResolve) {
        console.warn("[Memoir] Timed out waiting for OFFSCREEN_STOPPED");
        offscreenStopResolve();
      }
    }, 15000);
  });
  chrome.runtime.sendMessage({ type: "OFFSCREEN_STOP_RECORDING" }).catch(() => {
    // If offscreen doc is gone, resolve immediately
    offscreenStopResolve?.();
    offscreenStopResolve = null;
  });

  // Wait for offscreen to finish draining/sending final chunks and for uploads to settle.
  await offscreenStoppedPromise;
  await waitForInFlightUploadsToDrain(15000);

  // Upload any chunks that failed during the recording
  if (pendingChunks.length > 0) {
    console.log(`[Memoir] Uploading ${pendingChunks.length} pending chunks`);
    for (const chunk of [...pendingChunks]) {
      const ok = await uploadChunkWithRetry(chunk, 2);
      if (ok) {
        pendingChunks.splice(pendingChunks.indexOf(chunk), 1);
      }
    }
  }

  // If too many failures, use fallback path
  if (consecutiveChunkFailures >= MAX_CHUNK_FAILURES) {
    await useFallbackComplete(meetingId, sessionId, reason);
    return;
  }

  // Scrape current DOM participants from the meeting tab so they flow
  // into POST /complete and land in meeting_participants. Without this,
  // SPEAKER_XX rows stay unnamed (witnessed 2026-05-27 — tab recording
  // completed cleanly through T7 but speakers showed as raw labels with
  // platform_display_name = NULL).
  let scrapedParticipants: Array<{ display_name: string }> = [];
  if (meetingState.tabId) {
    try {
      const reply = await chrome.tabs.sendMessage(meetingState.tabId, {
        type: "SCRAPE_PARTICIPANTS_FROM_DOM",
      });
      const payload = reply?.data;
      if (payload && Array.isArray(payload.participants)) {
        scrapedParticipants = payload.participants
          .map((p: { name?: string }) => ({ display_name: (p.name || "").trim() }))
          .filter((p: { display_name: string }) => p.display_name.length > 0);
      }
    } catch (e) {
      console.warn("[Memoir] DOM scrape failed at complete:", e);
    }
  }
  console.log(`[Memoir] Completing with ${scrapedParticipants.length} scraped participants`);

  // Complete session
  try {
    await api.completeTabAudioSession(meetingId, sessionId, captionEvents, scrapedParticipants);
    console.log(`[Memoir] Tab audio session completed for meeting ${meetingId}`);
  } catch (err) {
    console.error("[Memoir] Complete session failed, trying fallback:", err);
    await useFallbackComplete(meetingId, sessionId, reason);
    return;
  }

  // Reset tab audio state
  captionEvents = [];
  consecutiveChunkFailures = 0;
  await closeOffscreen();

  if (reason === "manual_stop") {
    // Ad-hoc recordings have no tabId — there's no meeting tab to remain
    // "detected" on, so reset all the way to idle (popup → DashboardSummary).
    // Tab-audio recordings with a real tab go back to "detected" so the
    // floating button + popup let the user start again on the same meeting.
    if (!meetingState.tabId) {
      await transitionTo("idle", {
        meetingId: undefined,
        meetingTitle: undefined,
        meetingUrl: undefined,
        platform: undefined,
        recordingStartedAt: undefined,
        recordingMode: undefined,
        uploadSessionId: undefined,
      });
    } else {
      await transitionTo("detected", {
        meetingId: undefined,
        recordingStartedAt: undefined,
        recordingMode: undefined,
        uploadSessionId: undefined,
      });
      chrome.tabs.sendMessage(meetingState.tabId, {
        type: "RECORDING_STATUS_UPDATE",
        state: "stopped",
        meetingId,
      }).catch(() => {});
    }
  } else {
    // Meeting ended — transition to completed; content script handles post-meeting CTA
    await transitionTo("completed");
    if (meetingState.tabId) {
      chrome.tabs.sendMessage(meetingState.tabId, {
        type: "RECORDING_STATUS_UPDATE",
        state: "completed",
        meetingId,
      }).catch(() => {});
    }
  }
}

async function useFallbackComplete(meetingId: string, sessionId: string, reason: "manual_stop" | "meeting_ended" = "meeting_ended"): Promise<void> {
  console.log("[Memoir] Using full-file fallback upload");
  // Request full file from offscreen (now returns dual blobs: tab + optional mic)
  const fullFile = await new Promise<{ tabBase64: string; micBase64?: string } | null>((resolve) => {
    const timeout = setTimeout(() => resolve(null), 10000);
    chrome.runtime.sendMessage({ type: "OFFSCREEN_GET_FULL_FILE" });
    const listener = (msg: any) => {
      if (msg.type === "OFFSCREEN_FULL_FILE_READY") {
        clearTimeout(timeout);
        chrome.runtime.onMessage.removeListener(listener);
        resolve({ tabBase64: msg.tabBase64, micBase64: msg.micBase64 });
      }
    };
    chrome.runtime.onMessage.addListener(listener);
  });

  if (fullFile) {
    const tabBuffer = base64ToArrayBuffer(fullFile.tabBase64);
    const tabBlob = new Blob([tabBuffer], { type: "audio/webm;codecs=opus" });

    let micBlob: Blob | undefined;
    if (fullFile.micBase64) {
      const micBuffer = base64ToArrayBuffer(fullFile.micBase64);
      micBlob = new Blob([micBuffer], { type: "audio/webm;codecs=opus" });
    }

    // Same DOM scrape as the happy path — see comment at the regular
    // completeTabAudioSession call site.
    let fallbackParticipants: Array<{ display_name: string }> = [];
    if (meetingState.tabId) {
      try {
        const reply = await chrome.tabs.sendMessage(meetingState.tabId, {
          type: "SCRAPE_PARTICIPANTS_FROM_DOM",
        });
        const payload = reply?.data;
        if (payload && Array.isArray(payload.participants)) {
          fallbackParticipants = payload.participants
            .map((p: { name?: string }) => ({ display_name: (p.name || "").trim() }))
            .filter((p: { display_name: string }) => p.display_name.length > 0);
        }
      } catch (e) {
        console.warn("[Memoir] DOM scrape failed at fallback complete:", e);
      }
    }

    try {
      await api.fallbackCompleteTabAudioSession(
        meetingId, sessionId, tabBlob, captionEvents, micBlob, fallbackParticipants,
      );
      console.log("[Memoir] Fallback complete succeeded");
    } catch (err) {
      console.error("[Memoir] Fallback complete also failed:", err);
    }
  }

  captionEvents = [];
  consecutiveChunkFailures = 0;
  await closeOffscreen();

  if (reason === "manual_stop") {
    await transitionTo("detected", {
      meetingId: undefined,
      recordingStartedAt: undefined,
      recordingMode: undefined,
      uploadSessionId: undefined,
    });
    if (meetingState.tabId) {
      chrome.tabs.sendMessage(meetingState.tabId, {
        type: "RECORDING_STATUS_UPDATE",
        state: "stopped",
        meetingId,
      }).catch(() => {});
    }
  } else {
    await transitionTo("completed");
    if (meetingState.tabId) {
      chrome.tabs.sendMessage(meetingState.tabId, {
        type: "RECORDING_STATUS_UPDATE",
        state: "completed",
        meetingId,
      }).catch(() => {});
    }
  }
}

// ─── Message handler (central hub) ────────────────────────────────

chrome.runtime.onMessage.addListener(
  (message: ExtensionMessage, sender, sendResponse) => {
    handleMessage(message, sender)
      .then(sendResponse)
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true; // async response
  }
);

async function handleMessage(
  msg: ExtensionMessage,
  sender: chrome.runtime.MessageSender
): Promise<ExtensionResponse> {
  switch (msg.type) {
    // ── State ─────────────────────────────────────────────────
    case "GET_STATE": {
      // Auto-reset "completed" when user opens the popup
      if (meetingState.state === "completed") {
        await transitionTo("idle");
      }
      // URL reconciliation: if we believe a meeting is detected but
      // the user's active tab is a DIFFERENT meeting URL, refresh
      // detection from the tab reality. Covers three otherwise-stuck
      // scenarios:
      //   1. Extension reload while user was on a Meet tab — the SW
      //      restarts and loads stale meetingState from chrome.storage
      //      before tabs.onUpdated can fire for the new URL (race with
      //      tab reload).
      //   2. User closed the old Meet tab and opened a new meeting in
      //      a different tab — different tabId means the
      //      isUrlChangeWhileDetected branch in tabs.onUpdated rejects
      //      it (it requires tabId === meetingState.tabId).
      //   3. Any case where tabs.onUpdated dropped the URL-change
      //      event (SW asleep, listener race on startup).
      // Conservative: only triggers when the active tab is itself a
      // meeting URL and differs from our cached one. We don't reset
      // to idle on "no meet tab active" because the user might just
      // be glancing at another tab while still in the meeting.
      if (meetingState.state === "detected") {
        try {
          const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
          const activeTab = tabs[0];
          if (activeTab?.id != null && activeTab.url) {
            const detected = detectMeetingFromUrl(activeTab.url);
            if (detected && activeTab.url !== meetingState.meetingUrl) {
              let meetingUrl = activeTab.url;
              if (detected.platform === "zoom") {
                meetingUrl = attachZoomPassword(activeTab.id, meetingUrl);
              }
              console.log(
                `[Memoir] GET_STATE reconcile: refreshing detection from ${meetingState.meetingUrl} to ${meetingUrl}`
              );
              await transitionTo("detected", {
                platform: detected.platform,
                meetingUrl,
                meetingTitle: detected.title,
                tabId: activeTab.id,
              });
            }
          }
        } catch (e) {
          console.warn("[Memoir] GET_STATE URL reconcile failed (non-fatal):", e);
        }
      }
      // If no auth, try to sync from an open memoir.app tab
      if (!authState) {
        proactiveAuthSync();
        // Wait briefly for auth sync to complete, then re-check
        await new Promise((r) => setTimeout(r, 500));
        authState = await loadAuth();
      }
      const state: ExtensionState = { auth: authState, meeting: meetingState };
      return { success: true, data: state };
    }

    case "GET_AUDIO_INPUT_DEVICES": {
      const devices = await listAudioInputDevices();
      const selected = selectedMicDeviceId && devices.some((d) => d.deviceId === selectedMicDeviceId)
        ? selectedMicDeviceId
        : undefined;
      if (!selected && selectedMicDeviceId) {
        // Selected device no longer present.
        await saveSelectedMicDevice(null);
      }
      return { success: true, data: { devices, selectedDeviceId: selected } };
    }


    case "GET_RECORDING_MODE_PREF": {
      return { success: true, data: { mode: selectedUploadMode } };
    }

    case "SET_SELECTED_MIC_DEVICE": {
      const requested = msg.deviceId;
      if (requested === null || requested === "") {
        await saveSelectedMicDevice(null);
        return { success: true };
      }

      const devices = await listAudioInputDevices();
      if (!devices.some((d) => d.deviceId === requested)) {
        return { success: false, error: "Selected microphone is not available" };
      }

      await saveSelectedMicDevice(requested);
      return { success: true };
    }

    case "SET_RECORDING_MODE_PREF": {
      const mode = msg.mode === "mixed" ? "mixed" : "separate";
      await saveSelectedUploadMode(mode);
      return { success: true };
    }

    // ── Auth ──────────────────────────────────────────────────
    case "LOGIN": {
      const result = await api.login(msg.email, msg.password);
      const team = result.teams[0];
      authState = {
        accessToken: result.access_token,
        user: result.user,
        activeTeamId: team?.id ?? "",
        activeTeamName: team?.name,
      };
      await saveAuth(authState);
      selectedMicDeviceId = await loadSelectedMicDevice();
      selectedUploadMode = await loadSelectedUploadMode();
      updateBadge();
      // Sync to web
      syncAuthToWeb(authState);
      return { success: true, data: authState };
    }

    case "LOGOUT": {
      authState = null;
      selectedMicDeviceId = undefined;
      selectedUploadMode = "separate";
      await clearAuth();
      meetingState = IDLE_STATE;
      await saveMeetingState(meetingState);
      stopPolling();
      updateBadge();
      return { success: true };
    }

    case "AUTH_SYNC_FROM_WEB": {
      authState = {
        accessToken: msg.accessToken,
        user: msg.user,
        activeTeamId: msg.activeTeamId,
        activeTeamName: msg.activeTeamName,
      };
      await saveAuth(authState);
      selectedMicDeviceId = await loadSelectedMicDevice();
      selectedUploadMode = await loadSelectedUploadMode();
      updateBadge();
      return { success: true };
    }

    // ── Meeting detection ─────────────────────────────────────
    case "MEETING_STATE_CHANGE": {
      if (msg.state === "pre-join") {
        // Pre-join: do nothing — user hasn't joined the meeting yet
        return { success: true, data: meetingState };
      }
      // Use the real tab ID from the sender, not the hardcoded msg.tabId
      const realTabId = sender.tab?.id ?? msg.tabId;

      // For Zoom: attach captured password if missing
      // For Teams: override the meeting URL with the one captured by webNavigation
      let resolvedMeetingUrl = msg.meetingUrl;
      if (msg.platform === "zoom" && realTabId) {
        resolvedMeetingUrl = attachZoomPassword(realTabId, resolvedMeetingUrl);
      }
      if (msg.platform === "teams") {
        // 1. Try in-memory Map (per-tab, set by webNavigation listeners)
        const captured = realTabId ? capturedMeetingUrls.get(realTabId) : null;
        if (captured) {
          console.log(`[Memoir] Using captured Teams URL (map): ${captured}`);
          resolvedMeetingUrl = captured;
        } else if (/\/v2\/?(\?.*)?$/.test(resolvedMeetingUrl)) {
          // 2. Fallback to chrome.storage.local (survives service worker restart)
          const stored = await chrome.storage.local.get(TEAMS_URL_STORAGE_KEY);
          if (stored[TEAMS_URL_STORAGE_KEY]) {
            console.log(`[Memoir] Using captured Teams URL (storage): ${stored[TEAMS_URL_STORAGE_KEY]}`);
            resolvedMeetingUrl = stored[TEAMS_URL_STORAGE_KEY];
          }
        }
      }

      if (msg.state === "in-meeting") {
        if (meetingState.state === "idle" || meetingState.state === "completed") {
          await transitionTo("detected", {
            platform: msg.platform,
            meetingUrl: resolvedMeetingUrl,
            meetingTitle: msg.meetingTitle,
            tabId: realTabId,
          });
        } else if (
          // Switch to a NEW meeting tab. Production bug 2026-05-14:
          // user opens Tab A (state="detected", URL=meet/A), then opens
          // Tab B with a different meeting. Without this branch the SW
          // stays pointed at Tab A and clicking Record on Tab B's
          // floating button dispatched the bot to meet/A. Now if a
          // different tab reports a different URL while we're still in
          // "detected" (haven't started recording), the new tab wins.
          meetingState.state === "detected" &&
          realTabId !== meetingState.tabId &&
          resolvedMeetingUrl !== meetingState.meetingUrl
        ) {
          console.log(
            `[Memoir] Switching active meeting from tab ${meetingState.tabId} ` +
            `(${meetingState.meetingUrl}) to tab ${realTabId} (${resolvedMeetingUrl})`
          );
          meetingState.platform = msg.platform;
          meetingState.meetingUrl = resolvedMeetingUrl;
          meetingState.meetingTitle = msg.meetingTitle;
          meetingState.tabId = realTabId;
          await saveMeetingState(meetingState);
        } else if (
          // Same-tab URL refinement: e.g., Zoom pwd= appearing on the
          // initially-bare URL. Only fires within the same tab.
          meetingState.tabId === realTabId &&
          (meetingState.state === "detected" || meetingState.state === "joining") &&
          resolvedMeetingUrl.includes("pwd=") &&
          !(meetingState.meetingUrl?.includes("pwd="))
        ) {
          console.log(`[Memoir] Updated meeting URL from content script: ${resolvedMeetingUrl}`);
          meetingState.meetingUrl = resolvedMeetingUrl;
          await saveMeetingState(meetingState);
        }
      } else if (msg.state === "ended") {
        if (meetingState.state === "recording") {
          if (meetingState.recordingMode === "tab_audio") {
            await finalizeTabAudio();
          } else {
            await transitionTo("completed");
            stopPolling();
            if (meetingState.tabId) {
              chrome.tabs.sendMessage(meetingState.tabId, {
                type: "RECORDING_STATUS_UPDATE",
                state: "completed",
                meetingId: meetingState.meetingId,
              }).catch(() => {});
            }
          }
        } else if (meetingState.state === "joining") {
          await transitionTo("completed");
          stopPolling();
          if (meetingState.tabId) {
            chrome.tabs.sendMessage(meetingState.tabId, {
              type: "RECORDING_STATUS_UPDATE",
              state: "completed",
              meetingId: meetingState.meetingId,
            }).catch(() => {});
          }
        } else if (meetingState.state === "detected") {
          await transitionTo("idle");
        }
      }
      return { success: true, data: meetingState };
    }

    // ── Tab Audio Recording ───────────────────────────────────
    case "START_RECORDING_TAB_AUDIO": {
      if (!authState) return { success: false, error: "Not logged in" };
      if (meetingState.state !== "detected") {
        return { success: false, error: `Cannot start recording in ${meetingState.state} state` };
      }
      if (!meetingState.tabId) {
        return { success: false, error: "No meeting tab identified" };
      }

      // Brief joining transition
      await transitionTo("joining");

      try {
        const effectiveUploadMode: UploadAudioMode = msg.uploadMode === "mixed"
          ? "mixed"
          : (msg.uploadMode === "separate" ? "separate" : selectedUploadMode);
        const effectiveMicDeviceId = typeof msg.micDeviceId === "string" && msg.micDeviceId
          ? msg.micDeviceId
          : selectedMicDeviceId;
        if (effectiveMicDeviceId) {
          await saveSelectedMicDevice(effectiveMicDeviceId);
        }
        await saveSelectedUploadMode(effectiveUploadMode);

        // Get tab capture stream ID
        const streamId = await new Promise<string>((resolve, reject) => {
          (chrome.tabCapture as any).getMediaStreamId(
            { targetTabId: meetingState.tabId },
            (id: string) => {
              if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message ?? "Tab capture denied"));
              } else {
                resolve(id);
              }
            }
          );
        });

        // Create meeting record (no bot dispatched)
        const meeting = await api.createMeeting({
          meeting_url: meetingState.meetingUrl!,
          title: meetingState.meetingTitle,
          team_id: authState.activeTeamId,
          source: "extension_tab_audio",
          capture_mode: "tab_audio",
        });

        // Create upload session
        const { session_id } = await api.createTabAudioSession(meeting.id);

        // Create offscreen document and start recording
        await ensureOffscreen();
        chrome.runtime.sendMessage({
          type: "OFFSCREEN_START_RECORDING",
          streamId,
          micDeviceId: effectiveMicDeviceId,
          uploadMode: effectiveUploadMode,
        }).catch(() => {});

        // Reset tab audio state
        captionEvents = [];
        consecutiveChunkFailures = 0;

        // Transition to recording
        await transitionTo("recording", {
          meetingId: meeting.id,
          recordingMode: "tab_audio",
          uploadSessionId: session_id,
          recordingStartedAt: Date.now(),
        });

        // Notify content script
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "recording",
            mode: "tab_audio",
            meetingId: meeting.id,
          }).catch(() => {});
        }

        return { success: true, data: { meetingId: meeting.id, sessionId: session_id } };
      } catch (err: unknown) {
        const tabId = meetingState.tabId;
        await transitionTo("detected");
        await closeOffscreen();
        // Reset floating button back to "record" state
        if (tabId) {
          chrome.tabs.sendMessage(tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "stopped",
          }).catch(() => {});
        }
        const message = err instanceof Error ? err.message : "Failed to start tab audio recording";
        return { success: false, error: message };
      }
    }

    // ── Web-recorder handoff: list open meeting tabs ─────────────
    case "WEB_RECORDER_LIST_MEETING_TABS": {
      try {
        const tabs = await chrome.tabs.query({
          url: [
            "https://meet.google.com/*",
            "https://*.zoom.us/*",
            "https://zoom.us/*",
            "https://teams.microsoft.com/*",
            "https://teams.live.com/*",
          ],
        });
        const meetingTabs: MeetingTabInfo[] = [];
        for (const t of tabs) {
          if (!t.id || !t.url) continue;
          const detected = detectMeetingFromUrl(t.url);
          if (!detected) continue;
          meetingTabs.push({
            tabId: t.id,
            url: t.url,
            title: t.title ?? detected.title,
            favIconUrl: t.favIconUrl,
            platform: detected.platform,
          });
        }
        return { success: true, data: { tabs: meetingTabs } };
      } catch (err) {
        return { success: false, error: (err as Error).message || "Tab query failed" };
      }
    }

    // ── Web-recorder handoff: start recording a chosen tab ───────
    // The page on app.meetmemoir.com/record picked a tab. We mirror the
    // logic of START_RECORDING_TAB_AUDIO but use msg.tabId instead of
    // meetingState.tabId (which may be unset when the user is on /record,
    // not on the meeting tab itself).
    case "WEB_RECORDER_HANDOFF_START": {
      if (!authState) return { success: false, error: "Not logged in" };
      if (
        meetingState.state === "recording" ||
        meetingState.state === "joining" ||
        meetingState.state === "starting_to_record"
      ) {
        return {
          success: false,
          error: `A recording is already in progress (${meetingState.state})`,
        };
      }
      const requestedTabId = Number(msg.tabId);
      if (!Number.isFinite(requestedTabId)) {
        return { success: false, error: "Invalid tabId" };
      }

      let tab: chrome.tabs.Tab;
      try {
        tab = await chrome.tabs.get(requestedTabId);
      } catch {
        return {
          success: false,
          error: "Selected meeting tab is no longer open",
        };
      }
      if (!tab.url) {
        return { success: false, error: "Selected tab has no URL" };
      }
      const detected = detectMeetingFromUrl(tab.url);
      if (!detected) {
        return {
          success: false,
          error: "Selected tab is not a recognized meeting platform",
        };
      }

      try {
        const effectiveMicDeviceId = selectedMicDeviceId;
        const effectiveUploadMode = selectedUploadMode;

        const streamId = await new Promise<string>((resolve, reject) => {
          (chrome.tabCapture as unknown as {
            getMediaStreamId: (
              opts: { targetTabId: number },
              cb: (id: string) => void,
            ) => void;
          }).getMediaStreamId({ targetTabId: requestedTabId }, (id) => {
            if (chrome.runtime.lastError) {
              reject(
                new Error(
                  chrome.runtime.lastError.message ?? "Tab capture denied",
                ),
              );
            } else {
              resolve(id);
            }
          });
        });

        // Seed meetingState so the rest of the pipeline (chunk uploads,
        // finalize, popup state, floating button) treats this as the
        // active meeting. transitionTo("recording", ...) below sets the
        // remaining fields.
        meetingState = {
          ...meetingState,
          state: "joining",
          platform: detected.platform,
          meetingUrl: tab.url,
          meetingTitle: msg.title || tab.title || detected.title,
          tabId: requestedTabId,
        };

        const meeting = await api.createMeeting({
          meeting_url: tab.url,
          title: msg.title || tab.title || detected.title,
          team_id: authState.activeTeamId,
          source: "extension_tab_audio",
          capture_mode: "tab_audio",
        });

        const { session_id } = await api.createTabAudioSession(meeting.id);

        await ensureOffscreen();
        chrome.runtime
          .sendMessage({
            type: "OFFSCREEN_START_RECORDING",
            streamId,
            micDeviceId: effectiveMicDeviceId,
            uploadMode: effectiveUploadMode,
          })
          .catch(() => {});

        captionEvents = [];
        consecutiveChunkFailures = 0;

        await transitionTo("recording", {
          meetingId: meeting.id,
          recordingMode: "tab_audio",
          uploadSessionId: session_id,
          recordingStartedAt: Date.now(),
        });

        // Notify the meeting tab's content script so its floating button
        // flips to "Recording" — same path the existing extension flow uses.
        chrome.tabs
          .sendMessage(requestedTabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "recording",
            mode: "tab_audio",
            meetingId: meeting.id,
          })
          .catch(() => {});

        return {
          success: true,
          data: { meetingId: meeting.id, sessionId: session_id },
        };
      } catch (err) {
        await transitionTo("idle");
        await closeOffscreen();
        const message =
          err instanceof Error ? err.message : "Failed to start handoff recording";
        return { success: false, error: message };
      }
    }

    // ── Ad-hoc recording (mic only, no meeting tab) ──────────────
    // Used by:
    //   - Popup "Start recording — Record an ad-hoc conversation" CTA
    //   - /record page when extension is installed and 0 meeting tabs are open
    // Both surfaces converge here so the extension is always the recorder
    // when installed (option (b) per UX plan).
    case "START_RECORDING_AD_HOC": {
      if (!authState) return { success: false, error: "Not logged in" };
      if (
        meetingState.state === "recording" ||
        meetingState.state === "joining" ||
        meetingState.state === "starting_to_record"
      ) {
        return {
          success: false,
          error: `A recording is already in progress (${meetingState.state})`,
        };
      }

      try {
        const effectiveMicDeviceId = selectedMicDeviceId;
        // Ad-hoc uses mixed mode (single source = mic) — same shape the
        // /record web-recorder originally used.
        const effectiveUploadMode: UploadAudioMode = "mixed";

        // Create the meeting with extension_tab_audio source — the badge
        // logic on /meetings collapses both extension paths (with-tab and
        // ad-hoc) under "Extension Recording". No meeting URL for ad-hoc.
        const meeting = await api.createMeeting({
          title: msg.title || undefined,
          team_id: authState.activeTeamId,
          source: "extension_tab_audio",
          capture_mode: "tab_audio",
        });
        const { session_id } = await api.createTabAudioSession(meeting.id);

        // Open offscreen with streamId=null → mic-only path inside offscreen.ts.
        await ensureOffscreen();
        chrome.runtime
          .sendMessage({
            type: "OFFSCREEN_START_RECORDING",
            streamId: null,
            micDeviceId: effectiveMicDeviceId,
            uploadMode: effectiveUploadMode,
          })
          .catch(() => {});

        captionEvents = [];
        consecutiveChunkFailures = 0;

        // Seed meetingState — no platform / no meeting URL / no tabId.
        meetingState = {
          ...meetingState,
          state: "joining",
          platform: undefined,
          meetingUrl: undefined,
          meetingTitle: msg.title || "Voice note",
          tabId: undefined,
        };

        await transitionTo("recording", {
          meetingId: meeting.id,
          recordingMode: "tab_audio",
          uploadSessionId: session_id,
          recordingStartedAt: Date.now(),
        });

        return {
          success: true,
          data: { meetingId: meeting.id, sessionId: session_id },
        };
      } catch (err) {
        await transitionTo("idle");
        await closeOffscreen();
        const message =
          err instanceof Error ? err.message : "Failed to start ad-hoc recording";
        return { success: false, error: message };
      }
    }

    // ── Cross-surface recording state (bridge from /record page) ──
    // Page polls this every 1s so its timer freezes the moment the
    // popup stops the recording — backend status only flips to
    // waiting_for_transcription after the upload completes (~5s), which
    // would otherwise leave the page UI ticking during that window.
    case "WEB_RECORDER_GET_RECORDING_STATE": {
      return {
        success: true,
        data: {
          state: meetingState.state,
          meetingId: meetingState.meetingId ?? null,
          recordingStartedAt: meetingState.recordingStartedAt ?? null,
        },
      };
    }

    // ── Mic device sync (bridge from /record page) ───────────────
    case "WEB_RECORDER_GET_MIC_DEVICE": {
      return {
        success: true,
        data: { deviceId: selectedMicDeviceId ?? null },
      };
    }
    case "WEB_RECORDER_SET_MIC_DEVICE": {
      try {
        await saveSelectedMicDevice(msg.deviceId);
        return { success: true };
      } catch (err) {
        return {
          success: false,
          error: err instanceof Error ? err.message : "Failed to save mic device",
        };
      }
    }

    // ── Web-recorder handoff: stop ───────────────────────────────
    case "WEB_RECORDER_HANDOFF_STOP": {
      if (
        meetingState.recordingMode !== "tab_audio" ||
        meetingState.state !== "recording"
      ) {
        return {
          success: false,
          error: `No active tab-audio recording (state=${meetingState.state})`,
        };
      }
      if (
        msg.meetingId &&
        meetingState.meetingId &&
        msg.meetingId !== meetingState.meetingId
      ) {
        return { success: false, error: "Meeting ID mismatch" };
      }
      // Fire-and-forget — matches the existing STOP_RECORDING pattern for tab_audio.
      finalizeTabAudio("manual_stop").catch((err) =>
        console.error("[Memoir] Handoff stop error:", err),
      );
      return { success: true, data: { ok: true } };
    }

    // ── Instant admission signal from the host's Meet tab ──────
    // Content script saw the host click an "Admit" / "Admit all"
    // button whose surrounding notification mentions our bot's name.
    // Drive the same joining -> starting_to_record + 1.5 s timer
    // transition that the poll-based path runs, ~3-5 s earlier.
    //
    // The poll-based path in pollMeetingStatus() stays in place as a
    // fallback for auto-admit, keyboard-shortcut admit, and any case
    // where the click matcher misses. Both paths converge on the same
    // target state, and the poll's transition is guarded by
    // `state === "joining"` so it short-circuits cleanly when this
    // handler already moved us to starting_to_record.
    case "BOT_ADMITTED_LOCALLY": {
      if (meetingState.state !== "joining") {
        return { success: true };
      }
      // Step 1 — instant UI flip to "Starting to record..." for fast feedback.
      await transitionTo("starting_to_record");
      if (meetingState.tabId) {
        chrome.tabs.sendMessage(meetingState.tabId, {
          type: "RECORDING_STATUS_UPDATE",
          state: "starting_to_record",
          mode: "bot",
        }).catch(() => {});
      }

      // Step 2 — Phase 2 fast-path. Fire the admit-detected webhook so
      // backend commits status="recording" + recording_started_at without
      // waiting for the bot's own admission detection (which takes 1-8 s).
      // With Phase 1's pre-started FFmpeg the bot is already capturing
      // audio by the time admission lands, so this is safe to flip
      // immediately. Idempotent on the backend side — if the bot's
      // webhook arrives first, this is a no-op. If THIS fires first
      // and the bot fails to detect admission, bot_error eventually
      // fires and the poll handler reverts the UI.
      //
      // Fire-and-forget: the poll handler will see recording_started_at
      // populated within ~1 s on next poll and flip to "Stop Recording".
      // Network failures are absorbed by the api-client retry; if all
      // retries fail, the bot's own webhook path takes over (slower but
      // working — today's behavior).
      if (meetingState.meetingId) {
        api.notifyAdmitDetected(meetingState.meetingId).catch((err) => {
          console.warn("[Memoir] admit-detected webhook failed (bot path will take over):", err);
        });
      }
      return { success: true };
    }

    // ── Bot Recording (original START_RECORDING, now renamed) ─
    case "START_RECORDING_BOT":
    case "START_RECORDING": {
      // The floating button now flips to "Joining..." optimistically on
      // click (see floating-button.ts handleClick). The two early-return
      // guards below used to return silently — fine when the content
      // script only flipped *after* hearing back from us, but with the
      // optimistic flip a silent early-return leaves the button stuck
      // on "Joining..." forever. Send a "stopped" status update on
      // either guard so the floating button reverts to "Record".
      const _revertFloatingButton = () => {
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "stopped",
            meetingId: undefined,
          }).catch(() => {});
        }
      };

      if (!authState) {
        _revertFloatingButton();
        return { success: false, error: "Not logged in" };
      }
      if (meetingState.state !== "detected") {
        _revertFloatingButton();
        return { success: false, error: `Cannot start recording in ${meetingState.state} state` };
      }

      await transitionTo("joining");
      startPolling();

      try {
        const meeting = await api.createMeeting({
          meeting_url: meetingState.meetingUrl!,
          title: meetingState.meetingTitle,
          team_id: authState.activeTeamId,
          source: "extension",
        });

        meetingState.meetingId = meeting.id;
        meetingState.recordingMode = "bot";
        await saveMeetingState(meetingState);

        // Notify content script
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "joining",
            meetingId: meeting.id,
          }).catch(() => {});
        }

        return { success: true, data: { meetingId: meeting.id } };
      } catch (err: unknown) {
        await transitionTo("detected");
        stopPolling();
        // Notify content script so the floating button reverts to
        // 'Record with Memoir'. Required because handleClick() now
        // optimistically flips to 'Joining...' immediately on click —
        // without this, a failed createMeeting / startBot would leave
        // the button stuck on 'Joining...' forever.
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "stopped",
            meetingId: undefined,
          }).catch(() => {});
        }
        const message = err instanceof Error ? err.message : "Failed to start recording";
        return { success: false, error: message };
      }
    }

    case "STOP_RECORDING": {
      if (meetingState.recordingMode === "tab_audio" && meetingState.state === "recording") {
        // Tab audio stop — trigger finalize flow (async, non-blocking response)
        finalizeTabAudio("manual_stop").catch((err) => console.error("[Memoir] Tab audio finalize error:", err));
        return { success: true };
      }

      // Bot recording stop. The button does NOT flip optimistically here;
      // instead it stays on "Stop Recording" until the polling loop sees
      // meeting.status="processing" and flips it then — which only happens
      // after the bot has actually been ejected and core_api has committed
      // the status. This avoids the UI claiming the bot is gone while it's
      // still merging/uploading, and matches the user's mental model of
      // "button changes when bot is ejected".
      const stoppedMeetingId = meetingState.meetingId;

      // Fire the stop API call in the background. core_api commits
      // status="processing" synchronously (see meetings.py:stop_meeting)
      // and fires the orchestrator stop in its own background task, so
      // even though we don't await here, the next extension poll picks
      // up the status flip immediately.
      if (stoppedMeetingId) {
        api.stopMeeting(stoppedMeetingId).catch((err) => {
          console.warn("[Memoir] Stop API failed:", err);
        });
      }

      // Resume fast polling — we stopped it on entering steady-state
      // recording to avoid the 1.5s status spam. Now that the user
      // initiated a stop, we need the poll loop running again so the
      // floating button flips back the moment core_api reports
      // status="processing". Setting stopRequestedAt makes
      // pickPollInterval() return POLL_FAST_MS regardless of the
      // still-"recording" local state (the previous version called
      // applyPollCadence which returned null and silently stopped
      // polling, leaving the user waiting for the 30 s chrome.alarms
      // backup).
      meetingState = { ...meetingState, stopRequestedAt: Date.now() };
      saveMeetingState(meetingState).catch(() => {});
      startFastPolling();

      // Polling now running. No transitionTo here. No "stopped" message
      // sent to content script. The poll handler in pollMeetingStatus
      // flips the button to "Record with Memoir" when it observes the
      // backend status change — see the "processing" branch there.
      return { success: true };
    }

    // ── Tab Audio: chunk from offscreen doc ───────────────────
    case "TAB_AUDIO_CHUNK_READY":
    case "TAB_AUDIO_FINAL_CHUNK": {
      if (!meetingState.meetingId || !meetingState.uploadSessionId) {
        return { success: true };
      }

      const track = msg.track as "tab" | "mic" | "mixed";
      const audioBuffer = base64ToArrayBuffer(msg.base64Audio);
      const blob = new Blob([audioBuffer], { type: "audio/webm;codecs=opus" });
      const chunk: PendingChunk = {
        seq: msg.seq,
        blob,
        startMs: msg.startMs,
        durationMs: msg.durationMs,
        sha256: msg.sha256,
        size: msg.size,
        track,
      };

      inFlightChunkUploads++;
      try {
        const ok = await uploadChunkWithRetry(chunk, 2);
        if (ok) {
          consecutiveChunkFailures = 0;
          console.log(`[Memoir] Chunk ${track}/${msg.seq} uploaded OK (${msg.size} bytes)`);
        } else {
          consecutiveChunkFailures++;
          pendingChunks.push(chunk);
          console.warn(`[Memoir] Chunk ${track}/${msg.seq} upload failed after retries (failure #${consecutiveChunkFailures}, size=${msg.size})`);

          if (consecutiveChunkFailures >= MAX_CHUNK_FAILURES) {
            console.warn("[Memoir] Too many chunk failures — will use fallback on stop");
          }
        }
      } finally {
        inFlightChunkUploads = Math.max(0, inFlightChunkUploads - 1);
      }

      return { success: true };
    }

    case "OFFSCREEN_STOPPED": {
      offscreenStopResolve?.();
      offscreenStopResolve = null;
      return { success: true };
    }

    case "OFFSCREEN_AUDIO_INPUTS": {
      return { success: true };
    }

    // ── Caption speaker events ─────────────────────────────────
    case "ACTIVE_SPEAKER_EVENT": {
      captionEvents.push({
        speaker: msg.speaker,
        start: msg.start,
        end: msg.end,
      });
      return { success: true };
    }

    // ── Notes ─────────────────────────────────────────────────
    case "SAVE_NOTES": {
      if (!meetingState.meetingId) return { success: false, error: "No active meeting" };
      try {
        await api.saveNotes(meetingState.meetingId, msg.content);
        return { success: true };
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Failed to save notes";
        return { success: false, error: message };
      }
    }

    case "GET_NOTES": {
      if (!meetingState.meetingId) return { success: true, data: { content: "" } };
      try {
        const notes = await api.getNotes(meetingState.meetingId);
        return { success: true, data: notes };
      } catch {
        return { success: true, data: { content: "" } };
      }
    }

    // ── Dashboard ─────────────────────────────────────────────
    case "GET_DASHBOARD_STATS": {
      if (!authState) return { success: false, error: "Not logged in" };
      try {
        const stats = await api.getDashboardStats(authState.activeTeamId);
        return { success: true, data: stats };
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Failed to get stats";
        return { success: false, error: message };
      }
    }

    case "GET_NEXT_MEETING": {
      if (!authState) return { success: false, error: "Not logged in" };
      try {
        const next = await api.getNextMeeting(authState.activeTeamId);
        return { success: true, data: next };
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Failed to get next meeting";
        return { success: false, error: message };
      }
    }

    case "GET_MEETING_PARTICIPANTS": {
      // Source: DOM of the meeting page (NOT the backend). The user wants
      // the *current* in-call attendees, which means asking the content
      // script that's already injected in the meeting tab to scrape names
      // from the Meet / Zoom / Teams DOM and reply.
      //
      // Content script reply shape: { data: { participants: [], count: N } }.
      // The popup (MeetingDetected.tsx + RecordingStatus.tsx) expects the
      // SAME shape via res.data → must pass `reply.data` through as an
      // object, not coerce to an array. The old Array.isArray() check
      // discarded the participants and made the popup show "Participant
      // list will appear once you join the meeting" even when count > 0.
      const tabId = meetingState.tabId;
      const empty = { participants: [], count: null as number | null };
      if (!tabId) return { success: true, data: empty };
      try {
        const reply = await chrome.tabs.sendMessage(tabId, {
          type: "SCRAPE_PARTICIPANTS_FROM_DOM",
        });
        const payload = reply?.data;
        if (payload && typeof payload === "object" && Array.isArray(payload.participants)) {
          return { success: true, data: payload };
        }
        return { success: true, data: empty };
      } catch {
        return { success: true, data: empty };
      }
    }

    // ── Dismiss detected meeting ──────────────────────────────
    case "DISMISS_MEETING": {
      if (meetingState.state === "detected") {
        await transitionTo("idle");
      }
      return { success: true };
    }

    // ── Offscreen doc messages (no response needed) ───────────
    case "TAB_AUDIO_ERROR": {
      console.error(`[Memoir] Tab audio error: ${msg.error}`);
      // If recording, revert to detected so user can retry
      if (meetingState.state === "recording" && meetingState.recordingMode === "tab_audio") {
        await closeOffscreen();
        await transitionTo("detected", {
          meetingId: undefined,
          recordingStartedAt: undefined,
          recordingMode: undefined,
          uploadSessionId: undefined,
        });
        if (meetingState.tabId) {
          chrome.tabs.sendMessage(meetingState.tabId, {
            type: "RECORDING_STATUS_UPDATE",
            state: "stopped",
          }).catch(() => {});
        }
      }
      return { success: true };
    }

    default:
      return { success: false, error: "Unknown message type" };
  }
}

// ─── Proactive auth sync ─────────────────────────────────────────
// Inject content script into existing memoir.app tabs to trigger auth sync.
// Needed when: extension is freshly installed/updated while user is already
// logged in on the web, or when popup opens with no auth.

async function proactiveAuthSync() {
  const patterns = [`${WEB_BASE}/*`];
  try {
    const tabs = await chrome.tabs.query({ url: patterns });
    for (const tab of tabs) {
      if (tab.id) {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ["content-memoir-app.js"],
        }).catch(() => {}); // ignore if already injected or tab closed
      }
    }
  } catch {
    // scripting API may not be available
  }
}

// ─── Install handler ──────────────────────────────────────────────

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install" || details.reason === "update") {
    console.log(`[Memoir] Extension ${details.reason}`);
    // Inject content script into existing memoir.app tabs to pick up auth
    proactiveAuthSync();
    // Scan existing tabs for meetings and inject content scripts
    injectIntoExistingMeetingTabs();
  }
});

async function injectIntoExistingMeetingTabs() {
  try {
    const tabs = await chrome.tabs.query({ url: [
      "https://meet.google.com/*",
      "https://*.zoom.us/*",
      "https://zoom.us/*",
      "https://teams.microsoft.com/*",
      "https://teams.live.com/*",
    ]});
    for (const tab of tabs) {
      if (!tab.id || !tab.url) continue;
      const detected = detectMeetingFromUrl(tab.url);
      if (detected) {
        console.log(`[Memoir] Found existing meeting tab: ${tab.url}`);
        if (meetingState.state === "idle" || meetingState.state === "completed") {
          await transitionTo("detected", {
            platform: detected.platform,
            meetingUrl: tab.url,
            meetingTitle: detected.title,
            tabId: tab.id,
          });
        }
        const file = `content-${detected.platform === "google_meet" ? "meet" : detected.platform}.js`;
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: [file],
        }).catch(() => {});
      }
    }
  } catch {
    // tabs.query may fail if permissions not yet granted
  }
}
