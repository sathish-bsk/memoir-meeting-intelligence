import { sendMessage } from "../../shared/messaging";
import { FloatingButton } from "../common/floating-button";
import { PostMeetingCTA } from "../common/post-meeting-cta";
import { CaptionScraper } from "../common/caption-scraper";

interface ScrapedParticipant { name: string; isSelf?: boolean }
interface ParticipantsPayload { participants: ScrapedParticipant[]; count: number | null }

/** Mirrors ZoomBot.get_participant_count() — button aria-label / inner
 *  text → /(\d+)/, falls back to scanning all buttons for "participant"
 *  in label, then DOM item counts. */
function getZoomParticipantCount(): number | null {
  for (const sel of [
    'button[aria-label*="Participants" i]',
    'button[aria-label*="participant" i]',
  ]) {
    const btn = document.querySelector<HTMLElement>(sel);
    if (!btn) continue;
    const label = btn.getAttribute("aria-label") || "";
    const m1 = label.match(/(\d+)/);
    if (m1) return parseInt(m1[1], 10);
    const m2 = (btn.innerText || "").match(/(\d+)/);
    if (m2) return parseInt(m2[1], 10);
  }
  // Generic button sweep
  for (const btn of Array.from(document.querySelectorAll<HTMLElement>("button"))) {
    const label = (btn.getAttribute("aria-label") || "").toLowerCase();
    if (label.includes("participant")) {
      const m = label.match(/(\d+)/) || (btn.innerText || "").match(/(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
  }
  // Item-count fallbacks
  for (const sel of [".participants-item", '[class*="participant-item"]']) {
    const n = document.querySelectorAll(sel).length;
    if (n > 0) return n;
  }
  return null;
}

/**
 * Zoom participants — mirrors BotWorker zoom_bot.py:398-426
 * (get_participant_names). Reads the panel observer's accumulated set
 * if present, else falls back to direct DOM scrape of the same
 * selectors the bot uses.
 *
 * Selectors:
 *   .participants-li                             — roster li with aria-label
 *                                                  "Name (Host)" — regex ^([^(,]+)
 *   .participants-item__display-name             — alt roster name span
 *   captionScraper.getSeenSpeakers()             — session speaker history
 *
 * Length cap (< 100) matches the bot's check.
 */
function getZoomParticipants(): ParticipantsPayload {
  const out = new Map<string, ScrapedParticipant>();
  const add = (name: string | null | undefined, source: string) => {
    const t = (name || "").trim();
    if (!t || t.length >= 100) return;
    if (!out.has(t)) {
      out.set(t, { name: t });
      console.log(`[Memoir Zoom] +${t} via ${source}`);
    }
  };

  // 1. Observer-injected accumulated set, when BotWorker-style observer
  //    is running. Not normally present in user-browser, but if a
  //    future popup-injected observer is added it'll populate this.
  const injected = (window as unknown as { __memoirParticipants?: string[] }).__memoirParticipants;
  if (injected && injected.length > 0) {
    injected.forEach((n) => add(n, "__memoirParticipants"));
  }

  // 2. Direct DOM — same fallback the bot uses
  document.querySelectorAll<HTMLElement>(".participants-li").forEach((el) => {
    const label = el.getAttribute("aria-label") || "";
    const match = label.match(/^([^(,]+)/);
    if (match) add(match[1].trim(), ".participants-li aria-label");
  });
  document.querySelectorAll<HTMLElement>(".participants-item__display-name").forEach((el) => {
    add(el.innerText, ".participants-item__display-name");
  });

  // 3. Speaker-map history (caption-driven)
  (captionScraper?.getSeenSpeakers() ?? []).forEach((n) => add(n, "speaker-map"));

  const participants = Array.from(out.values());
  const count = getZoomParticipantCount();
  console.log("[Memoir Zoom] participants:", participants, "count:", count);
  return { participants, count };
}
import type { Platform } from "../../shared/types";

const PLATFORM: Platform = "zoom";
const ZOOM_URL_PATTERN = /zoom\.us\/(?:wc|j|s|w|meeting)\/(\d+)/;

let currentState: "idle" | "pre-join" | "in-meeting" | "ended" = "idle";
let floatingButton: FloatingButton | null = null;
let postCTA: PostMeetingCTA | null = null;
let activeMeetingId: string | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let lastSentUrl: string | null = null; // Track last URL sent to background
let captionScraper: CaptionScraper | null = null;
let captionObserver: MutationObserver | null = null;

/** Quick check: are we on an active Zoom web client meeting URL?
 *  Only /wc/DIGITS is an active meeting.
 *  /s/ and /j/ are landing/launch pages ("Join from browser" / "Join from app"). */
function isZoomMeetingUrl(): boolean {
  return /\/wc\/\d+/.test(window.location.pathname);
}

/** Check if we're on the Zoom pre-join / waiting screen */
function isPreJoinScreen(): boolean {
  // Only on actual meeting URLs — not the Zoom home/dashboard
  if (!isZoomMeetingUrl()) return false;

  // Zoom shows a preview screen before joining with camera/mic options
  const previewContainer =
    document.querySelector(".preview-container") ||
    document.querySelector(".preview-video") ||
    document.querySelector('[class*="preview"]');

  if (previewContainer) {
    const joinBtn =
      document.querySelector('button[aria-label="Join" i]') ||
      document.querySelector('button[aria-label="Join Audio by Computer" i]') ||
      document.querySelector('button[aria-label="Join Meeting" i]');
    if (joinBtn) return true;
  }

  const allText = document.body?.innerText?.toLowerCase() ?? "";
  if (
    allText.includes("join from your browser") ||
    allText.includes("launch meeting")
  ) {
    return true;
  }

  return false;
}

function detectState(): "pre-join" | "in-meeting" | "ended" | null {
  if (!document.body) return null;

  // Skip detection entirely on non-meeting Zoom pages (home, settings, etc.)
  if (!isZoomMeetingUrl()) return null;

  const allText = document.body.innerText?.toLowerCase() ?? "";

  // Check for ended state first (text-based — works across all Zoom versions)
  if (
    allText.includes("meeting has been ended") ||
    allText.includes("meeting has ended") ||
    allText.includes("this meeting has been ended") ||
    allText.includes("host has ended") ||
    allText.includes("meeting ended") ||
    allText.includes("you have been removed from this meeting")
  ) {
    return "ended";
  }

  // Check for pre-join before in-meeting
  if (isPreJoinScreen()) {
    return "pre-join";
  }

  // We're on a meeting URL — if not ended or pre-join, we're in-meeting.
  return "in-meeting";
}

function getMeetingTitle(): string {
  const titleEl =
    document.querySelector(".meeting-topic") ||
    document.querySelector(".meeting-title-container") ||
    document.querySelector('[class*="meeting-topic"]');
  if (titleEl?.textContent?.trim()) return titleEl.textContent.trim();
  const match = window.location.href.match(ZOOM_URL_PATTERN);
  return match ? `Zoom Meeting ${match[1]}` : "Zoom Meeting";
}

function createButton() {
  if (floatingButton) return;
  // Always use floating mode for Zoom web (toolbar DOM is too volatile)
  floatingButton = new FloatingButton();
}

function startCaptionScraping(recordingStartTime: number) {
  captionScraper = new CaptionScraper((speaker) => {
    console.log(`[Memoir Zoom] Active speaker: ${speaker}`);
  });
  captionScraper.start(recordingStartTime);

  captionObserver = new MutationObserver(() => {
    // Zoom live transcription: "#live-transcription-subtitle" contains "Name: text"
    const subtitleEl = document.querySelector("#live-transcription-subtitle");
    if (subtitleEl?.textContent) {
      const match = subtitleEl.textContent.match(/^([^:]+):/);
      if (match) captionScraper?.onCaption(match[1].trim());
    }
  });

  captionObserver.observe(document.body, { childList: true, subtree: true });
}

function stopCaptionScraping() {
  captionObserver?.disconnect();
  captionObserver = null;
  captionScraper?.stop();
  captionScraper = null;
}

function onStateChange(newState: "pre-join" | "in-meeting" | "ended" | null) {
  // If we were in-meeting and detectState() now returns null,
  // the meeting is over (Zoom navigated away from call UI).
  if (!newState && (currentState === "in-meeting" || currentState === "ended")) {
    newState = "ended";
  }
  if (!newState || newState === currentState) return;
  currentState = newState;

  if (newState === "pre-join") {
    lastSentUrl = window.location.href;
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "pre-join",
      platform: PLATFORM,
      meetingUrl: window.location.href,
      meetingTitle: getMeetingTitle(),
      tabId: 0,
    });
  } else if (newState === "in-meeting") {
    lastSentUrl = window.location.href;
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "in-meeting",
      platform: PLATFORM,
      meetingUrl: window.location.href,
      meetingTitle: getMeetingTitle(),
      tabId: 0,
    });

    createButton();
    floatingButton?.setState("record");
  } else if (newState === "ended") {
    stopUrlMonitor();
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "ended",
      platform: PLATFORM,
      meetingUrl: window.location.href,
      tabId: 0,
    });

    if (floatingButton) floatingButton.setState("hidden");

    if (activeMeetingId) {
      if (postCTA) postCTA.destroy();
      postCTA = new PostMeetingCTA(activeMeetingId);
      activeMeetingId = null;
    }
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "SCRAPE_PARTICIPANTS_FROM_DOM") {
    sendResponse({ data: getZoomParticipants() });
    return false;
  }
  if (msg.type === "RECORDING_STATUS_UPDATE") {
    if (msg.state === "joining" && floatingButton) {
      floatingButton.setState("joining");
      activeMeetingId = msg.meetingId ?? null;
    } else if (msg.state === "starting_to_record" && floatingButton) {
      floatingButton.setState("starting_to_record");
    } else if (msg.state === "recording" && floatingButton) {
      floatingButton.setState("recording");
      if (msg.mode === "tab_audio") {
        startCaptionScraping(Date.now());
      }
    } else if (msg.state === "stopped") {
      if (msg.meetingId) activeMeetingId = msg.meetingId;
      if (floatingButton) floatingButton.setState("record");
      stopCaptionScraping();
    } else if (msg.state === "completed") {
      if (msg.meetingId) activeMeetingId = msg.meetingId;
      if (floatingButton) floatingButton.setState("hidden");
      stopCaptionScraping();
    } else if (msg.state === "finalizing_upload") {
      if (floatingButton) floatingButton.setState("uploading");
      stopCaptionScraping();
    }
  }
});

// Debounced MutationObserver (Zoom re-renders frequently)
const observer = new MutationObserver(() => {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    onStateChange(detectState());
  }, 300);
});

// Monitor URL changes — Zoom SPA rewrites URL via pushState/replaceState.
// The URL may change from /join?fromPWA=1 to /start?fromPWA=1&pwd=...
// We need to catch the pwd and re-send the URL to the background.
let urlCheckInterval: ReturnType<typeof setInterval> | null = null;

function startUrlMonitor() {
  if (urlCheckInterval) return;
  urlCheckInterval = setInterval(() => {
    const currentUrl = window.location.href;
    if (currentUrl !== lastSentUrl && isZoomMeetingUrl()) {
      // URL changed — re-send if it now has pwd (or if we never sent one)
      const hasPwd = currentUrl.includes("pwd=");
      const lastHadPwd = lastSentUrl?.includes("pwd=") ?? false;
      if (hasPwd && !lastHadPwd) {
        console.log("[Memoir] Zoom URL updated with pwd:", currentUrl);
        lastSentUrl = currentUrl;
        sendMessage({
          type: "MEETING_STATE_CHANGE",
          state: "in-meeting",
          platform: PLATFORM,
          meetingUrl: currentUrl,
          meetingTitle: getMeetingTitle(),
          tabId: 0,
        });
      }
    }
  }, 500); // Check every 500ms — lightweight string comparison
}

function stopUrlMonitor() {
  if (urlCheckInterval) {
    clearInterval(urlCheckInterval);
    urlCheckInterval = null;
  }
}

function init() {
  const initial = detectState();
  if (initial) onStateChange(initial);

  // Start URL monitoring for pwd capture
  if (isZoomMeetingUrl()) startUrlMonitor();

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
