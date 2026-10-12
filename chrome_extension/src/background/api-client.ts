import type { AuthState, DashboardStats } from "../shared/types";
import { API_BASE, AUTH_BASE } from "../shared/constants";
import { loadAuth, refreshToken as doRefresh } from "./token-manager";

async function getAuthHeaders(): Promise<Record<string, string>> {
  const auth = await loadAuth();
  if (!auth) return { "Content-Type": "application/json" };
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${auth.accessToken}`,
  };
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = await getAuthHeaders();
  let response = await fetch(url, {
    ...options,
    credentials: "include",
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });

  // On 401, attempt token refresh and retry once
  if (response.status === 401) {
    const auth = await loadAuth();
    if (auth) {
      const refreshed = await doRefresh(auth);
      if (refreshed) {
        const retryHeaders: Record<string, string> = {
          "Content-Type": "application/json",
          Authorization: `Bearer ${refreshed.accessToken}`,
        };
        response = await fetch(url, {
          ...options,
          credentials: "include",
          headers: { ...retryHeaders, ...(options.headers as Record<string, string>) },
        });
      }
    }
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${response.status}`);
  }

  if (response.status === 204) return undefined as T;
  return response.json();
}

/** Login with email/password */
export async function login(
  email: string,
  password: string
): Promise<{ access_token: string; user: AuthState["user"]; teams: Array<{ id: string; name: string }> }> {
  // Step 1: login
  const tokenRes = await request<{ access_token: string }>(`${AUTH_BASE}/auth/login`, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

  // Step 2: get profile
  const profile = await request<AuthState["user"]>(`${AUTH_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${tokenRes.access_token}` } as Record<string, string>,
  });

  // Step 3: get teams
  const teams = await request<Array<{ id: string; name: string }>>(`${API_BASE}/api/teams`, {
    headers: { Authorization: `Bearer ${tokenRes.access_token}` } as Record<string, string>,
  });

  return {
    access_token: tokenRes.access_token,
    user: profile,
    teams,
  };
}

/** Create a meeting (with dedup) */
export async function createMeeting(data: {
  meeting_url?: string;
  title?: string;
  team_id: string;
  source: string;
  capture_mode?: string;
}): Promise<{ id: string; status: string }> {
  return request(`${API_BASE}/api/meetings`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

/** Get meeting status */
export async function getMeeting(meetingId: string): Promise<{
  id: string;
  status: string;
  title?: string;
  recording_started_at?: string | null;
}> {
  return request(`${API_BASE}/api/meetings/${meetingId}`);
}

/** Lightweight polling endpoint — single-SELECT, 5 ms response. Used by the
 *  floating-button polling loop. Avoids the 5-6 query fan-out of getMeeting
 *  which under sustained polling load was saturating the DB connection pool
 *  and stalling webhook handlers. See core_api/app/api/meetings.py
 *  get_meeting_status for the corresponding endpoint.
 */
export async function getMeetingStatus(meetingId: string): Promise<{
  status: string;
  intelligence_status?: string | null;
  recording_started_at?: string | null;
}> {
  return request(`${API_BASE}/api/meetings/${meetingId}/status`);
}

/** Stop a meeting */
export async function stopMeeting(meetingId: string): Promise<{ status: string }> {
  return request(`${API_BASE}/api/meetings/${meetingId}/stop`, { method: "POST" });
}

/** Extension fast-path: notify backend that host clicked Admit on the bot.
 *  Backend commits status=recording + recording_started_at immediately
 *  (Phase 2). Idempotent — bot's own joined_meeting webhook may also fire;
 *  the first to land wins, the other is a no-op.
 *
 *  Retries up to 3 times with 1s/2s backoff. If all fail, the bot's
 *  own detection eventually fires the same handler — worst case = today's
 *  behavior (slower-but-working).
 */
export async function notifyAdmitDetected(meetingId: string): Promise<{ status: string }> {
  const backoffs = [0, 1000, 2000];
  let lastErr: unknown = null;
  for (let i = 0; i < backoffs.length; i++) {
    if (backoffs[i] > 0) await new Promise((r) => setTimeout(r, backoffs[i]));
    try {
      return await request(`${API_BASE}/api/meetings/${meetingId}/admit-detected`, {
        method: "POST",
      });
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr ?? new Error("notifyAdmitDetected failed");
}

/** Upsert meeting notes */
export async function saveNotes(meetingId: string, content: string): Promise<void> {
  await request(`${API_BASE}/api/meetings/${meetingId}/notes`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

/** Get meeting notes */
export async function getNotes(meetingId: string): Promise<{ content: string }> {
  return request(`${API_BASE}/api/meetings/${meetingId}/notes`);
}

/** Get dashboard stats */
export async function getDashboardStats(teamId: string): Promise<DashboardStats> {
  return request(`${API_BASE}/api/dashboard/stats?team_id=${teamId}`);
}

/** Get the next upcoming calendar event for the team (earliest by start_time
 *  that hasn't started yet). Used by the popup's "Next up" card. */
export async function getNextMeeting(teamId: string): Promise<NextMeeting | null> {
  const events = await request<CalendarEventLite[]>(
    `${API_BASE}/api/calendar/events?team_id=${teamId}`,
  );
  if (!events?.length) return null;
  const now = Date.now();
  const future = events
    .filter((e) => e.start_time && new Date(e.start_time).getTime() > now)
    .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
  if (!future.length) return null;
  const e = future[0];
  return {
    id: e.id,
    title: e.title,
    start_time: e.start_time,
    meeting_url: e.meeting_url,
    bot_scheduled: !!e.bot_scheduled,
    minutes_until: Math.round((new Date(e.start_time).getTime() - now) / 60000),
  };
}

interface CalendarEventLite {
  id: string;
  title: string;
  start_time: string;
  meeting_url: string | null;
  bot_scheduled?: boolean;
  attendees?: { email?: string; name?: string; organizer?: boolean }[] | null;
}

export interface MeetingParticipant {
  name: string;
  email?: string;
  organizer?: boolean;
}

/** Find the calendar event whose meeting_url matches the supplied URL
 *  and return its attendees as displayable participants. Returns [] if
 *  no event matches (e.g., ad-hoc meeting not on the calendar). */
export async function getMeetingParticipants(
  teamId: string,
  meetingUrl: string,
): Promise<MeetingParticipant[]> {
  const events = await request<CalendarEventLite[]>(
    `${API_BASE}/api/calendar/events?team_id=${teamId}`,
  );
  if (!events?.length) return [];
  // Match by meeting_url. Strip trailing slashes / query params for safety.
  const norm = (u: string | null | undefined) =>
    (u || "").replace(/[?#].*$/, "").replace(/\/+$/, "");
  const target = norm(meetingUrl);
  const match = events.find((e) => norm(e.meeting_url) === target);
  if (!match?.attendees?.length) return [];
  return match.attendees
    .map<MeetingParticipant>((a) => ({
      name: a.name || (a.email ? a.email.split("@")[0] : "—"),
      email: a.email,
      organizer: a.organizer,
    }))
    .filter((p) => p.name !== "—");
}

export interface NextMeeting {
  id: string;
  title: string;
  start_time: string;
  meeting_url: string | null;
  bot_scheduled: boolean;
  minutes_until: number;
}

async function getAuthToken(): Promise<string | null> {
  const auth = await loadAuth();
  return auth?.accessToken ?? null;
}

async function requestMultipart<T>(url: string, formData: FormData): Promise<T> {
  const token = await getAuthToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(url, {
    method: "PUT",
    credentials: "include",
    headers,
    body: formData,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

async function postMultipart<T>(url: string, formData: FormData): Promise<T> {
  const token = await getAuthToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: formData,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

/** Create a tab audio upload session */
export async function createTabAudioSession(meetingId: string): Promise<{ session_id: string }> {
  return request(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions`, { method: "POST" });
}

/** Upload a single audio chunk (idempotent) */
export async function uploadTabAudioChunk(
  meetingId: string,
  sessionId: string,
  seq: number,
  chunkBlob: Blob,
  metadata: { sha256: string; startMs: number; durationMs: number; size: number; track?: string }
): Promise<{ status: string; chunk_seq: number }> {
  const fd = new FormData();
  fd.append("file", chunkBlob, `chunk_${seq.toString().padStart(4, "0")}.webm`);
  fd.append("sha256", metadata.sha256);
  fd.append("start_ms", String(metadata.startMs));
  fd.append("duration_ms", String(metadata.durationMs));
  fd.append("size", String(metadata.size));
  if (metadata.track) {
    fd.append("track", metadata.track);
  }

  return requestMultipart(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}/chunks/${seq}`, fd);
}

/**
 * Finalize session (triggers transcription).
 *
 * `participants` is optional — Phase 1 extension popup recordings don't
 * yet scrape DOM participants (those scrapers ship with the web recorder's
 * Phase 3 work), so callers pass `[]` and the backend writes an empty
 * participants array into the v2 speaker_events.json. Web recorder calls
 * pass populated rosters; backend upserts MeetingParticipant rows AND
 * populates transcription_jobs.params.participant_names so closed-vocab
 * attribution in TranscriptionBase has the roster.
 */
export async function completeTabAudioSession(
  meetingId: string,
  sessionId: string,
  speakerEvents: Array<{ speaker: string; start: number; end: number }>,
  participants: Array<{ display_name: string }> = []
): Promise<{ status: string }> {
  return request(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}/complete`, {
    method: "POST",
    body: JSON.stringify({ speaker_events: speakerEvents, participants }),
  });
}

/** Abort session (best-effort) */
export async function abortTabAudioSession(meetingId: string, sessionId: string): Promise<void> {
  await request(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}/abort`, {
    method: "POST",
  }).catch(() => {});
}

/**
 * Full-file fallback upload (supports dual-track: tab + mic).
 *
 * Symmetric with completeTabAudioSession — also forwards the participants
 * roster so flaky-network rescue uploads don't silently drop participant
 * data and leave closed-vocab attribution worse off than the happy path.
 */
export async function fallbackCompleteTabAudioSession(
  meetingId: string,
  sessionId: string,
  tabBlob: Blob,
  speakerEvents: Array<{ speaker: string; start: number; end: number }>,
  micBlob?: Blob,
  participants: Array<{ display_name: string }> = []
): Promise<{ status: string }> {
  const fd = new FormData();
  fd.append("tab_file", tabBlob, "recording_tab.webm");
  if (micBlob) {
    fd.append("mic_file", micBlob, "recording_mic.webm");
  }
  fd.append("speaker_events", JSON.stringify(speakerEvents));
  fd.append("participants", JSON.stringify(participants));
  return postMultipart(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}/fallback-complete`, fd);
}

/** Get session state (for crash recovery) */
export async function getTabAudioSession(
  meetingId: string,
  sessionId: string
): Promise<{ session_id: string; status: string; total_chunks: number; uploaded_seqs: number[] }> {
  return request(`${API_BASE}/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}`);
}
