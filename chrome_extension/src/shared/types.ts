/** Recording mode — determines how audio is captured */
export type RecordingMode = "bot" | "tab_audio";

/** Meeting lifecycle states.
 *
 *  starting_to_record is a brief intermediate state between joining and
 *  recording. It fires the moment the extension first sees
 *  meeting.status="recording" arrive at the API poll (i.e. the moment
 *  bot.join_meeting() returned True on the bot side). A 1.5 s timer then
 *  transitions to "recording" so the UI reads "Stop Recording" once the
 *  bot's recorder is plausibly capturing audio. The timer is the only
 *  exit path on the happy flow; failure / completion / stop paths cancel
 *  it via centralized cleanup inside transitionTo().
 */
export type MeetingStateValue =
  | "idle"
  | "detected"
  | "joining"
  | "starting_to_record"
  | "recording"
  | "finalizing_upload"
  | "completed";

/** Supported meeting platforms */
export type Platform = "google_meet" | "zoom" | "teams";

/** Auth state stored in chrome.storage.local */
export interface AuthState {
  accessToken: string;
  user: {
    id: string;
    email: string;
    name: string;
    avatar_url?: string;
  };
  activeTeamId: string;
  activeTeamName?: string;
}

/** Full extension state managed by background worker */
export interface ExtensionState {
  auth: AuthState | null;
  meeting: MeetingState;
}

/** Current meeting state */
export interface MeetingState {
  state: MeetingStateValue;
  platform?: Platform;
  meetingUrl?: string;
  meetingTitle?: string;
  meetingId?: string; // Memoir meeting ID after recording starts
  recordingStartedAt?: number; // timestamp ms
  tabId?: number;
  recordingMode?: RecordingMode;
  uploadSessionId?: string;
  chunkSeq?: number;
  // Last server-side meeting.status observed via /status poll. Used to pick
  // between 1.5 s and 5 s cadence — see tunePollIntervalForState().
  lastSeenServerStatus?: string;
  // Timestamp (ms) when the user clicked Stop. While set within the recent
  // window, pickPollInterval() forces POLL_FAST_MS so the status flip from
  // "recording" → "processing" is caught within ~1.5 s instead of waiting
  // for the chrome.alarms 30 s backup. Cleared once we observe a non-
  // "recording" server status.
  stopRequestedAt?: number;
}

/** Dashboard stats returned by API — must match core_api DashboardStats schema */
export interface DashboardStats {
  total_meetings: number;
  total_minutes: number;
  total_speakers: number;
  this_week_meetings: number;
  last_week_meetings: number;
}

export interface AudioInputDevice {
  deviceId: string;
  label: string;
  isDefault: boolean;
}

export type UploadAudioMode = "separate" | "mixed";

/** Messages sent between content scripts, popup, and background */
export type ExtensionMessage =
  | { type: "GET_STATE" }
  | { type: "GET_AUDIO_INPUT_DEVICES" }
  | { type: "SET_SELECTED_MIC_DEVICE"; deviceId: string | null }
  | { type: "GET_RECORDING_MODE_PREF" }
  | { type: "SET_RECORDING_MODE_PREF"; mode: UploadAudioMode }
  | { type: "LOGIN"; email: string; password: string }
  | { type: "LOGOUT" }
  | { type: "AUTH_SYNC_FROM_WEB"; accessToken: string; activeTeamId: string; activeTeamName?: string; user: AuthState["user"] }
  | { type: "AUTH_SYNC_TO_WEB"; accessToken: string; activeTeamId: string; activeTeamName?: string; user: AuthState["user"] }
  | { type: "MEETING_STATE_CHANGE"; state: "pre-join" | "in-meeting" | "ended"; platform: Platform; meetingUrl: string; meetingTitle?: string; tabId: number }
  | { type: "START_RECORDING" }
  | { type: "STOP_RECORDING" }
  | { type: "SAVE_NOTES"; content: string }
  | { type: "GET_NOTES" }
  | { type: "GET_DASHBOARD_STATS" }
  | { type: "GET_NEXT_MEETING" }
  | { type: "GET_MEETING_PARTICIPANTS" }
  // Content-script side: scrape current participants from the meeting DOM
  | { type: "SCRAPE_PARTICIPANTS_FROM_DOM" }
  | { type: "DISMISS_MEETING" }
  | { type: "START_RECORDING_TAB_AUDIO"; micDeviceId?: string; uploadMode?: UploadAudioMode }
  | { type: "START_RECORDING_BOT" }
  // Content script observed the host clicking an "Admit" / "Admit all"
  // button whose surrounding notification mentions the bot's name. The
  // SW uses this as an instant trigger for the joining -> starting_to_record
  // transition, ~3-5 s earlier than the backend poll signal would arrive.
  | { type: "BOT_ADMITTED_LOCALLY" }
  | { type: "TAB_AUDIO_CHUNK_READY"; track: "tab" | "mic" | "mixed"; seq: number; base64Audio: string; startMs: number; durationMs: number; sha256: string; size: number }
  | { type: "TAB_AUDIO_FINAL_CHUNK"; track: "tab" | "mic" | "mixed"; seq: number; base64Audio: string; startMs: number; durationMs: number; sha256: string; size: number }
  | { type: "OFFSCREEN_START_RECORDING"; streamId: string | null; micDeviceId?: string; uploadMode?: UploadAudioMode }
  | { type: "OFFSCREEN_STOP_RECORDING" }
  | { type: "OFFSCREEN_STOPPED"; hasMic: boolean; finalSeq: number }
  | { type: "OFFSCREEN_LIST_AUDIO_INPUTS" }
  | { type: "OFFSCREEN_AUDIO_INPUTS"; devices: AudioInputDevice[] }
  | { type: "OFFSCREEN_GET_FULL_FILE" }
  | { type: "TAB_AUDIO_PROGRESS"; chunkSeq: number; totalSize: number }
  | { type: "TAB_AUDIO_ERROR"; error: string }
  | { type: "ACTIVE_SPEAKER_EVENT"; speaker: string; start: number; end: number }
  | { type: "OFFSCREEN_FULL_FILE_READY"; tabBase64: string; micBase64?: string }
  // Web-recorder handoff: page on app.meetmemoir.com/record asks the extension to
  // record a specific meeting tab (no getDisplayMedia picker). Routed via the
  // memoir-app content script's window.postMessage bridge.
  | { type: "WEB_RECORDER_LIST_MEETING_TABS" }
  | { type: "WEB_RECORDER_HANDOFF_START"; tabId: number; title?: string; language?: string | null }
  | { type: "WEB_RECORDER_HANDOFF_STOP"; meetingId: string }
  // Ad-hoc recording (no meeting tab): used by both the popup "Start
  // recording" CTA and /record's mic-only fallback when extension is installed.
  // Internally opens the offscreen document in mic-only mode (no tabCapture).
  | { type: "START_RECORDING_AD_HOC"; title?: string }
  // Mic device sync between page and extension (bridge subtypes).
  | { type: "WEB_RECORDER_GET_MIC_DEVICE" }
  | { type: "WEB_RECORDER_SET_MIC_DEVICE"; deviceId: string | null }
  // Cross-surface recording state — /record polls this so its timer
  // freezes the moment the extension popup stops the recording (without
  // waiting for the backend status flip after upload finishes).
  | { type: "WEB_RECORDER_GET_RECORDING_STATE" };

/** Web-recorder handoff: minimal info about a candidate meeting tab. */
export interface MeetingTabInfo {
  tabId: number;
  url: string;
  title: string;
  favIconUrl?: string;
  platform: Platform;
}

/** Response wrapper */
export interface ExtensionResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
}
