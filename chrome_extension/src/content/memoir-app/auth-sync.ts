import { sendMessage } from "../../shared/messaging";
import { ZUSTAND_AUTH_KEY } from "../../shared/constants";
import type { ExtensionMessage } from "../../shared/types";

/**
 * Content script that runs on memoir.app / localhost:3000.
 *
 * 1. Sets a DOM marker so the frontend can detect the extension
 * 2. Reads localStorage auth and syncs to the extension
 * 3. Listens for auth changes (storage events) and re-syncs
 * 4. Receives auth from extension popup login and writes to localStorage
 * 5. Detects web logout and notifies extension to clear auth
 * 6. Bridges page → service worker for /record extension-handoff requests
 */

// ─── DOM marker for extension detection ───────────────────────────
document.documentElement.setAttribute("data-memoir-ext", "true");

// ─── JWT parsing (matches frontend's parseJwtPayload) ─────────────

function parseJwtPayload(token: string): Record<string, unknown> {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join(""),
    );
    return JSON.parse(jsonPayload);
  } catch {
    return {};
  }
}

// ─── Zustand persist format (must match frontend/src/store/auth.ts) ─

interface ZustandPersistedState {
  state: {
    accessToken: string | null;
    activeTeamId: string | null;
    user: {
      id: string;
      email: string;
      name: string;
      team_id: string;
      team_role: string;
      plan: string;
      onboarding_completed: boolean;
      is_super_admin: boolean;
    } | null;
    teams: Array<{ id: string; name: string; slug: string }>;
    isAuthenticated: boolean;
  };
  version: number;
}

function readAuthFromLocalStorage(): ZustandPersistedState["state"] | null {
  try {
    const raw = localStorage.getItem(ZUSTAND_AUTH_KEY);
    if (!raw) return null;
    const parsed: ZustandPersistedState = JSON.parse(raw);
    if (parsed?.state?.accessToken && parsed?.state?.user) {
      return parsed.state;
    }
    return null;
  } catch {
    return null;
  }
}

function syncWebToExtension() {
  const auth = readAuthFromLocalStorage();
  if (auth?.accessToken && auth?.user && auth?.activeTeamId) {
    sendMessage({
      type: "AUTH_SYNC_FROM_WEB",
      accessToken: auth.accessToken,
      activeTeamId: auth.activeTeamId,
      activeTeamName: undefined,
      user: {
        id: auth.user.id,
        email: auth.user.email,
        name: auth.user.name,
      },
    });
  }
}

function syncLogoutToExtension() {
  sendMessage({ type: "LOGOUT" });
}

// ─── Initial sync on page load ────────────────────────────────────
const initialAuth = readAuthFromLocalStorage();
if (initialAuth?.accessToken && initialAuth?.user && initialAuth?.activeTeamId) {
  syncWebToExtension();
} else {
  // No auth in web localStorage — if we're on the login page,
  // the user is logged out, so clear extension auth too
  if (window.location.pathname.includes("/login") || window.location.pathname === "/") {
    syncLogoutToExtension();
  }
}

// ─── Listen for localStorage changes (login/logout on another tab) ─
window.addEventListener("storage", (event) => {
  if (event.key === ZUSTAND_AUTH_KEY) {
    const auth = readAuthFromLocalStorage();
    if (auth?.accessToken) {
      syncWebToExtension();
    } else {
      syncLogoutToExtension();
    }
  }
});

// ─── Listen for auth sync FROM extension (popup login) → write to localStorage
chrome.runtime.onMessage.addListener((msg: ExtensionMessage) => {
  if (msg.type === "AUTH_SYNC_TO_WEB") {
    try {
      // Parse JWT to get full user fields (matches frontend's setAccessToken)
      const payload = parseJwtPayload(msg.accessToken);
      const user = {
        id: (payload.sub as string) || msg.user.id,
        email: (payload.email as string) || msg.user.email,
        name: (payload.name as string) || msg.user.name,
        team_id: (payload.team_id as string) || msg.activeTeamId,
        team_role: (payload.team_role as string) || "member",
        plan: (payload.plan as string) || "free",
        onboarding_completed: Boolean(payload.onboarding_completed),
        is_super_admin: Boolean(payload.is_super_admin),
      };

      const teams = msg.activeTeamId
        ? [{ id: msg.activeTeamId, name: msg.activeTeamName || "Team", slug: "" }]
        : [];

      // Write in exact Zustand persist format
      const store: ZustandPersistedState = {
        state: {
          accessToken: msg.accessToken,
          activeTeamId: msg.activeTeamId,
          user,
          teams,
          isAuthenticated: true,
        },
        version: 0,
      };
      localStorage.setItem(ZUSTAND_AUTH_KEY, JSON.stringify(store));

      // Reload so the frontend Zustand store rehydrates with the new auth
      window.location.reload();
    } catch (err) {
      console.error("[Memoir Extension] Failed to write auth to localStorage:", err);
    }
  }
});

// ─── Observe for in-page Zustand updates (same tab) ───────────────
// Since Zustand updates via setState don't fire 'storage' events in the same tab,
// use a polling check. Reduced to 2s for faster sync.
let lastToken: string | null = readAuthFromLocalStorage()?.accessToken ?? null;

setInterval(() => {
  const auth = readAuthFromLocalStorage();
  const currentToken = auth?.accessToken ?? null;
  if (currentToken !== lastToken) {
    lastToken = currentToken;
    if (currentToken) {
      syncWebToExtension();
    } else {
      // Auth was cleared on web (logged out) — notify extension
      syncLogoutToExtension();
    }
  }
}, 2000);

// ─── Page → Extension bridge (for /record extension handoff) ──────────
//
// The page (frontend/src/components/recorder/extension-bridge.ts) calls
// window.postMessage({ type: "MEMOIR_EXT_REQUEST", requestId, subtype, payload })
// and listens for { type: "MEMOIR_EXT_RESPONSE", requestId, success, data?, error? }.
//
// We validate the message, relay to the service worker via sendMessage(),
// then post the response back on the same window.

type HandoffSubtype =
  | "LIST_MEETING_TABS"
  | "HANDOFF_START"
  | "HANDOFF_STOP"
  | "AD_HOC_START"
  | "GET_MIC_DEVICE"
  | "SET_MIC_DEVICE"
  | "GET_RECORDING_STATE";
const ALLOWED_SUBTYPES: ReadonlySet<HandoffSubtype> = new Set<HandoffSubtype>([
  "LIST_MEETING_TABS",
  "HANDOFF_START",
  "HANDOFF_STOP",
  "AD_HOC_START",
  "GET_MIC_DEVICE",
  "SET_MIC_DEVICE",
  "GET_RECORDING_STATE",
]);

interface PageRequest {
  type: "MEMOIR_EXT_REQUEST";
  requestId: string;
  subtype: HandoffSubtype;
  payload?: Record<string, unknown>;
}

function buildSwMessage(
  subtype: HandoffSubtype,
  payload: Record<string, unknown>,
): ExtensionMessage {
  switch (subtype) {
    case "LIST_MEETING_TABS":
      return { type: "WEB_RECORDER_LIST_MEETING_TABS" };
    case "HANDOFF_START":
      return {
        type: "WEB_RECORDER_HANDOFF_START",
        tabId: Number(payload.tabId),
        title: typeof payload.title === "string" ? payload.title : undefined,
        language:
          typeof payload.language === "string" || payload.language === null
            ? (payload.language as string | null)
            : undefined,
      };
    case "HANDOFF_STOP":
      return {
        type: "WEB_RECORDER_HANDOFF_STOP",
        meetingId: String(payload.meetingId),
      };
    case "AD_HOC_START":
      return {
        type: "START_RECORDING_AD_HOC",
        title: typeof payload.title === "string" ? payload.title : undefined,
      };
    case "GET_MIC_DEVICE":
      return { type: "WEB_RECORDER_GET_MIC_DEVICE" };
    case "SET_MIC_DEVICE":
      return {
        type: "WEB_RECORDER_SET_MIC_DEVICE",
        deviceId:
          typeof payload.deviceId === "string" || payload.deviceId === null
            ? (payload.deviceId as string | null)
            : null,
      };
    case "GET_RECORDING_STATE":
      return { type: "WEB_RECORDER_GET_RECORDING_STATE" };
  }
}

window.addEventListener("message", async (event) => {
  if (event.source !== window) return;
  if (event.origin !== window.location.origin) return;

  const data = event.data as PageRequest | undefined;
  if (!data || data.type !== "MEMOIR_EXT_REQUEST") return;
  if (typeof data.requestId !== "string") return;
  if (!ALLOWED_SUBTYPES.has(data.subtype)) return;

  const payload =
    data.payload && typeof data.payload === "object" ? data.payload : {};

  try {
    const swMessage = buildSwMessage(data.subtype, payload);
    const response = await sendMessage(swMessage);
    window.postMessage(
      {
        type: "MEMOIR_EXT_RESPONSE",
        requestId: data.requestId,
        success: response.success,
        data: response.data,
        error: response.error,
      },
      window.location.origin,
    );
  } catch (err) {
    window.postMessage(
      {
        type: "MEMOIR_EXT_RESPONSE",
        requestId: data.requestId,
        success: false,
        error: (err as Error).message || "Bridge error",
      },
      window.location.origin,
    );
  }
});
