import { sendMessage } from "../../shared/messaging";
import { FloatingButton, type ToolbarPlacement } from "../common/floating-button";
import { PostMeetingCTA } from "../common/post-meeting-cta";
import type { Platform } from "../../shared/types";
import { CaptionScraper } from "../common/caption-scraper";

interface ScrapedParticipant { name: string; isSelf?: boolean }
interface ParticipantsPayload { participants: ScrapedParticipant[]; count: number | null }

/**
 * Mirrors GoogleMeetBot.get_participant_count() at browser.py:275-329.
 * Primary: regex \((\d+)\) on the People button aria-label.
 * Fallbacks: count [data-participant-id] tiles, then "alone" body text.
 */
function getMeetParticipantCount(): number | null {
  for (const sel of [
    'button[aria-label*="Show everyone"]',
    'button[aria-label*="people"]',
    'button[aria-label*="participant"]',
  ]) {
    const btn = document.querySelector(sel);
    if (!btn) continue;
    const label = btn.getAttribute("aria-label") || "";
    const m = label.match(/\((\d+)\)/);
    if (m) return parseInt(m[1], 10);
  }
  const tileCount = document.querySelectorAll("[data-participant-id]").length;
  if (tileCount > 0) return tileCount;
  const body = document.body.innerText || "";
  if (
    body.includes("You're the only one here") ||
    body.includes("No one else is here") ||
    body.includes("you're the only one") ||
    body.includes("Waiting for others to join")
  ) {
    return 1;
  }
  return null;
}

/**
 * Meet participant list — mirrors the JS the bot evaluates in
 * BotWorker/app/services/browser.py:331 (GoogleMeetBot.get_participant_names)
 * + the injected scrapeParticipants() at browser.py:502. Same selectors,
 * same length<100 sanity check, same parenthesis-strip on aria-label.
 *
 * Sources (in priority order):
 *   1. window.__memoirParticipants — populated by the injected
 *      MutationObserver if the bot (or a future popup-injected version)
 *      is running on this page
 *   2. [data-participant-id] tiles, reading inner [data-self-name] /
 *      [data-tooltip] (attribute OR innerText)
 *   3. Same tiles' own aria-label, stripping "(…)" suffix, rejecting
 *      labels that start with a digit or are >= 100 chars
 *   4. span.notranslate — name labels in sidebar/tiles
 *   5. CaptionScraper.getSeenSpeakers() — session caption history
 */
// Strings that show up in span.notranslate / aria-label but are NOT
// participant names — Meet UI controls, toolbar labels, etc. Without
// this list, the popup shows things like "devices" / "Microphone" /
// "Captions" as fake attendees alongside real names.
const MEET_UI_DENYLIST = new Set([
  "devices",
  "device",
  "microphone",
  "camera",
  "captions",
  "reactions",
  "hand",
  "raise hand",
  "lower hand",
  "more options",
  "leave call",
  "leave meeting",
  "present now",
  "chat",
  "people",
  "host controls",
  "host management",
  "info",
  "activities",
  "show everyone",
  "you",
  "speaker",
  "speakers",
  "headphones",
  "default",
  "system default",
  "audio",
  "video",
  "screen",
  "screen share",
  "share screen",
  "settings",
  "meeting host",
  "host",
  "contributors",
  "viewers",
  "search for people",
  "add people",
]);

function getMeetParticipants(): ParticipantsPayload {
  const out = new Map<string, ScrapedParticipant>();
  const add = (name: string | null | undefined, source: string) => {
    const t = (name || "").trim();
    if (!t || t.length >= 100) return;
    // Reject Meet UI strings and labels that don't look like real names.
    const lower = t.toLowerCase();
    if (MEET_UI_DENYLIST.has(lower)) return;
    // Reject single tokens that are all-lowercase + short — real display
    // names start with a capital letter (or include one). "devices" /
    // "microphone" / etc. fall into this; "John" / "John Doe" do not.
    if (t.length < 20 && !/[A-ZÀ-ſ]/.test(t)) return;
    // Reject pure-punctuation / pure-numeric / button-text patterns.
    if (!/[a-zA-ZÀ-ſ]/.test(t)) return;
    if (!out.has(t)) {
      out.set(t, { name: t });
      console.log(`[Memoir Meet] +${t} via ${source}`);
    }
  };

  // 1. Observer-injected accumulated set
  const injected = (window as unknown as { __memoirParticipants?: string[] }).__memoirParticipants;
  if (injected && injected.length > 0) {
    injected.forEach((n) => add(n, "__memoirParticipants"));
  }

  // 2 + 3. Tile scrape — verbatim transliteration of browser.py:336-358
  document.querySelectorAll<HTMLElement>("[data-participant-id]").forEach((el) => {
    const nameEl = el.querySelector<HTMLElement>("[data-self-name]") || el.querySelector<HTMLElement>("[data-tooltip]");
    if (nameEl) {
      const n =
        nameEl.getAttribute("data-self-name") ||
        nameEl.getAttribute("data-tooltip") ||
        nameEl.innerText;
      add(n, "tile child [data-self-name|data-tooltip|innerText]");
    }
    const label = el.getAttribute("aria-label") || "";
    if (label && !/^\d/.test(label) && label.length < 100) {
      const cleaned = label.replace(/\s*\(.*\)$/, "").trim();
      if (cleaned) add(cleaned, "tile aria-label (cleaned)");
    }
  });

  // 4. span.notranslate — name labels in sidebar / tiles (per
  //    browser.py:519 injected observer)
  document.querySelectorAll<HTMLElement>("span.notranslate").forEach((el) => {
    add(el.textContent, "span.notranslate");
  });

  // 5. Caption-speaker history
  (captionScraper?.getSeenSpeakers() ?? []).forEach((n) => add(n, "speaker-map"));

  const participants = Array.from(out.values());
  // Prefer the deduped name-set length over the People-button badge —
  // Meet caches that badge and lags 10-30s when someone leaves, so the
  // popup would otherwise show "2" while the chip list shows only 1.
  // Fall back to the badge regex only when we have zero scraped names
  // (e.g. tiles haven't rendered yet on first popup open).
  const badgeCount = getMeetParticipantCount();
  const count = participants.length > 0 ? participants.length : badgeCount;
  console.log("[Memoir Meet] participants:", participants, "count:", count, "(badge:", badgeCount, ")");
  return { participants, count };
}

const PLATFORM: Platform = "google_meet";
const MEET_URL_PATTERN = /meet\.google\.com\/([a-z]{3}-[a-z]{4}-[a-z]{3})/;

let currentState: "idle" | "pre-join" | "in-meeting" | "ended" = "idle";
let floatingButton: FloatingButton | null = null;
let postCTA: PostMeetingCTA | null = null;
let activeMeetingId: string | null = null;
let captionScraper: CaptionScraper | null = null;
let captionObserver: MutationObserver | null = null;

/** Check if we're on the pre-join "Ready to join?" screen */
function isPreJoinScreen(): boolean {
  // "Join now" or "Ask to join" button
  const buttons = document.querySelectorAll("button");
  for (const btn of buttons) {
    const text = btn.textContent?.trim().toLowerCase() ?? "";
    if (text === "join now" || text === "ask to join") {
      return true;
    }
  }
  // "Ready to join?" heading
  const headings = document.querySelectorAll("h1, h2, div[role='heading']");
  for (const h of headings) {
    if (h.textContent?.toLowerCase().includes("ready to join")) return true;
  }
  return false;
}

/** Detect meeting state from DOM */
function detectState(): "pre-join" | "in-meeting" | "ended" | null {
  // Check for "ended" state first
  const headings = document.querySelectorAll("h1, [data-obt] h2, [jsname] h1");
  for (const h of headings) {
    const text = h.textContent?.toLowerCase() ?? "";
    if (
      text.includes("you left the meeting") ||
      text.includes("the meeting has ended") ||
      text.includes("you've been removed") ||
      text.includes("returning to home screen")
    ) {
      return "ended";
    }
  }

  // Check for pre-join BEFORE checking toolbar — the pre-join screen has
  // mic/camera buttons that can match toolbar selectors
  if (isPreJoinScreen()) {
    return "pre-join";
  }

  // Check for in-meeting: the real meeting toolbar
  // Use more specific selectors that only match the in-meeting toolbar
  const toolbar =
    document.querySelector('[data-panel-id="toolbar"]') ||
    document.querySelector('[data-tooltip="Turn off microphone"]') ||
    document.querySelector('[data-tooltip="Turn on microphone"]') ||
    document.querySelector('button[aria-label="Leave call" i]');

  if (toolbar) return "in-meeting";

  // Pre-join if URL matches but no toolbar
  if (MEET_URL_PATTERN.test(window.location.href)) {
    return "pre-join";
  }

  return null;
}

/** Find the leave/end call button and its parent toolbar container */
function findToolbarPlacement(): ToolbarPlacement | undefined {
  // Google Meet: leave button has a red icon and specific aria-label
  const leaveBtn =
    document.querySelector('button[aria-label="Leave call"]') ||
    document.querySelector('button[aria-label="Leave call" i]') ||
    document.querySelector('button[data-tooltip="Leave call"]') ||
    document.querySelector('button[data-tooltip="Leave call" i]');

  if (leaveBtn && leaveBtn.parentElement) {
    return {
      container: leaveBtn.parentElement,
      leaveButton: leaveBtn,
    };
  }

  // Fallback: look for the bottom toolbar bar itself
  const toolbar = document.querySelector('[data-panel-id="toolbar"]');
  if (toolbar) {
    const endBtn = toolbar.querySelector('button[style*="background"]') ||
      toolbar.querySelector('[data-tooltip*="Leave"]');
    if (endBtn && endBtn.parentElement) {
      return {
        container: endBtn.parentElement,
        leaveButton: endBtn,
      };
    }
  }

  return undefined;
}

function getMeetingTitle(): string {
  // Prefer the Meet URL code (e.g. "bjx-gxtp-sjs") as the title — it's
  // stable, identifies the meeting room uniquely, and matches what other
  // entry points (calendar event sync, manual schedule) put in the title
  // field. document.title scrape is fragile: it's often just "Google Meet"
  // on fresh meetings, sometimes uses the calendar event title (good),
  // sometimes uses participant names. Prior logic returned the literal
  // "Google Meet" on too many real meetings.
  const match = window.location.href.match(MEET_URL_PATTERN);
  const urlCode = match ? match[1] : null;
  const docTitle = document.title.replace(" - Google Meet", "").trim();
  // Use document.title only if it carries information beyond the generic
  // platform string (i.e. a calendar event title or participant name).
  if (docTitle && docTitle !== "Google Meet" && docTitle.toLowerCase() !== "meet") {
    return urlCode ? `${docTitle} (${urlCode})` : docTitle;
  }
  return urlCode ? `Meet ${urlCode}` : "Google Meet";
}

function createButton() {
  if (floatingButton) return;

  // Try toolbar placement first, fall back to floating
  const placement = findToolbarPlacement();
  floatingButton = new FloatingButton(placement);
}

function startCaptionScraping(recordingStartTime: number) {
  captionScraper = new CaptionScraper((speaker) => {
    console.log(`[Memoir Meet] Active speaker: ${speaker}`);
  });
  captionScraper.start(recordingStartTime);

  captionObserver = new MutationObserver(() => {
    // Google Meet: speaker name badge in captions (class .NWpY1d or similar)
    const speakerEl =
      document.querySelector(".NWpY1d") ||
      document.querySelector("[data-sender-name]") ||
      document.querySelector(".CNusmb .zs7s8d");
    if (speakerEl && speakerEl.textContent) {
      captionScraper?.onCaption(speakerEl.textContent.trim());
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
  if (!newState || newState === currentState) return;
  currentState = newState;

  if (newState === "in-meeting") {
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
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "ended",
      platform: PLATFORM,
      meetingUrl: window.location.href,
      tabId: 0,
    });

    if (floatingButton) {
      floatingButton.setState("hidden");
    }

    if (activeMeetingId) {
      if (postCTA) postCTA.destroy();
      postCTA = new PostMeetingCTA(activeMeetingId);
      activeMeetingId = null;
    }
  }
  // pre-join: do nothing — no button, no notification to background
}

// Listen for status updates from background
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  // Popup → SW → here: scrape attendee names from the Meet DOM.
  // Union with the CaptionScraper's accumulated speakers (silent
  // participants who've already spoken still get picked up).
  if (msg.type === "SCRAPE_PARTICIPANTS_FROM_DOM") {
    sendResponse({ data: getMeetParticipants() });
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
      // User stopped recording but is still in the meeting
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

// MutationObserver for DOM changes
const observer = new MutationObserver(() => {
  const detected = detectState();
  onStateChange(detected);

  // If we're in-meeting but button was created as floating (toolbar wasn't ready),
  // try to re-create anchored to the leave button
  if (currentState === "in-meeting" && floatingButton && !floatingButton.isAnchored()) {
    const placement = findToolbarPlacement();
    if (placement) {
      const currentState = floatingButton.getState();
      floatingButton.destroy();
      floatingButton = new FloatingButton(placement);
      floatingButton.setState(currentState);
    }
  }
});

function init() {
  const initial = detectState();
  if (initial) onStateChange(initial);

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-panel-id", "aria-label"],
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
