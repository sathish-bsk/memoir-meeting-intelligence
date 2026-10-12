import { sendMessage } from "../../shared/messaging";
import { FloatingButton } from "../common/floating-button";
import { PostMeetingCTA } from "../common/post-meeting-cta";
import { CaptionScraper } from "../common/caption-scraper";

interface ScrapedParticipant { name: string; isSelf?: boolean }
interface ParticipantsPayload { participants: ScrapedParticipant[]; count: number | null }

/** Mirrors TeamsBot.get_participant_count() — People button aria-label
 *  / inner text → /(\d+)/; falls back to scanning all buttons whose
 *  aria-label mentions people/participant/roster, then roster DOM
 *  item counts. */
function getTeamsParticipantCount(): number | null {
  for (const sel of [
    'button[aria-label*="People" i]',
    'button[data-tid="roster-button"]',
  ]) {
    const btn = document.querySelector<HTMLElement>(sel);
    if (!btn) continue;
    const label = btn.getAttribute("aria-label") || "";
    const m1 = label.match(/(\d+)/);
    if (m1) return parseInt(m1[1], 10);
    const m2 = (btn.innerText || "").match(/(\d+)/);
    if (m2) return parseInt(m2[1], 10);
  }
  for (const btn of Array.from(document.querySelectorAll<HTMLElement>("button"))) {
    const label = (btn.getAttribute("aria-label") || "").toLowerCase();
    if (label.includes("people") || label.includes("participant") || label.includes("roster")) {
      const m = label.match(/(\d+)/) || (btn.innerText || "").match(/(\d+)/);
      if (m) return parseInt(m[1], 10);
    }
  }
  for (const sel of [
    '[data-tid="roster-participant"]',
    '[data-tid="participantItem"]',
    '[data-cid="roster-participant"]',
    '.roster-list-item',
    '[role="listitem"][data-tid*="participant"]',
  ]) {
    const n = document.querySelectorAll(sel).length;
    if (n > 0) return n;
  }
  return null;
}

/**
 * Teams participants — mirrors BotWorker teams_bot.py:297-325
 * (get_participant_names). Reads the panel observer's accumulated set
 * if present, else falls back to direct DOM scrape of the same
 * selectors the bot uses.
 *
 * Selectors:
 *   [data-tid*="participantsInCall"]      — aria-label, regex ^([^,]+)
 *   [data-cid="roster-participant"] span[title]  — title attribute
 *   captionScraper.getSeenSpeakers()      — session speaker history
 *
 * Length cap (< 100) matches the bot's check.
 */
function getTeamsParticipants(): ParticipantsPayload {
  const out = new Map<string, ScrapedParticipant>();
  const add = (name: string | null | undefined, source: string) => {
    const t = (name || "").trim();
    if (!t || t.length >= 100) return;
    if (!out.has(t)) {
      out.set(t, { name: t });
      console.log(`[Memoir Teams] +${t} via ${source}`);
    }
  };

  // 1. Observer-injected accumulated set
  const injected = (window as unknown as { __memoirParticipants?: string[] }).__memoirParticipants;
  if (injected && injected.length > 0) {
    injected.forEach((n) => add(n, "__memoirParticipants"));
  }

  // 2. Direct DOM — same selectors the bot uses
  document.querySelectorAll<HTMLElement>('[data-tid*="participantsInCall"]').forEach((el) => {
    const label = el.getAttribute("aria-label") || "";
    const match = label.match(/^([^,]+)/);
    if (match) add(match[1].trim(), '[data-tid*="participantsInCall"] aria-label');
  });
  document.querySelectorAll<HTMLElement>('[data-cid="roster-participant"] span[title]').forEach((el) => {
    add(el.getAttribute("title"), '[data-cid="roster-participant"] span[title]');
  });

  // 3. Speaker-map history (caption-driven)
  (captionScraper?.getSeenSpeakers() ?? []).forEach((n) => add(n, "speaker-map"));

  const participants = Array.from(out.values());
  const count = getTeamsParticipantCount();
  console.log("[Memoir Teams] participants:", participants, "count:", count);
  return { participants, count };
}
import type { Platform } from "../../shared/types";

const PLATFORM: Platform = "teams";
const TEAMS_URL_RE = /teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\//;
const TEAMS_URL_STORAGE_KEY = "memoir-teams-meeting-url";

let currentState: "idle" | "pre-join" | "in-meeting" | "ended" = "idle";
let floatingButton: FloatingButton | null = null;
let postCTA: PostMeetingCTA | null = null;
let activeMeetingId: string | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let captionScraper: CaptionScraper | null = null;
let captionObserver: MutationObserver | null = null;

// ─── URL capture ─────────────────────────────────────────────────
// Teams SPA rewrites /meet/ABC123?p=... → /v2/ via pushState.
// With run_at: "document_start", this code runs BEFORE Teams JS,
// so window.location.href still has the real meeting URL.

let capturedMeetingUrl: string | null = null;

// 1. Immediate capture at script load (primary strategy)
if (TEAMS_URL_RE.test(window.location.href)) {
  capturedMeetingUrl = window.location.href;
  chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: capturedMeetingUrl });
} else {
  // 2. We're on /v2/ — read URL stored by background (webNavigation listener)
  chrome.storage.local.get(TEAMS_URL_STORAGE_KEY, (result) => {
    const stored = result[TEAMS_URL_STORAGE_KEY];
    if (stored && !capturedMeetingUrl) {
      capturedMeetingUrl = stored;
    }
  });
}

/** Get the real Teams meeting URL (not the /v2/ SPA URL) */
function getMeetingUrl(): string {
  if (capturedMeetingUrl) return capturedMeetingUrl;

  const href = window.location.href;
  if (TEAMS_URL_RE.test(href)) {
    capturedMeetingUrl = href;
    return href;
  }

  // Look for meeting join links in the DOM
  const joinLinks = document.querySelectorAll('a[href*="/meet/"], a[href*="/meetup-join/"]');
  for (const link of joinLinks) {
    const linkHref = (link as HTMLAnchorElement).href;
    if (TEAMS_URL_RE.test(linkHref)) {
      capturedMeetingUrl = linkHref;
      return linkHref;
    }
  }

  // Check data attributes for meeting URLs
  const meetingEls = document.querySelectorAll("[data-meeting-link], [data-meeting-url], [data-join-url], [data-tid*='meeting']");
  for (const el of meetingEls) {
    for (const attr of el.getAttributeNames()) {
      const val = el.getAttribute(attr) ?? "";
      if (TEAMS_URL_RE.test(val)) {
        capturedMeetingUrl = val;
        return val;
      }
    }
  }

  // Aggressive: scan all page HTML for meeting URL patterns
  // This finds URLs in data attributes, hidden elements, inline scripts, etc.
  try {
    const html = document.body.innerHTML;
    const match = html.match(
      /https:\/\/teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\/[^\s"'<>\\]+/
    );
    if (match) {
      // Decode HTML entities
      const url = match[0].replace(/&amp;/g, "&");
      capturedMeetingUrl = url;
      return url;
    }
  } catch {
    // innerHTML can be large — ignore errors
  }

  // Fallback to current URL
  return href;
}

// ─── State detection ─────────────────────────────────────────────

/** Check if we're on the pre-join screen (before actually joining the call) */
function isPreJoinScreen(): boolean {
  const allText = document.body?.innerText?.toLowerCase() ?? "";
  const joinBtn =
    document.querySelector('button[data-tid="prejoin-join-button"]') ||
    document.querySelector('button[aria-label*="Join now" i]') ||
    document.querySelector('button[aria-label*="Join meeting" i]');
  if (joinBtn) return true;

  if (
    (allText.includes("join now") || allText.includes("join meeting")) &&
    !allText.includes("waiting for others")
  ) {
    return true;
  }

  return false;
}

function detectState(): "pre-join" | "in-meeting" | "ended" | null {
  // document.body may not exist yet at document_start
  if (!document.body) return null;

  const allText = document.body.innerText?.toLowerCase() ?? "";

  // Check for ended state first
  if (
    allText.includes("call ended") ||
    allText.includes("you left the meeting") ||
    allText.includes("the meeting has ended") ||
    allText.includes("you left the call") ||
    allText.includes("the call has ended")
  ) {
    return "ended";
  }

  // Check for pre-join BEFORE checking in-meeting
  if (isPreJoinScreen()) {
    return "pre-join";
  }

  // Broad in-meeting detection — Teams web uses many different DOM structures
  const inCall =
    document.querySelector('[data-tid="hangup-btn"]') ||
    document.querySelector('[id="hangup-button"]') ||
    document.querySelector('[data-tid="toggle-mute"]') ||
    document.querySelector('button[aria-label*="Hang up" i]') ||
    document.querySelector('button[aria-label*="Leave" i]') ||
    document.querySelector('button[aria-label*="leave call" i]') ||
    document.querySelector('[data-tid="call-duration"]');

  if (inCall) return "in-meeting";

  // Fallback: if URL is /v2/ and we see meeting-related UI
  if (window.location.pathname.startsWith("/v2/")) {
    if (
      allText.includes("waiting for others to join") ||
      allText.includes("mute") ||
      allText.includes("leave")
    ) {
      const buttons = document.querySelectorAll("button");
      for (const btn of buttons) {
        const label =
          (btn.textContent?.toLowerCase() ?? "") +
          (btn.getAttribute("aria-label")?.toLowerCase() ?? "");
        if (label.includes("leave") || label.includes("hang up")) {
          return "in-meeting";
        }
      }
    }
  }

  return null;
}

function getMeetingTitle(): string {
  const titleEl =
    document.querySelector('[data-tid="call-title"]') ||
    document.querySelector(".calling-screen-header");
  if (titleEl?.textContent?.trim()) return titleEl.textContent.trim();
  return "Teams Meeting";
}

function createButton() {
  if (floatingButton) return;
  floatingButton = new FloatingButton();
}

function startCaptionScraping(recordingStartTime: number) {
  captionScraper = new CaptionScraper((speaker) => {
    console.log(`[Memoir Teams] Active speaker: ${speaker}`);
  });
  captionScraper.start(recordingStartTime);

  captionObserver = new MutationObserver(() => {
    // Teams: author span inside closed-caption renderer
    const authorEl = document.querySelector(
      '[data-tid="closed-caption-renderer-wrapper"] span[data-tid="author"]'
    );
    if (authorEl?.textContent) {
      captionScraper?.onCaption(authorEl.textContent.trim());
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
  // the meeting is over (Teams SPA navigated away from call UI).
  if (!newState && (currentState === "in-meeting" || currentState === "ended")) {
    newState = "ended";
  }
  if (!newState || newState === currentState) return;
  currentState = newState;

  if (newState === "pre-join") {
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "pre-join",
      platform: PLATFORM,
      meetingUrl: getMeetingUrl(),
      meetingTitle: getMeetingTitle(),
      tabId: 0,
    });
  } else if (newState === "in-meeting") {
    sendMessage({
      type: "MEETING_STATE_CHANGE",
      state: "in-meeting",
      platform: PLATFORM,
      meetingUrl: getMeetingUrl(),
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
      meetingUrl: getMeetingUrl(),
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
    sendResponse({ data: getTeamsParticipants() });
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

// ─── Click listener to capture meeting URLs from Join buttons ────
// On the Teams home page, "Join" is a <button> not an <a>, so there's
// no href to read. We walk up the DOM from the clicked element, scan
// the parent card for any meeting URL in links, data attrs, or raw HTML.

function extractMeetingUrlFromElement(root: Element): string | null {
  // 1. Check <a> links inside the container
  const links = root.querySelectorAll("a[href]");
  for (const link of links) {
    const href = (link as HTMLAnchorElement).href;
    if (TEAMS_URL_RE.test(href)) return href;
  }

  // 2. Check data attributes on all elements
  const allEls = root.querySelectorAll("*");
  for (const el of allEls) {
    for (const attr of el.getAttributeNames()) {
      const val = el.getAttribute(attr) ?? "";
      if (TEAMS_URL_RE.test(val)) return val;
    }
  }

  // 3. Scan the container's HTML for a meeting URL pattern
  const html = root.innerHTML;
  const match = html.match(
    /https:\/\/teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\/[^\s"'<>\\]+/
  );
  if (match) return match[0].replace(/&amp;/g, "&");

  return null;
}

document.addEventListener("click", (e) => {
  const target = e.target as HTMLElement;

  // Check if click is directly on a link with meeting URL
  const link = target.closest('a[href*="/meet/"], a[href*="/meetup-join/"]') as HTMLAnchorElement | null;
  if (link?.href && TEAMS_URL_RE.test(link.href)) {
    capturedMeetingUrl = link.href;
    chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: link.href });
    return;
  }

  // For <button> clicks (like "Join"), walk up to find a parent card
  // and scan it for meeting URLs
  const btn = target.closest("button");
  if (btn) {
    const btnText = (btn.textContent ?? "").toLowerCase();
    if (btnText.includes("join") || btnText.includes("share")) {
      // Walk up to find a container card (try multiple levels)
      let container = btn.parentElement;
      for (let i = 0; i < 8 && container; i++) {
        const url = extractMeetingUrlFromElement(container);
        if (url) {
          capturedMeetingUrl = url;
          chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: url });
          return;
        }
        container = container.parentElement;
      }
    }
  }
}, true); // capture phase to run before Teams handlers

// ─── Listen for meeting URLs from page-interceptor (MAIN world) ──
// The page-interceptor.ts runs in the MAIN world and intercepts
// fetch/XHR/clipboard to capture meeting URLs that Teams stores
// only in JavaScript state (never in DOM or address bar).
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  if (event.data?.type === "__MEMOIR_MEETING_URL" && event.data?.url) {
    const url = event.data.url;
    if (TEAMS_URL_RE.test(url)) {
      capturedMeetingUrl = url;
      chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: url });
    }
  }
});

// ─── SPA navigation monitoring ───────────────────────────────────
// Capture meeting URL if Teams navigates from /meet/ to /v2/

const origPushState = history.pushState.bind(history);
const origReplaceState = history.replaceState.bind(history);

const checkUrlForCapture = () => {
  const href = window.location.href;
  if (TEAMS_URL_RE.test(href)) {
    capturedMeetingUrl = href;
    chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: href });
  }
};

history.pushState = function (...args) {
  origPushState(...args);
  checkUrlForCapture();
};
history.replaceState = function (...args) {
  origReplaceState(...args);
  checkUrlForCapture();
};
window.addEventListener("popstate", checkUrlForCapture);

// ─── MutationObserver (debounced) ────────────────────────────────

const observer = new MutationObserver(() => {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    onStateChange(detectState());
  }, 300);
});

function init() {
  // Try to capture meeting URL from referrer
  if (!capturedMeetingUrl && document.referrer) {
    if (TEAMS_URL_RE.test(document.referrer)) {
      capturedMeetingUrl = document.referrer;
      chrome.storage.local.set({ [TEAMS_URL_STORAGE_KEY]: capturedMeetingUrl });
    }
  }

  const initial = detectState();
  if (initial) onStateChange(initial);

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });
}

// With document_start, document.body won't exist yet
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
