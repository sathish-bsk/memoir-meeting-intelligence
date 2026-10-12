import { useAuthStore } from "@/store/auth";
import type {
  TokenResponse,
  UserProfile,
  Team,
  TeamMember,
  Meeting,
  MeetingDetail,
  MeetingListResponse,
  TranscriptResponse,
  CoachingResponse,
  IntelligenceResponse,
  MemoirResponse,
  EnhancedSearchResponse,
  DashboardStats,
  PlanInfo,
  AdminTeamLookup,
  CalendarConnection,
  CalendarEvent,
  CalendarEventListV3,
  CalendarSyncResponse,
  SlackConnection,
  SlackChannel,
  SlackNotificationSettings,
  ZoomConnection,
  TeamsCloudConnection,
  TranscriptionVersion,
  MeetingSpeakersResponse,
  SpeakerProfile,
  IntelligenceOverview,
  PainPointAggregation,
  FeatureRequestAggregation,
  CompetitorAggregation,
  ObjectionAggregation,
  QuestionAggregation,
  PMFTrendPoint,
  DigestResponse,
  DigestListResponse,
  DigestSettingsResponse,
  DigestSettingsUpdate,
  JobRunListResponse,
  CallIntelligenceResponse,
  CrmConnection,
  CrmPushResult,
  CrmProperty,
  CrmStage,
  DealStagesResponse,
  DealStageCallsResponse,
  DealTimelineResponse,
  PmfSignalsResponse,
  PmfSignalCallsResponse,
  PmfHistoryResponse,
  CrmPipelineResponse,
  AdminOverview,
  AdminActivity,
  AdminCostSummary,
  AdminCostTrendPoint,
  AdminTeamCostRow,
  AdminMeetingCostRow,
  AdminCustomerRow,
  AdminCustomerDetail,
  AdminChurnRiskRow,
  AdminProcessingStats,
  AdminProcessingFailure,
} from "./types";

import { apiRequestStart, apiRequestEnd } from "./api-loading";

// Empty string = relative URLs. Next.js rewrites proxy /auth/* and /api/* to backend services.
// In production behind Traefik gateway, the same relative paths work directly.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

class ApiClient {
  private getHeaders(): HeadersInit {
    const token = useAuthStore.getState().accessToken;
    const headers: HeadersInit = {
      "Content-Type": "application/json",
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  private async request<T>(
    url: string,
    options: RequestInit = {},
    opts: { silent?: boolean } = {},
  ): Promise<T> {
    // Polling endpoints (e.g. /processing-status, in-flight artifact
    // refetches) should NOT trigger the global loader bar — without this
    // the v3 detail page flickered the top bar every 5 s during the
    // pipeline. Pass {silent: true} from the call site to opt out.
    if (!opts.silent) apiRequestStart();
    try {
      const response = await fetch(`${API_URL}${url}`, {
        ...options,
        credentials: "include",
        headers: {
          ...this.getHeaders(),
          ...options.headers,
        },
      });

      if (response.status === 401) {
        const isAuthEndpoint = url.startsWith("/auth/login") || url.startsWith("/auth/register") || url.startsWith("/auth/forgot");
        if (isAuthEndpoint) {
          const error = await response.json().catch(() => ({}));
          throw new ApiError(401, error.detail || "Invalid email or password");
        }
        const refreshed = await useAuthStore.getState().refresh();
        if (refreshed) {
          const retryResponse = await fetch(`${API_URL}${url}`, {
            ...options,
            credentials: "include",
            headers: {
              ...this.getHeaders(),
              ...options.headers,
            },
          });
          if (!retryResponse.ok) {
            const error = await retryResponse.json().catch(() => ({}));
            throw new ApiError(
              retryResponse.status,
              error.detail || `API Error: ${retryResponse.status}`,
            );
          }
          if (retryResponse.status === 204) return undefined as T;
          return retryResponse.json();
        }
        useAuthStore.getState().logout();
        throw new ApiError(401, "Session expired");
      }

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new ApiError(
          response.status,
          error.detail || `API Error: ${response.status}`,
        );
      }

      if (response.status === 204) return undefined as T;
      return response.json();
    } finally {
      if (!opts.silent) apiRequestEnd();
    }
  }

  // ─── OAuth ─────────────────────────────────────────────────────

  async getGoogleAuthUrl(): Promise<{ redirect_url: string; state: string }> {
    return this.request("/auth/oauth/google/authorize");
  }

  async handleGoogleAuthCallback(code: string, state: string): Promise<TokenResponse> {
    return this.request("/auth/oauth/google/callback", {
      method: "POST",
      body: JSON.stringify({ code, state }),
    });
  }

  async getMicrosoftAuthUrl(): Promise<{ redirect_url: string; state: string }> {
    return this.request("/auth/oauth/microsoft/authorize");
  }

  async handleMicrosoftAuthCallback(code: string, state: string): Promise<TokenResponse> {
    return this.request("/auth/oauth/microsoft/callback", {
      method: "POST",
      body: JSON.stringify({ code, state }),
    });
  }

  // ─── Auth ──────────────────────────────────────────────────────
  // Login/signup return { access_token, token_type, expires_in }.
  // Refresh token is set as httpOnly cookie by the server.

  async login(email: string, password: string) {
    return this.request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  async signup(data: { email: string; password: string; name: string }) {
    return this.request<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async refreshToken() {
    return this.request<TokenResponse>("/auth/refresh", {
      method: "POST",
    });
  }

  async logout() {
    return this.request<{ message: string }>("/auth/logout", {
      method: "POST",
    });
  }

  async getProfile() {
    return this.request<UserProfile>("/auth/me");
  }

  async updateProfile(data: {
    name?: string;
    avatar_url?: string;
    onboarding_completed?: boolean;
  }) {
    return this.request<UserProfile>("/auth/me", {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async switchTeam(teamId: string) {
    return this.request<TokenResponse>("/auth/switch-team", {
      method: "POST",
      body: JSON.stringify({ team_id: teamId }),
    });
  }

  async changeEmail(data: { new_email: string; password: string }) {
    return this.request<{ message: string }>("/auth/change-email", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async changePassword(data: {
    current_password: string;
    new_password: string;
  }) {
    return this.request<{ message: string }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async resendVerification() {
    return this.request<{ message: string }>("/auth/resend-verification", {
      method: "POST",
    });
  }

  async verifyEmail(token: string) {
    return this.request<{ message: string }>(
      `/auth/verify-email?token=${encodeURIComponent(token)}`,
    );
  }

  async verifyEmailChange(token: string) {
    return this.request<{ message: string }>(
      `/auth/verify-email-change?token=${encodeURIComponent(token)}`,
    );
  }

  // ─── Teams ─────────────────────────────────────────────────────

  async getTeams() {
    return this.request<Team[]>("/api/teams");
  }

  async createTeam(data: {
    id?: string;
    name: string;
    crm_type?: string;
    team_size_range?: string;
    intelligence_priority?: string;
  }) {
    return this.request<Team>("/api/teams", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getTeam(teamId: string) {
    return this.request<Team>(`/api/teams/${teamId}`);
  }

  async updateTeam(teamId: string, data: Record<string, unknown>) {
    return this.request<Team>(`/api/teams/${teamId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async getTeamMembers(teamId: string) {
    return this.request<TeamMember[]>(`/api/teams/${teamId}/members`);
  }

  async inviteToTeam(teamId: string, email: string) {
    return this.request<{ invite_code: string; team_name: string }>(
      `/api/teams/${teamId}/invite`,
      { method: "POST", body: JSON.stringify({ email }) },
    );
  }

  async joinTeam(inviteCode: string) {
    return this.request<Team>("/api/teams/join", {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode }),
    });
  }

  async removeTeamMember(teamId: string, memberId: string) {
    return this.request<{ message: string }>(
      `/api/teams/${teamId}/members/${memberId}`,
      { method: "DELETE" },
    );
  }

  async reinviteTeamMember(teamId: string, memberId: string) {
    return this.request<{ message: string }>(
      `/api/teams/${teamId}/members/${memberId}/reinvite`,
      { method: "POST" },
    );
  }

  async getInviteDetails(code: string) {
    return this.request<{ email: string | null; account_exists: boolean }>(
      `/api/teams/invite/details?code=${encodeURIComponent(code)}`,
    );
  }

  async lookupTeamByDomain(domain: string) {
    return this.request<{ found: boolean; teams: { id: string; name: string }[] }>(
      `/api/teams/lookup-by-domain?domain=${encodeURIComponent(domain)}`,
    );
  }

  async requestJoinTeam(teamId: string, message?: string) {
    return this.request<{ status: string; team_name: string }>(
      `/api/teams/${teamId}/request-join`,
      {
        method: "POST",
        body: JSON.stringify({ message: message || null }),
      },
    );
  }

  async approveJoinRequest(teamId: string, memberId: string) {
    return this.request<{ message: string }>(
      `/api/teams/${teamId}/members/${memberId}/approve`,
      { method: "POST" },
    );
  }

  async declineJoinRequest(teamId: string, memberId: string) {
    return this.request<{ message: string }>(
      `/api/teams/${teamId}/members/${memberId}/decline`,
      { method: "POST" },
    );
  }

  async revokeInvite(teamId: string, memberId: string) {
    return this.request<{ message: string }>(
      `/api/teams/${teamId}/members/${memberId}/revoke`,
      { method: "POST" },
    );
  }

  // ─── Permissions ───────────────────────────────────────────────

  async changeMemberRole(teamId: string, memberUserId: string, role: string) {
    return this.request<{ message: string }>(
      `/auth/teams/${teamId}/members/${memberUserId}/role`,
      { method: "PATCH", body: JSON.stringify({ role }) },
    );
  }

  async getMemberScopes(teamId: string, memberUserId: string) {
    return this.request<{
      effective_scopes: string[];
      role_defaults: string[];
      overrides: Record<string, boolean>;
    }>(`/auth/teams/${teamId}/members/${memberUserId}/scopes`);
  }

  async updateMemberScopes(
    teamId: string,
    memberUserId: string,
    overrides: Record<string, boolean>,
  ) {
    return this.request<{
      effective_scopes: string[];
      role_defaults: string[];
      overrides: Record<string, boolean>;
    }>(`/auth/teams/${teamId}/members/${memberUserId}/scopes`, {
      method: "PATCH",
      body: JSON.stringify({ overrides }),
    });
  }

  // ─── Meetings ──────────────────────────────────────────────────

  async getMeetings(params: {
    team_id: string;
    page?: number;
    per_page?: number;
    status?: string;
    has_recording?: boolean;
    start_date?: string;
    end_date?: string;
    source?: string;
    crm_push_status?: string;
    confidence_min?: number;
    confidence_max?: number;
    filter_ids?: string;
    scope?: "hosted" | "shared" | "all";
    platform?: "meet" | "zoom" | "teams";
    /** "created_at" (default) | "activity". v3 callers pass "activity" so
     *  the day-detail list aligns with the /week-activity strip dots. */
    date_basis?: "created_at" | "activity";
  }) {
    const searchParams = new URLSearchParams({ team_id: params.team_id });
    if (params.page) searchParams.set("page", String(params.page));
    if (params.per_page)
      searchParams.set("per_page", String(params.per_page));
    if (params.status) searchParams.set("status", params.status);
    if (params.has_recording !== undefined)
      searchParams.set("has_recording", String(params.has_recording));
    if (params.start_date) searchParams.set("start_date", params.start_date);
    if (params.end_date) searchParams.set("end_date", params.end_date);
    if (params.source) searchParams.set("source", params.source);
    if (params.crm_push_status) searchParams.set("crm_push_status", params.crm_push_status);
    if (params.confidence_min !== undefined)
      searchParams.set("confidence_min", String(params.confidence_min));
    if (params.confidence_max !== undefined)
      searchParams.set("confidence_max", String(params.confidence_max));
    if (params.filter_ids) searchParams.set("filter_ids", params.filter_ids);
    if (params.scope) searchParams.set("scope", params.scope);
    if (params.platform) searchParams.set("platform", params.platform);
    if (params.date_basis) searchParams.set("date_basis", params.date_basis);
    return this.request<MeetingListResponse>(
      `/api/meetings?${searchParams.toString()}`,
    );
  }

  async getMeeting(meetingId: string) {
    return this.request<MeetingDetail>(`/api/meetings/${meetingId}`);
  }

  async approveExternalShare(meetingId: string) {
    return this.request<{ status: string; recipients_count: number }>(
      `/api/meetings/${meetingId}/approve-external-share`,
      { method: "POST" },
    );
  }

  async createMeeting(data: {
    meeting_url: string;
    title?: string;
    team_id: string;
    scheduled_start_at?: string;
  }) {
    return this.request<Meeting>("/api/meetings", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  /** Create a meeting for the in-browser web recorder.
   *
   * No meeting_url is sent — backend validates source=web_recorder
   * implies meeting_url is NULL. `language` flows through to
   * transcription_jobs.params.language so the recorded audio is
   * transcribed in the user's selected language (NULL = multilingual).
   */
  async createWebRecorderMeeting(data: {
    title?: string;
    team_id: string;
    language?: string | null;
  }) {
    return this.request<Meeting>("/api/meetings", {
      method: "POST",
      body: JSON.stringify({
        source: "web_recorder",
        capture_mode: "web_recorder",
        title: data.title,
        team_id: data.team_id,
        language: data.language ?? null,
      }),
    });
  }

  /** Create an upload session for the web recorder's chunked WebM uploads. */
  async createTabAudioSession(meetingId: string) {
    return this.request<{ session_id: string; status: string }>(
      `/api/meetings/${meetingId}/tab-audio/sessions`,
      { method: "POST" },
    );
  }

  /** Cancel a web recorder meeting (always soft-fails — see plan §7.1.6.5). */
  async cancelWebRecorderMeeting(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/web-recorder-cancel`,
      { method: "POST" },
    );
  }

  /** Finalize a web recorder upload session.
   *
   * Routes through the standard request wrapper so 401 expiry during
   * long recordings (15-min JWT TTL) triggers refresh-and-retry. A
   * hand-rolled fetch here would surface as a "Finalize failed: HTTP 401"
   * to the user and leak the meeting to the sweeper.
   */
  async completeWebRecorderSession(
    meetingId: string,
    sessionId: string,
    payload: { speaker_events: unknown[]; participants: { display_name: string }[] },
  ) {
    return this.request<{ status: string }>(
      `/api/meetings/${meetingId}/tab-audio/sessions/${sessionId}/complete`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  }

  /** Read the current Bearer token (post-refresh). Chunk uploader uses
   * this before each PUT so long recordings survive the 15-min JWT TTL.
   * NOT a substitute for the request-wrapper's full 401 refresh flow —
   * if a chunk PUT still 401s, the uploader's retry loop catches it. */
  getAccessToken(): string | null {
    return useAuthStore.getState().accessToken;
  }

  async getMeetingsActivity(teamId: string, days = 60) {
    return this.request<{ days: { date: string; count: number }[]; total: number }>(
      `/api/meetings/activity?team_id=${encodeURIComponent(teamId)}&days=${days}`,
    );
  }

  async getMeetingsStats(teamId: string) {
    return this.request<{ total: number; total_seconds: number; this_week: number }>(
      `/api/meetings/stats?team_id=${encodeURIComponent(teamId)}`,
    );
  }

  // ── Calendar connection health (Phase 2 Track B) ─────────────────
  async refreshCalendarHealthOnSignin() {
    return this.request<{ queued: number }>(
      `/api/calendar/health/refresh`,
      { method: "POST" },
    );
  }

  async getCalendarHealthStatus() {
    return this.request<{
      needs_reconnect: boolean;
      connections: Array<{
        connection_id: string;
        provider: string;
        provider_email: string | null;
        auto_record: boolean;
        token_invalid: boolean;
        token_invalid_at: string | null;
        watch_expires_at: string | null;
      }>;
    }>(`/api/calendar/health/status`);
  }

  async stopMeeting(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/stop`,
      { method: "POST" },
    );
  }

  async deleteMeeting(meetingId: string) {
    return this.request<void>(`/api/meetings/${meetingId}`, {
      method: "DELETE",
    });
  }

  // ─── Transcripts & Intelligence ────────────────────────────────

  async getTranscript(meetingId: string, version?: number) {
    const params = version != null ? `?version=${version}` : "";
    return this.request<TranscriptResponse>(
      `/api/meetings/${meetingId}/transcription${params}`,
    );
  }

  async getCoaching(meetingId: string, version?: number) {
    const params = version != null ? `?version=${version}` : "";
    return this.request<CoachingResponse>(
      `/api/meetings/${meetingId}/coaching${params}`,
    );
  }

  async getIntelligence(meetingId: string, version?: number) {
    const params = version != null ? `?version=${version}` : "";
    return this.request<IntelligenceResponse>(
      `/api/meetings/${meetingId}/intelligence${params}`,
    );
  }

  async getMemoir(meetingId: string, version?: number) {
    const params = version != null ? `?version=${version}` : "";
    return this.request<MemoirResponse>(
      `/api/meetings/${meetingId}/memoir${params}`,
    );
  }

  async getVersions(meetingId: string) {
    return this.request<{ versions: TranscriptionVersion[] }>(
      `/api/meetings/${meetingId}/versions`,
    );
  }

  getRecordingUrl(meetingId: string): string {
    const token = useAuthStore.getState().accessToken;
    return `${API_URL}/api/meetings/${meetingId}/recording${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  }

  /** Presigned URL whose response carries Content-Disposition: attachment.
   *  Use this for "Download recording" buttons — the browser saves the
   *  file instead of streaming it inline. */
  async getRecordingDownloadUrl(meetingId: string, version: "preprocessed" | "raw" | "enhanced" = "preprocessed") {
    return this.request<{ url: string; filename: string; version: string }>(
      `/api/meetings/${meetingId}/recording/download-url?version=${version}`,
    );
  }

  async getRecordingPresignedUrl(meetingId: string): Promise<string> {
    const data = await this.request<{ url: string }>(`/api/meetings/${meetingId}/recording-url`);
    return data.url;
  }

  async getAudioUrls(meetingId: string): Promise<Record<string, string>> {
    return this.request<Record<string, string>>(`/api/meetings/${meetingId}/audio-urls`);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  async getRawAsr(meetingId: string, version?: number): Promise<Record<string, unknown>[]> {
    const q = version != null ? `?version=${version}` : "";
    return this.request<Record<string, unknown>[]>(`/api/meetings/${meetingId}/raw-asr${q}`);
  }

  /** @deprecated Use pushToCrm(meetingId, provider) instead */
  async pushToCrmLegacy(meetingId: string) {
    return this.request(`/api/meetings/${meetingId}/memoir/push-crm`, {
      method: "POST",
    });
  }

  /** @deprecated Use skipCrmPush(meetingId, provider) instead */
  async skipCrmPushLegacy(meetingId: string) {
    return this.request(`/api/meetings/${meetingId}/memoir/skip`, {
      method: "POST",
    });
  }

  async uploadFromLink(data: { url: string; team_id: string; title?: string }) {
    return this.request<Meeting>("/api/meetings/upload-link", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async uploadRecording(file: File, teamId: string, title?: string) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("team_id", teamId);
    if (title) formData.append("title", title);

    const token = useAuthStore.getState().accessToken;
    const headers: HeadersInit = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const resp = await fetch(`${API_URL}/api/meetings/upload`, {
      method: "POST",
      credentials: "include",
      headers,
      body: formData,
    });

    if (resp.status === 401) {
      const refreshed = await useAuthStore.getState().refresh();
      if (refreshed) {
        const retryToken = useAuthStore.getState().accessToken;
        const retryHeaders: HeadersInit = {};
        if (retryToken) retryHeaders["Authorization"] = `Bearer ${retryToken}`;
        const retry = await fetch(`${API_URL}/api/meetings/upload`, {
          method: "POST",
          credentials: "include",
          headers: retryHeaders,
          body: formData,
        });
        if (!retry.ok) {
          const error = await retry.json().catch(() => ({}));
          throw new ApiError(retry.status, error.detail || `Upload failed: ${retry.status}`);
        }
        return retry.json() as Promise<Meeting>;
      }
      useAuthStore.getState().logout();
      throw new ApiError(401, "Session expired");
    }

    if (!resp.ok) {
      const error = await resp.json().catch(() => ({}));
      throw new ApiError(resp.status, error.detail || `Upload failed: ${resp.status}`);
    }
    return resp.json() as Promise<Meeting>;
  }

  async getMeetingNotes(meetingId: string) {
    return this.request<{ meeting_id: string; user_id: string; content: string; updated_at: string | null }>(
      `/api/meetings/${meetingId}/notes`,
    );
  }

  async saveMeetingNotes(meetingId: string, content: string) {
    return this.request<{ meeting_id: string; user_id: string; content: string; updated_at: string | null }>(
      `/api/meetings/${meetingId}/notes`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    );
  }

  async retranscribe(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/retranscribe`,
      { method: "POST" },
    );
  }

  async rerunIntelligence(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/rerun-intelligence`,
      { method: "POST" },
    );
  }

  async getProcessingStatus(meetingId: string) {
    return this.request<{
      transcription: string;
      summary: string;
      intelligence: string;
      crm_extraction: string;
      active_stage: string;
      stages: Record<string, { percent: number; label: string }>;
    }>(
      `/api/meetings/${meetingId}/processing-status`,
      {},
      { silent: true },  // 5s poll — never trigger the global loader bar
    );
  }

  // ─── Email from Call Detail ──────────────────────────────────

  async getEmailPreview(meetingId: string, template: string = "external") {
    return this.request<{ subject: string; html_body: string; template: string }>(
      `/api/meetings/${meetingId}/email-preview?template=${template}`,
    );
  }

  async sendMeetingEmail(meetingId: string, toEmail: string, template: string = "external", recipientName?: string) {
    return this.request<{ status: string; to_email: string }>(
      `/api/meetings/${meetingId}/send-email`,
      {
        method: "POST",
        body: JSON.stringify({ to_email: toEmail, template, recipient_name: recipientName }),
      },
    );
  }

  async getEmailLog(meetingId: string) {
    return this.request<Array<{
      id: string;
      to_email: string;
      status: string;
      trigger_type: string;
      created_at: string;
    }>>(`/api/meetings/${meetingId}/email-log`);
  }

  // ─── Inline Editing ───────────────────────────────────────────

  async editTranscriptSegment(meetingId: string, segmentIndex: number, text: string) {
    return this.request<{ status: string; segment_index: number }>(
      `/api/meetings/${meetingId}/transcription`,
      {
        method: "PATCH",
        body: JSON.stringify({ segment_index: segmentIndex, text }),
      },
    );
  }

  async resetTranscript(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/transcript/reset`,
      { method: "POST" },
    );
  }

  async editMemoir(meetingId: string, data: {
    narrative_summary?: string;
    action_items?: Array<{
      text: string;
      type: string;
      completed?: boolean;
      owner?: string;
      owner_email?: string;
      urgency?: string;
      context?: string | null;
    }>;
    signals?: { buying: string[]; risk: string[] };
    next_steps?: Record<string, unknown>;
  }) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/memoir`,
      {
        method: "PATCH",
        body: JSON.stringify(data),
      },
    );
  }

  async resetMemoir(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/memoir/reset`,
      { method: "POST" },
    );
  }

  // ─── Speakers ───────────────────────────────────────────────────

  async getMeetingSpeakers(meetingId: string) {
    return this.request<MeetingSpeakersResponse>(
      `/api/meetings/${meetingId}/speakers`,
    );
  }

  async assignSpeaker(
    meetingId: string,
    data: {
      diarization_label: string;
      speaker_name: string;
      speaker_profile_id?: string;
      email?: string;
    },
  ) {
    return this.request<{
      status: string;
      diarization_label: string;
      speaker_name: string;
      speaker_profile_id: string;
      segments_updated: number;
    }>(`/api/meetings/${meetingId}/speakers`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async getTeamSpeakers(teamId: string, query?: string) {
    const params = new URLSearchParams({ team_id: teamId });
    if (query) params.set("q", query);
    return this.request<SpeakerProfile[]>(
      `/api/speakers?${params.toString()}`,
    );
  }

  async mergeSpeakerProfile(sourceId: string, targetId: string, dryRun = false) {
    const qs = dryRun ? "?dry_run=true" : "";
    return this.request<{
      source_id: string;
      source_name: string;
      target_id: string;
      target_name: string;
      participants_to_move: number;
      samples_to_move: number;
      talk_seconds_to_add: number;
      meeting_count_to_add: number;
      dry_run: boolean;
    }>(`/api/speakers/${sourceId}/merge-into/${targetId}${qs}`, { method: "POST" });
  }

  // ─── Plan ───────────────────────────────────────────────────────

  async getTeamPlan(teamId: string) {
    return this.request<PlanInfo>(`/auth/teams/${teamId}/plan`);
  }

  // ─── Super Admin ──────────────────────────────────────────────

  async adminLookupTeam(email: string) {
    return this.request<AdminTeamLookup>(
      `/auth/internal/admin/lookup-team?email=${encodeURIComponent(email)}`,
    );
  }

  async adminUpdatePlan(
    email: string,
    plan: string,
    planLimits?: Record<string, number>,
  ) {
    return this.request<{ message: string; team_id: string; plan: string; limits: Record<string, number> }>(
      "/auth/internal/admin/update-plan",
      {
        method: "PUT",
        body: JSON.stringify({
          email,
          plan,
          plan_limits: planLimits || null,
        }),
      },
    );
  }

  // ─── Search ────────────────────────────────────────────────────

  async search(query: string, teamId: string, opts?: {
    resultType?: "all" | "call" | "transcript";
    page?: number;
    perPage?: number;
  }) {
    const params = new URLSearchParams({ q: query, team_id: teamId });
    if (opts?.resultType) params.set("result_type", opts.resultType);
    if (opts?.page) params.set("page", String(opts.page));
    if (opts?.perPage) params.set("per_page", String(opts.perPage));
    return this.request<EnhancedSearchResponse>(`/api/search?${params}`);
  }

  // ─── Dashboard ─────────────────────────────────────────────────

  async getDashboardStats(teamId: string, userId?: string) {
    const params = new URLSearchParams({ team_id: teamId });
    if (userId) params.set("user_id", userId);
    return this.request<DashboardStats>(`/api/dashboard/stats?${params.toString()}`);
  }

  // ─── Calendar Integration (user-based) ─────────────────────────

  async connectGoogleCalendar() {
    return this.request<{ auth_url: string }>(
      "/integrations/calendar/connect/google",
      { method: "POST" },
    );
  }

  async handleGoogleCallback(code: string, state: string) {
    return this.request<CalendarConnection>(
      "/integrations/calendar/callback/google",
      {
        method: "POST",
        body: JSON.stringify({ code, state }),
      },
    );
  }

  async connectOutlookCalendar() {
    return this.request<{ auth_url: string }>(
      "/integrations/calendar/connect/outlook",
      { method: "POST" },
    );
  }

  async handleOutlookCallback(code: string, state: string) {
    return this.request<CalendarConnection>(
      "/integrations/calendar/callback/outlook",
      {
        method: "POST",
        body: JSON.stringify({ code, state }),
      },
    );
  }

  async disconnectCalendar(provider?: string) {
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    const qs = params.toString();
    return this.request<{ message: string }>(
      `/integrations/calendar/disconnect${qs ? `?${qs}` : ""}`,
      { method: "DELETE" },
    );
  }

  async getCalendarStatus(provider?: string) {
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    const qs = params.toString();
    return this.request<CalendarConnection>(
      `/integrations/calendar/status${qs ? `?${qs}` : ""}`,
    );
  }

  async syncCalendar(provider?: string) {
    const params = new URLSearchParams();
    if (provider) params.set("provider", provider);
    const qs = params.toString();
    return this.request<CalendarSyncResponse>(
      `/integrations/calendar/sync${qs ? `?${qs}` : ""}`,
      { method: "POST" },
    );
  }

  async updateCalendarAutoRecord(connectionId: string, enabled: boolean) {
    return this.request<{ connection_id: string; auto_record: boolean; provider: string }>(
      `/api/calendar/auto-record?connection_id=${encodeURIComponent(connectionId)}&enabled=${enabled}`,
      { method: "PUT" },
    );
  }

  async getCalendarEvents(teamId: string) {
    return this.request<CalendarEvent[]>(
      `/api/calendar/events?team_id=${encodeURIComponent(teamId)}`,
    );
  }

  /** v3 calendar events list (plan/meetings-events-v3-list.md §3.4).
   *  Returns envelope with current_user_email + current_user_role. */
  async getCalendarEventsV3(opts: {
    teamId: string;
    scope?: "hosted" | "participant" | "all";
    includePast?: boolean;
    startDate?: string;
    endDate?: string;
  }) {
    const qs = new URLSearchParams({ team_id: opts.teamId });
    if (opts.scope) qs.set("scope", opts.scope);
    if (opts.includePast) qs.set("include_past", "true");
    if (opts.startDate) qs.set("start_date", opts.startDate);
    if (opts.endDate) qs.set("end_date", opts.endDate);
    return this.request<CalendarEventListV3>(
      `/api/calendar/events/v3?${qs.toString()}`,
    );
  }

  /** Per-day calendar-event counts for the v3 DateStrip.
   *  `tzOffsetMinutes` is minutes east of UTC (e.g., IST=330). Backend
   *  shifts timestamps by this offset before grouping so the dots align
   *  with the user's local-day boundaries. */
  async getCalendarEventsWeekActivityV3(opts: {
    teamId: string;
    scope?: "hosted" | "participant" | "all";
    rangeStart: string;
    rangeEnd: string;
    tzOffsetMinutes: number;
  }) {
    const qs = new URLSearchParams({
      team_id: opts.teamId,
      range_start: opts.rangeStart,
      range_end: opts.rangeEnd,
      tz_offset_minutes: String(opts.tzOffsetMinutes),
    });
    if (opts.scope) qs.set("scope", opts.scope);
    return this.request<Record<string, number>>(
      `/api/calendar/events/week-activity?${qs.toString()}`,
    );
  }

  /** Per-day meeting counts for the v3 DateStrip. Mirrors the
   *  scope/role logic of getMeetings(). */
  async getMeetingsWeekActivityV3(opts: {
    teamId: string;
    scope?: "hosted" | "shared" | "all";
    rangeStart: string;
    rangeEnd: string;
    tzOffsetMinutes: number;
  }) {
    const qs = new URLSearchParams({
      team_id: opts.teamId,
      range_start: opts.rangeStart,
      range_end: opts.rangeEnd,
      tz_offset_minutes: String(opts.tzOffsetMinutes),
    });
    if (opts.scope) qs.set("scope", opts.scope);
    return this.request<Record<string, number>>(
      `/api/meetings/week-activity?${qs.toString()}`,
    );
  }

  async scheduleBot(eventId: string, title?: string) {
    return this.request<{ meeting_id: string; status: string; event_id: string }>(
      `/api/calendar/events/${eventId}/schedule-bot`,
      {
        method: "POST",
        body: JSON.stringify({ title: title || null }),
      },
    );
  }

  async cancelBot(eventId: string) {
    return this.request<{ message: string; event_id: string }>(
      `/api/calendar/events/${eventId}/cancel-bot`,
      { method: "DELETE" },
    );
  }

  // ─── Slack Integration (user-based) ───────────────────────────

  async connectSlack() {
    return this.request<{ auth_url: string }>(
      "/integrations/slack/connect",
      { method: "POST" },
    );
  }

  async handleSlackCallback(code: string, state: string) {
    return this.request<SlackConnection>(
      "/integrations/slack/callback",
      {
        method: "POST",
        body: JSON.stringify({ code, state }),
      },
    );
  }

  async getSlackStatus() {
    return this.request<SlackConnection>("/integrations/slack/status");
  }

  async disconnectSlack() {
    return this.request<{ message: string }>(
      "/integrations/slack/disconnect",
      { method: "DELETE" },
    );
  }

  async updateSlackSettings(settings: Partial<SlackNotificationSettings>) {
    return this.request<SlackNotificationSettings>(
      "/integrations/slack/settings",
      {
        method: "PATCH",
        body: JSON.stringify(settings),
      },
    );
  }

  async getSlackChannels() {
    return this.request<{ channels: SlackChannel[] }>(
      "/integrations/slack/channels",
    );
  }

  // ─── Zoom Cloud Recording Integration ──────────────────────────

  async connectZoom() {
    return this.request<{ auth_url: string }>(
      "/integrations/zoom/connect",
      { method: "POST" },
    );
  }

  async handleZoomCallback(code: string, state: string) {
    return this.request<ZoomConnection>(
      "/integrations/zoom/callback",
      {
        method: "POST",
        body: JSON.stringify({ code, state }),
      },
    );
  }

  async getZoomStatus() {
    return this.request<ZoomConnection>("/integrations/zoom/status");
  }

  async disconnectZoom() {
    return this.request<{ message: string }>(
      "/integrations/zoom/disconnect",
      { method: "DELETE" },
    );
  }

  async updateZoomSettings(settings: { auto_import?: boolean; recording_preference?: string }) {
    return this.request<ZoomConnection>(
      "/integrations/zoom/settings",
      {
        method: "PATCH",
        body: JSON.stringify(settings),
      },
    );
  }

  // ─── Teams Cloud Recording ─────────────────────────────────────

  async connectTeamsCloud() {
    return this.request<{ auth_url: string }>(
      "/integrations/teams-cloud/connect",
      { method: "POST" },
    );
  }

  async handleTeamsCloudCallback(code: string, state: string) {
    return this.request<TeamsCloudConnection>(
      "/integrations/teams-cloud/callback",
      {
        method: "POST",
        body: JSON.stringify({ code, state }),
      },
    );
  }

  async getTeamsCloudStatus() {
    return this.request<TeamsCloudConnection>("/integrations/teams-cloud/status");
  }

  async disconnectTeamsCloud() {
    return this.request<{ message: string }>(
      "/integrations/teams-cloud/disconnect",
      { method: "DELETE" },
    );
  }

  async updateTeamsCloudSettings(settings: { auto_import?: boolean; recording_preference?: string }) {
    return this.request<TeamsCloudConnection>(
      "/integrations/teams-cloud/settings",
      {
        method: "PATCH",
        body: JSON.stringify(settings),
      },
    );
  }

  async getTeamsAdminConsentUrl() {
    return this.request<{ admin_consent_url: string; tenant_id: string }>(
      "/integrations/teams-cloud/admin-consent-url",
    );
  }

  async completeTeamsAdminConsent(tenant_id: string) {
    return this.request<{ message: string; subscription_id: string }>(
      "/integrations/teams-cloud/admin-consent-complete",
      {
        method: "POST",
        body: JSON.stringify({ tenant_id }),
      },
    );
  }

  // ─── Intelligence ──────────────────────────────────────────────

  async getIntelligenceOverview(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<IntelligenceOverview>(
      `/api/intelligence/overview?${params.toString()}`,
    );
  }

  async getPainPoints(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<PainPointAggregation[]>(
      `/api/intelligence/pain-points?${params.toString()}`,
    );
  }

  async getFeatureRequests(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<FeatureRequestAggregation[]>(
      `/api/intelligence/feature-requests?${params.toString()}`,
    );
  }

  async getCompetitors(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<CompetitorAggregation[]>(
      `/api/intelligence/competitors?${params.toString()}`,
    );
  }

  async getObjections(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<ObjectionAggregation[]>(
      `/api/intelligence/objections?${params.toString()}`,
    );
  }

  async getPMFSignals(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<PMFTrendPoint[]>(
      `/api/intelligence/pmf-signals?${params.toString()}`,
    );
  }

  async getDigests(teamId: string, page?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (page) params.set("page", String(page));
    return this.request<DigestListResponse>(
      `/api/intelligence/digests?${params.toString()}`,
    );
  }

  async getCurrentDigest(teamId: string) {
    return this.request<DigestResponse>(
      `/api/intelligence/digests/current?team_id=${teamId}`,
    );
  }

  async generateDigestNow(teamId: string) {
    return this.request<DigestResponse>(
      `/api/intelligence/digests/generate?team_id=${teamId}`,
      { method: "POST" },
    );
  }

  async getDigestSettings(teamId: string) {
    return this.request<DigestSettingsResponse>(
      `/api/intelligence/digest-settings?team_id=${teamId}`,
    );
  }

  async updateDigestSettings(teamId: string, data: DigestSettingsUpdate) {
    return this.request<DigestSettingsResponse>(
      `/api/intelligence/digest-settings?team_id=${teamId}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    );
  }

  async getDigestHistory(teamId: string, page?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (page) params.set("page", String(page));
    return this.request<JobRunListResponse>(
      `/api/intelligence/digest-history?${params.toString()}`,
    );
  }

  async getCallIntelligence(meetingId: string) {
    return this.request<CallIntelligenceResponse>(
      `/api/intelligence/call/${meetingId}`,
    );
  }

  async getMeetingSummary(meetingId: string) {
    return this.request<Record<string, unknown>>(
      `/api/meetings/${meetingId}/summary`,
    );
  }

  async getMeetingNextSteps(meetingId: string) {
    return this.request<{
      milestone: string;
      next_meeting_date: string;
      updated_at: string | null;
      updated_by: string | null;
    }>(`/api/meetings/${meetingId}/next-steps`);
  }

  async saveMeetingNextSteps(
    meetingId: string,
    body: { milestone?: string | null; next_meeting_date?: string | null },
  ) {
    return this.request<{
      milestone: string;
      next_meeting_date: string;
      updated_at: string | null;
      updated_by: string | null;
    }>(`/api/meetings/${meetingId}/next-steps`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  async getQuestions(teamId: string, days?: number, limit?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    if (limit) params.set("limit", String(limit));
    return this.request<QuestionAggregation[]>(
      `/api/intelligence/questions?${params.toString()}`,
    );
  }

  async getFaqGroups(teamId: string) {
    return this.request<{ groups: Record<string, unknown>[]; last_updated: string | null }>(
      `/api/intelligence/faq-groups?team_id=${teamId}`,
    );
  }

  async includeInIntelligence(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/intelligence/call/${meetingId}/include`,
      { method: "POST" },
    );
  }

  // ─── Sharing ────────────────────────────────────────────────────

  async createShareLink(meetingId: string) {
    return this.request<{ share_token: string; share_url: string; views: number }>(
      `/api/meetings/${meetingId}/share`,
      { method: "POST" },
    );
  }

  async getSharedCall(token: string) {
    // Public endpoint — no auth header needed
    const response = await fetch(`${API_URL}/api/shared/${encodeURIComponent(token)}`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new ApiError(response.status, error.detail || "Shared call not found");
    }
    return response.json();
  }

  async revokeShareLink(meetingId: string) {
    return this.request<{ status: string; meeting_id: string }>(
      `/api/meetings/${meetingId}/share`,
      { method: "DELETE" },
    );
  }

  async createIntelligenceShareLink(data: {
    team_id: string;
    bucket_type: string;
    title: string;
    items: Array<{ label: string; count: number; mentions: unknown[]; call_ids: string[]; extra?: Record<string, unknown> }>;
    total_calls: number;
  }): Promise<{ share_token: string; share_url: string }> {
    return this.request("/api/intelligence/share", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getSharedIntelligence(token: string) {
    // Public endpoint — no auth header needed
    const response = await fetch(`${API_URL}/api/shared/intelligence/${encodeURIComponent(token)}`);
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new ApiError(response.status, error.detail || "Shared intelligence not found");
    }
    return response.json();
  }

  // ─── Notification Preferences ──────────────────────────────────

  async getNotificationPreferences(teamId: string) {
    return this.request<Record<string, Record<string, boolean>>>(
      `/api/notifications/preferences?team_id=${teamId}`,
    );
  }

  async updateNotificationPreference(
    teamId: string,
    eventType: string,
    channel: string,
    enabled: boolean,
  ) {
    return this.request<{ status: string }>(
      "/api/notifications/preferences",
      {
        method: "PATCH",
        body: JSON.stringify({ team_id: teamId, event_type: eventType, channel, enabled }),
      },
    );
  }

  // ─── Notification Digest Settings (new unified endpoints) ──────

  async getNotificationDigestSettings(teamId: string, jobType: string = "intelligence_digest") {
    return this.request<DigestSettingsResponse>(
      `/api/notifications/digest-settings?team_id=${teamId}&job_type=${jobType}`,
    );
  }

  async updateNotificationDigestSettings(
    teamId: string,
    jobType: string,
    data: { frequency?: string; preferred_time?: string; preferred_day?: number; enabled?: boolean },
  ) {
    return this.request<DigestSettingsResponse>(
      "/api/notifications/digest-settings",
      {
        method: "PUT",
        body: JSON.stringify({ team_id: teamId, job_type: jobType, ...data }),
      },
    );
  }

  async generateNotificationDigest(teamId: string, jobType: string = "intelligence_digest") {
    return this.request<{ status: string; digest_id: string }>(
      `/api/notifications/digest-settings/generate?team_id=${teamId}&job_type=${jobType}`,
      { method: "POST" },
    );
  }

  // ─── Notification Log (bell icon) ─────────────────────────────

  async getNotificationLog(teamId: string, limit: number = 20, offset: number = 0) {
    return this.request<{
      items: Array<{
        id: string;
        event_type: string;
        title: string;
        body: string | null;
        link: string | null;
        channel: string | null;
        status: string;
        read: boolean;
        created_at: string | null;
      }>;
      unread_count: number;
      total: number;
    }>(`/api/notifications/log?team_id=${teamId}&limit=${limit}&offset=${offset}`);
  }

  async markNotificationsRead(ids?: string[], all?: boolean) {
    return this.request<{ status: string }>(
      "/api/notifications/log/mark-read",
      {
        method: "POST",
        body: JSON.stringify(all ? { all: true } : { ids }),
      },
    );
  }

  // ─── Slack Config (notification settings page) ─────────────────

  async getSlackNotificationConfig(teamId: string) {
    return this.request<{
      connected: boolean;
      workspace_name: string | null;
      channel_id: string | null;
      channel_name: string | null;
    }>(`/api/notifications/slack-config?team_id=${teamId}`);
  }

  async updateSlackNotificationConfig(teamId: string, channelId: string, channelName: string) {
    return this.request<{ status: string }>(
      "/api/notifications/slack-config",
      {
        method: "PUT",
        body: JSON.stringify({ team_id: teamId, channel_id: channelId, channel_name: channelName }),
      },
    );
  }

  async sendSlackTestMessage(teamId: string): Promise<{ status: string }> {
    return this.request(`/api/notifications/slack-config/test?team_id=${teamId}`, { method: "POST" });
  }

  // ─── CRM Integration ─────────────────────────────────────────────

  async getCrmConnections(): Promise<CrmConnection[]> {
    const data = await this.request<{ connections: CrmConnection[] }>("/api/crm/connections");
    return data.connections;
  }

  async getCrmAuthUrl(provider: string, region: string = "us"): Promise<{ auth_url: string }> {
    return this.request<{ auth_url: string }>(`/api/crm/${provider}/auth-url?region=${region}`);
  }

  async handleCrmCallback(provider: string, code: string, state: string, region: string = "us", accountsServer: string = ""): Promise<unknown> {
    let url = `/api/crm/${provider}/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}&region=${region}`;
    if (accountsServer) url += `&accounts_server=${encodeURIComponent(accountsServer)}`;
    return this.request(url, { method: "POST" });
  }

  async disconnectCrm(provider: string): Promise<void> {
    await this.request(`/api/crm/${provider}/disconnect`, { method: "DELETE" });
  }

  async getCrmStatus(provider: string): Promise<CrmConnection> {
    return this.request<CrmConnection>(`/api/crm/${provider}/status`);
  }

  async updateCrmSettings(provider: string, settings: { auto_push_enabled?: boolean }): Promise<void> {
    await this.request(`/api/crm/${provider}/settings`, {
      method: "PUT",
      body: JSON.stringify(settings),
    });
  }

  async getCrmPushResults(meetingId: string): Promise<CrmPushResult[]> {
    const data = await this.request<{ results: CrmPushResult[] }>(`/api/crm/push-results/${meetingId}`);
    return data.results;
  }

  async pushToCrm(meetingId: string, provider: string): Promise<CrmPushResult> {
    return this.request<CrmPushResult>(`/api/crm/push/${meetingId}/${provider}`, { method: "POST" });
  }

  async skipCrmPush(meetingId: string, provider: string): Promise<void> {
    await this.request(`/api/crm/skip/${meetingId}/${provider}`, { method: "POST" });
  }

  async editCrmPushData(meetingId: string, provider: string, data: Record<string, unknown>): Promise<void> {
    await this.request(`/api/crm/push/${meetingId}/${provider}/edit`, {
      method: "PUT",
      body: JSON.stringify({ data }),
    });
  }

  async getCrmProperties(provider: string, objectType: string = "contacts"): Promise<CrmProperty[]> {
    const data = await this.request<{ properties: CrmProperty[] }>(`/api/crm/${provider}/properties?object_type=${objectType}`);
    return data.properties;
  }

  async regenerateCrmData(): Promise<{ status: string; total_meetings: number; providers: string[] }> {
    return this.request(`/api/crm/regenerate`, { method: "POST" });
  }

  async regenerateCrmExtraction(meetingId: string): Promise<{ status: string; meeting_id: string; job_id: string }> {
    return this.request(`/api/crm/regenerate/${meetingId}`, { method: "POST" });
  }

  async getCrmRegenerateStatus(meetingId: string): Promise<{
    status: string;
    crm_extraction?: Record<string, unknown> | null;
    error?: string | null;
  }> {
    return this.request(`/api/crm/regenerate-status/${meetingId}`);
  }

  async getCrmStages(provider: string): Promise<CrmStage[]> {
    const data = await this.request<{ stages: CrmStage[] }>(`/api/crm/${provider}/stages`);
    return data.stages;
  }

  async getCrmFieldMapping(provider: string): Promise<Record<string, string>> {
    const data = await this.request<{ field_mapping: Record<string, string> }>(`/api/crm/${provider}/field-mapping`);
    return data.field_mapping;
  }

  async saveCrmFieldMapping(provider: string, mapping: Record<string, string>): Promise<void> {
    await this.request(`/api/crm/${provider}/field-mapping`, {
      method: "PUT",
      body: JSON.stringify({ mapping }),
    });
  }

  async getCrmStageMapping(provider: string): Promise<Record<string, string>> {
    const data = await this.request<{ stage_mapping: Record<string, string> }>(`/api/crm/${provider}/stage-mapping`);
    return data.stage_mapping;
  }

  async saveCrmStageMapping(provider: string, mapping: Record<string, string>): Promise<void> {
    await this.request(`/api/crm/${provider}/stage-mapping`, {
      method: "PUT",
      body: JSON.stringify({ mapping }),
    });
  }

  async startBatchPush(provider: string): Promise<{ status: string; total: number }> {
    return this.request(`/api/crm/${provider}/batch-push`, { method: "POST" });
  }

  async getBatchPushStatus(provider: string): Promise<{ total: number; completed: number; progress: number }> {
    return this.request(`/api/crm/${provider}/batch-push/status`);
  }

  // ─── Deal Intelligence ──────────────────────────────────────────

  async getDealStages(teamId: string, days?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    return this.request<DealStagesResponse>(
      `/api/intelligence/deal-stages?${params.toString()}`
    );
  }

  async getDealStageCalls(teamId: string, stageLabel: string, days?: number, offset?: number, limit?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    if (offset) params.set("offset", String(offset));
    if (limit) params.set("limit", String(limit));
    return this.request<DealStageCallsResponse>(
      `/api/intelligence/deal-stages/${encodeURIComponent(stageLabel)}/calls?${params.toString()}`
    );
  }

  async getDealTimeline(dealId: string) {
    return this.request<DealTimelineResponse>(
      `/api/intelligence/deals/${dealId}/timeline`
    );
  }

  // ─── PMF Signals (5-dimension) ─────────────────────────────────

  async getPmfWeeklySignals(teamId: string) {
    return this.request<PmfSignalsResponse>(
      `/api/intelligence/pmf/signals?team_id=${teamId}`
    );
  }

  async getPmfSignalCalls(teamId: string, signalType: string, value: string, days?: number, offset?: number, limit?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (days) params.set("days", String(days));
    if (offset) params.set("offset", String(offset));
    if (limit) params.set("limit", String(limit));
    return this.request<PmfSignalCallsResponse>(
      `/api/intelligence/pmf/signals/${signalType}/${encodeURIComponent(value)}/calls?${params.toString()}`
    );
  }

  async getPmfHistory(teamId: string, weeks?: number) {
    const params = new URLSearchParams({ team_id: teamId });
    if (weeks) params.set("weeks", String(weeks));
    return this.request<PmfHistoryResponse>(
      `/api/intelligence/pmf/history?${params.toString()}`
    );
  }

  // ─── CRM Pipeline ─────────────────────────────────────────────

  async getCrmPipelineStages(provider: string) {
    return this.request<CrmPipelineResponse>(
      `/api/crm/${provider}/pipeline-stages`
    );
  }

  // ─── Admin Dashboard (super admin only) ───────────────────────

  async getAdminOverview() {
    return this.request<AdminOverview>("/api/admin/dashboard/overview");
  }

  async getAdminRecentActivity(limit = 20) {
    return this.request<AdminActivity[]>(`/api/admin/dashboard/recent-activity?limit=${limit}`);
  }

  async getAdminCostSummary(period: "month" | "week" | "day" = "month") {
    return this.request<AdminCostSummary>(`/api/admin/dashboard/cost/summary?period=${period}`);
  }

  async getAdminCostTrends(granularity: "daily" | "weekly" | "monthly" = "daily") {
    return this.request<AdminCostTrendPoint[]>(`/api/admin/dashboard/cost/trends?granularity=${granularity}`);
  }

  async getAdminCostByTeam(period: "month" | "week" | "day" = "month", sort: "cost" | "meetings" | "name" = "cost") {
    return this.request<AdminTeamCostRow[]>(`/api/admin/dashboard/cost/by-team?period=${period}&sort=${sort}`);
  }

  async getAdminCostMeetings(page = 1, limit = 20, teamId?: string) {
    const params = new URLSearchParams({ page: String(page), limit: String(limit) });
    if (teamId) params.set("team_id", teamId);
    return this.request<{ items: AdminMeetingCostRow[]; total: number }>(
      `/api/admin/dashboard/cost/meetings?${params}`
    );
  }

  async getAdminCustomers(sort: "last_active" | "cost" | "meetings" | "name" = "last_active") {
    return this.request<AdminCustomerRow[]>(`/api/admin/dashboard/customers?sort=${sort}`);
  }

  async getAdminCustomerDetail(teamId: string) {
    return this.request<AdminCustomerDetail>(`/api/admin/dashboard/customers/${teamId}`);
  }

  async getAdminChurnRisk() {
    return this.request<AdminChurnRiskRow[]>("/api/admin/dashboard/churn-risk");
  }

  async getAdminProcessingStats() {
    return this.request<AdminProcessingStats>("/api/admin/dashboard/processing/stats");
  }

  async getAdminProcessingFailures(days = 7, limit = 20) {
    return this.request<AdminProcessingFailure[]>(
      `/api/admin/dashboard/processing/failures?days=${days}&limit=${limit}`
    );
  }
}

export const api = new ApiClient();
