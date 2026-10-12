/**
 * Runs in the PAGE context (world: "MAIN") on Teams pages.
 * Intercepts fetch() and clipboard to capture meeting URLs that
 * Teams stores internally (never in the DOM or address bar).
 *
 * Communicates with the ISOLATED world content script via postMessage.
 */

const MEETING_URL_RE = /https:\/\/teams\.(live|microsoft)\.com\/(meet|l\/meetup-join)\/[^\s"'\\<>]+/;
const MSG_TYPE = "__MEMOIR_MEETING_URL";

function postMeetingUrl(url: string) {
  // Clean up the URL (remove trailing punctuation, HTML entities)
  const clean = url.replace(/&amp;/g, "&").replace(/[,;)}\]]+$/, "");
  window.postMessage({ type: MSG_TYPE, url: clean }, "*");
}

// ─── Intercept fetch() to capture meeting URLs from API calls ─────
const origFetch = window.fetch;
window.fetch = function (this: typeof globalThis, ...args: Parameters<typeof fetch>) {
  // Check request URL and body for meeting URLs
  try {
    const input = args[0];
    const url = typeof input === "string" ? input : (input as Request)?.url ?? "";
    const init = args[1];

    if (MEETING_URL_RE.test(url)) {
      postMeetingUrl(url.match(MEETING_URL_RE)![0]);
    }

    if (init?.body) {
      const body = typeof init.body === "string" ? init.body : "";
      const match = body.match(MEETING_URL_RE);
      if (match) postMeetingUrl(match[0]);
    }
  } catch {
    // Ignore errors in URL/body inspection
  }

  // Always call original fetch, then inspect response
  return origFetch.apply(this, args).then((response) => {
    const cloned = response.clone();
    cloned
      .text()
      .then((text) => {
        const match = text.match(MEETING_URL_RE);
        if (match) postMeetingUrl(match[0]);
      })
      .catch(() => {});
    return response;
  });
} as typeof fetch;

// ─── Intercept XMLHttpRequest (Teams uses both fetch and XHR) ─────
const origXhrOpen = XMLHttpRequest.prototype.open;
const origXhrSend = XMLHttpRequest.prototype.send;

XMLHttpRequest.prototype.open = function (
  method: string,
  url: string | URL,
  ...rest: unknown[]
) {
  (this as XMLHttpRequest & { _memoirUrl?: string })._memoirUrl = String(url);
  if (MEETING_URL_RE.test(String(url))) {
    postMeetingUrl(String(url).match(MEETING_URL_RE)![0]);
  }
  return origXhrOpen.apply(this, [method, url, ...rest] as Parameters<typeof origXhrOpen>);
};

XMLHttpRequest.prototype.send = function (body?: Document | XMLHttpRequestBodyInit | null) {
  if (body && typeof body === "string") {
    const match = body.match(MEETING_URL_RE);
    if (match) postMeetingUrl(match[0]);
  }

  // Listen for response
  this.addEventListener("load", function () {
    try {
      const text = this.responseText;
      const match = text.match(MEETING_URL_RE);
      if (match) postMeetingUrl(match[0]);
    } catch {
      // responseText may not be available for non-text responses
    }
  });

  return origXhrSend.apply(this, [body] as Parameters<typeof origXhrSend>);
};

// ─── Intercept clipboard writes (Share link button) ───────────────
if (navigator.clipboard?.writeText) {
  const origWriteText = navigator.clipboard.writeText.bind(navigator.clipboard);
  navigator.clipboard.writeText = function (text: string) {
    if (MEETING_URL_RE.test(text)) {
      postMeetingUrl(text.match(MEETING_URL_RE)![0]);
    }
    return origWriteText(text);
  };
}
