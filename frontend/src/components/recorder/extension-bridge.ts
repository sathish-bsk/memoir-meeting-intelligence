/**
 * Page-side bridge to the Memoir Chrome extension.
 *
 * The extension's memoir-app content script (chrome_extension/src/content/memoir-app/auth-sync.ts)
 * relays whitelisted message types to the service worker and posts the response
 * back via window.postMessage. We correlate request/response by a generated id.
 *
 * The extension's presence is signalled by `<html data-memoir-ext="true">`,
 * set by the same content script on load.
 */

export type ExtensionRequestSubtype =
  | "LIST_MEETING_TABS"
  | "HANDOFF_START"
  | "HANDOFF_STOP"
  | "AD_HOC_START"
  | "GET_MIC_DEVICE"
  | "SET_MIC_DEVICE"
  | "GET_RECORDING_STATE";

export interface MeetingTabInfo {
  tabId: number;
  url: string;
  title: string;
  favIconUrl?: string;
  platform: "google_meet" | "zoom" | "teams";
}

interface BridgeResponse {
  type: "MEMOIR_EXT_RESPONSE";
  requestId: string;
  success: boolean;
  data?: unknown;
  error?: string;
}

let counter = 0;
function nextRequestId(): string {
  counter += 1;
  return `memoir-ext-${Date.now()}-${counter}`;
}

export function isExtensionInstalled(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.dataset.memoirExt === "true";
}

export class ExtensionBridgeError extends Error {
  constructor(
    public readonly kind: "timeout" | "no_extension" | "bridge_error",
    message: string,
  ) {
    super(message);
    this.name = "ExtensionBridgeError";
  }
}

export async function postMessageToExtension<T>(
  subtype: ExtensionRequestSubtype,
  payload: Record<string, unknown> = {},
  timeoutMs = 3000,
): Promise<T> {
  if (!isExtensionInstalled()) {
    throw new ExtensionBridgeError("no_extension", "Memoir extension not installed");
  }

  return new Promise<T>((resolve, reject) => {
    const requestId = nextRequestId();
    let settled = false;

    const finish = () => {
      settled = true;
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };

    const onMessage = (event: MessageEvent) => {
      if (settled) return;
      if (event.source !== window) return;
      if (event.origin !== window.location.origin) return;
      const data = event.data as BridgeResponse | undefined;
      if (!data || data.type !== "MEMOIR_EXT_RESPONSE") return;
      if (data.requestId !== requestId) return;
      finish();
      if (data.success) {
        resolve(data.data as T);
      } else {
        reject(
          new ExtensionBridgeError(
            "bridge_error",
            data.error || `Extension request failed: ${subtype}`,
          ),
        );
      }
    };

    const timer = window.setTimeout(() => {
      if (settled) return;
      finish();
      reject(
        new ExtensionBridgeError(
          "timeout",
          `Extension request timed out after ${timeoutMs}ms: ${subtype}`,
        ),
      );
    }, timeoutMs);

    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "MEMOIR_EXT_REQUEST",
        requestId,
        subtype,
        payload,
      },
      window.location.origin,
    );
  });
}

/** List open meeting tabs (Meet / Zoom / Teams). */
export async function listMeetingTabs(): Promise<MeetingTabInfo[]> {
  const result = await postMessageToExtension<{ tabs: MeetingTabInfo[] }>(
    "LIST_MEETING_TABS",
    {},
    2000,
  );
  return result?.tabs ?? [];
}

/** Ask the extension to start recording the given tab + the user's mic. */
export async function startHandoffRecording(args: {
  tabId: number;
  title?: string;
  language?: string | null;
}): Promise<{ meetingId: string; sessionId: string }> {
  return postMessageToExtension<{ meetingId: string; sessionId: string }>(
    "HANDOFF_START",
    args as Record<string, unknown>,
    8000,
  );
}

/** Ask the extension to stop a handoff recording in progress. */
export async function stopHandoffRecording(meetingId: string): Promise<{ ok: true }> {
  return postMessageToExtension<{ ok: true }>(
    "HANDOFF_STOP",
    { meetingId },
    10000,
  );
}

/** Ask the extension to start an ad-hoc mic-only recording (no meeting tab). */
export async function startAdHocRecording(args: {
  title?: string;
}): Promise<{ meetingId: string; sessionId: string }> {
  return postMessageToExtension<{ meetingId: string; sessionId: string }>(
    "AD_HOC_START",
    args as Record<string, unknown>,
    8000,
  );
}

/** Read the currently-selected mic deviceId from the extension. */
export async function getExtensionMicDevice(): Promise<string | null> {
  const result = await postMessageToExtension<{ deviceId: string | null }>(
    "GET_MIC_DEVICE",
    {},
    2000,
  );
  return result?.deviceId ?? null;
}

/** Tell the extension to set the selected mic deviceId. */
export async function setExtensionMicDevice(deviceId: string | null): Promise<void> {
  await postMessageToExtension<unknown>(
    "SET_MIC_DEVICE",
    { deviceId },
    2000,
  );
}

/** Possible recording states from the extension's meetingState. */
export type ExtensionRecordingState =
  | "idle"
  | "detected"
  | "joining"
  | "starting_to_record"
  | "recording"
  | "finalizing_upload"
  | "completed";

/** Get the extension's current recording-state snapshot. Used by /record's
 *  poller to freeze the timer the instant the popup stops the recording. */
export async function getExtensionRecordingState(): Promise<{
  state: ExtensionRecordingState;
  meetingId: string | null;
  recordingStartedAt: number | null;
}> {
  return postMessageToExtension<{
    state: ExtensionRecordingState;
    meetingId: string | null;
    recordingStartedAt: number | null;
  }>("GET_RECORDING_STATE", {}, 2000);
}
