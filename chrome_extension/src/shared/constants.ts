/** API base URLs */
// In dev (unpacked), check for __MEMOIR_DEV__ build flag. Webpack DefinePlugin sets it.
// Falls back to update_url check (present only in Chrome Web Store installs).
declare const __MEMOIR_DEV__: boolean;
const IS_DEV = typeof __MEMOIR_DEV__ !== "undefined" ? __MEMOIR_DEV__ : !chrome.runtime.getManifest().update_url;

export const API_BASE = IS_DEV ? "http://localhost:8005" : "https://app.meetmemoir.com";
export const AUTH_BASE = IS_DEV ? "http://localhost:8001" : "https://app.meetmemoir.com";
export const WEB_BASE = IS_DEV ? "http://localhost:3000" : "https://app.meetmemoir.com";

/** chrome.storage.local key for auth */
export const STORAGE_AUTH_KEY = "memoir-ext-auth";

/** chrome.storage.local key for meeting state */
export const STORAGE_MEETING_KEY = "memoir-ext-meeting";

/** Zustand persist key used by the frontend */
export const ZUSTAND_AUTH_KEY = "memoir-auth";

/** Alarm names */
export const ALARM_TOKEN_REFRESH = "memoir-token-refresh";
export const ALARM_POLL_MEETING = "memoir-poll-meeting";

/** Polling intervals */
export const TOKEN_REFRESH_MINUTES = 12; // refresh 3min before 15min expiry
export const MEETING_POLL_MINUTES = 0.5; // every 30s

/** Notes debounce */
export const NOTES_DEBOUNCE_MS = 2000;

/** Chrome Web Store URL (placeholder until published) */
export const CHROME_STORE_URL = "https://chromewebstore.google.com/detail/memoir";
