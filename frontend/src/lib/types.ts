export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserProfile {
  id: string;
  email: string;
  email_verified: boolean;
  name: string;
  avatar_url: string | null;
  is_active: boolean;
  has_password: boolean;
  onboarding_completed: boolean;
  oauth_providers: string[];
  created_at: string;
  updated_at: string;
}

export interface User {
  id: string;
  email: string;
  name: string;
  team_id: string;
  team_role: "member" | "admin" | "owner";
  plan: string;
  onboarding_completed: boolean;
}

export interface Team {
  id: string;
  name: string;
  slug: string;
  created_by: string;
  crm_type: string | null;
  team_size_range: string | null;
  intelligence_priority: string | null;
  created_at: string;
  member_count: number;
  domain: string | null;
}

export interface TeamMember {
  id: string;
  user_id: string;
  email: string | null;
  name: string | null;
  role: string;
  invite_status: string;
  joined_at: string | null;
  calls_this_week: number;
  requested_at: string | null;
}

export interface Meeting {
  id: string;
  team_id: string;
  created_by: string;
  created_by_name: string | null;
  title: string | null;
  // Nullable post-migration 040: web_recorder and upload meetings have no
  // real URL (legacy "upload://local" placeholder was backfilled to NULL).
  meeting_url: string | null;
  status: string;
  intelligence_status?: string;
  source: string | null;
  // Populated when a BotWorker container handled the recording, regardless
  // of source (extension/manual/calendar/zoom_cloud entry points all set this).
  bot_job_id?: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  confidence_score: number | null;
  crm_push_status: string | null;
  has_recording: boolean;
  scheduled_start_at: string | null;
  created_at: string;
  summary_excerpt: string | null;
  primary_participant_name: string | null;
  primary_participant_company: string | null;
}

export interface MeetingDetail extends Meeting {
  has_transcript: boolean;
  has_analysis: boolean;
  has_memoir: boolean;
  memoir_deal_id: string | null;
  external_share_approved?: boolean;
  external_participants_count?: number;
  intelligence_status?: string;
}

export interface MeetingListResponse {
  meetings: Meeting[];
  total: number;
  page: number;
  per_page: number;
  counts: Record<string, number> | null;
  /** Optional — present on v3 responses so the UI can hide the `All`
   *  scope chip without trusting the auth-store role. */
  current_user_role?: "owner" | "admin" | "member" | null;
}

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string;
  original_start?: number;
  original_end?: number;
  detected_language?: string;
}

export interface SpeakerMapEntry {
  name: string;
  profile_id?: string;
}

export interface TranscriptResponse {
  meeting_id: string;
  segments: TranscriptSegment[];
  speaker_count: number;
  word_count: number;
  language: string;
  speaker_map?: Record<string, SpeakerMapEntry>;
  is_edited: boolean;
}

export interface MeetingParticipantInfo {
  id: string;
  diarization_label?: string;
  display_name?: string;
  speaker_profile_id?: string;
  email?: string;
  company?: string;
  title?: string;
  crm_links?: { hubspot?: string; zoho?: string };
  mapping_source: string;
  mapping_confidence: number;
  talk_time_seconds: number;
  segment_count: number;
  word_count: number;
}

export interface MeetingSpeakersResponse {
  meeting_id: string;
  participants: MeetingParticipantInfo[];
}

export interface SpeakerProfile {
  id: string;
  display_name: string;
  email?: string;
  company?: string;
  title?: string;
  crm_links?: { hubspot?: string; zoho?: string };
  meeting_count: number;
}

export interface CrmField {
  name: string;
  value: string;
  confidence: "definitive" | "indicative" | "inferred";
  speaker: string;
  quote: string;
}

export interface NextSteps {
  next_meeting: string | null;
  next_milestone: string | null;
  suggested_deal_stage: string | null;
  decisions?: Array<{title: string; detail: string}>;
  open_questions?: Array<{text: string}>;
}

export interface CallParticipant {
  name: string;
  title: string | null;
  company: string | null;
  role: string;
}

export interface CallMetadata {
  date: string | null;
  duration_minutes: number | null;
  participants: CallParticipant[];
  call_type: string | null;
}

export interface MemoirResponse {
  meeting_id: string;
  narrative_summary: string | null;
  discovery_fields: CrmField[] | null;
  action_items: ActionItem[] | null;
  signals: SignalData | null;
  next_steps: NextSteps | null;
  call_metadata: CallMetadata | null;
  disclaimer: string | null;
  overall_confidence_score: number | null;
  is_edited: boolean;
}

export interface ActionItem {
  text: string;
  type?: "rep" | "prospect";
  completed?: boolean;
  owner?: string;
  owner_email?: string;
  urgency?: string;
  context?: string;
  blocking?: string;
}

export interface SignalData {
  buying: string[];
  risk: string[];
  discussion_threads?: Array<{
    title: string;
    priority: string;
    summary: string;
    attributions?: Array<{speaker: string; action: string}>;
  }>;
}

export interface CoachingResponse {
  meeting_id: string;
  coaching_metrics: Record<string, unknown>;
}

export interface IntelligenceResponse {
  meeting_id: string;
  intelligence: Record<string, unknown>;
}

export interface SearchResult {
  meeting_id: string;
  title: string | null;
  prospect_name: string | null;
  prospect_company: string | null;
  snippet: string;
  score: number;
  date: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export interface EnhancedSearchResult {
  meeting_id: string;
  title: string | null;
  prospect_name: string | null;
  prospect_company: string | null;
  snippet: string;
  score: number;
  date: string | null;
  result_type: "call" | "transcript";
  timestamp_seconds: number | null;
}

export interface EnhancedSearchResponse {
  call_results: EnhancedSearchResult[];
  transcript_results: EnhancedSearchResult[];
  call_total: number;
  transcript_total: number;
  query: string;
}

export interface DashboardStats {
  total_meetings: number;
  total_minutes: number;
  total_speakers: number;
  this_week_meetings: number;
  last_week_meetings: number;
}

// Mock call type used in prototype UI (richer than API response)
export interface CallListItem {
  id: string;
  prospect_name: string;
  prospect_company: string;
  call_type: string;
  duration: number;
  time: string;
  date: string;
  top_takeaway: string;
  outcome: "positive" | "neutral" | "negative";
  crm_status: "pushed" | "pending" | "review_needed" | "skipped";
  confidence: number;
  rep_name: string;
}

export interface CallDetail {
  id: string;
  prospect_name: string;
  prospect_title: string;
  prospect_company: string;
  call_type: string;
  call_date: string;
  duration: number;
  time: string;
  confidence: number;
  crm_status: string;
  summary: string;
  crm_fields: CrmField[];
  action_items_rep: string[];
  action_items_prospect: string[];
  buying_signals: string[];
  risk_signals: string[];
  suggested_stage: string;
}

export interface WeeklyDigest {
  week: string;
  total_calls: number;
  total_reps: number;
  total_hours: number;
  headline: string;
  opening: string;
  scoreboard: {
    calls: number;
    strong_fits: number;
    competitor_mentions: number;
    pricing_positive: number;
    blockers: number;
    feature_requests: number;
  };
  feature_requests: Array<{
    name: string;
    count: number;
    quote: string;
    speaker: string;
    company: string;
  }>;
  praises: Array<{
    name: string;
    count: number;
    quote: string;
  }>;
  competitors: Array<{
    name: string;
    mentions: number;
    positive: string;
    negative: string;
  }>;
  objections: Array<{
    text: string;
    count: number;
    severity: "blocker" | "concern" | "friction";
    quote: string;
  }>;
  pain_quotes: Array<{
    quote: string;
    speaker: string;
    title: string;
    company: string;
    size: string;
  }>;
  desire_quotes: Array<{
    quote: string;
    speaker: string;
    title: string;
    company: string;
    size: string;
  }>;
  icp: {
    strong: number;
    partial: number;
    poor: number;
    poor_note: string;
  };
  team: {
    strengths: string[];
    coaching: string[];
  };
  takeaways: string[];
  wow: {
    pricing: string;
    competitors: string;
    new: string;
  };
}

export interface TeamListMember {
  name: string;
  email: string;
  role: string;
  calls: number;
  ext: boolean;
}

export interface PlanInfo {
  plan: string;
  limits: Record<string, number>;
  trial_ends_at: string | null;
}

export interface AdminTeamLookup {
  user_id: string;
  user_name: string;
  user_email: string;
  team_id: string;
  team_name: string;
  plan: string;
  plan_limits: Record<string, number>;
  trial_ends_at: string | null;
}

export interface CalendarConnection {
  id: string;
  user_id: string;
  provider: string;
  provider_email: string;
  auto_record: boolean;
  /** Set true by integration_service when an OAuth refresh fails with
   *  invalid_grant. Frontend should treat as "not connected" and prompt
   *  re-authentication via the OAuth flow. */
  token_invalid: boolean;
  last_synced_at: string | null;
  created_at: string;
}

export interface CalendarEvent {
  id: string;
  provider_event_id: string;
  title: string;
  start_time: string;
  end_time: string;
  meeting_url: string | null;
  linked_meeting_id: string | null;
  provider: string;
  /** True when a Memoir bot is scheduled to auto-join this event. Driven
   *  by POST /api/calendar/events/{id}/schedule-bot and DELETE .../cancel-bot. */
  bot_scheduled: boolean;
}

/** Attendee entry inside a CalendarEventV3.attendees JSONB. Optional fields
 *  because Google and Outlook sync don't always populate everything. */
export interface CalendarAttendee {
  email?: string | null;
  name?: string | null;
  response?: string | null;
  organizer?: boolean | null;
}

/** v3 calendar event — extends the legacy shape with description,
 *  cancelled_at, resolved_user_id, attendees, source, bot_invited,
 *  linked_meeting_status. See plan/meetings-events-v3-list.md §3.3. */
export interface CalendarEventV3 extends CalendarEvent {
  description?: string | null;
  cancelled_at?: string | null;
  resolved_user_id?: string | null;
  attendees?: CalendarAttendee[] | null;
  /** "calendar" — synced from user's OAuth connection; "bot_calendar" —
   *  resolved to this team via organizer-email lookup on the bot's calendar. */
  source: string;
  bot_invited: boolean;
  /** meetings.status of the linked recording (LEFT JOINed), or null when
   *  no linked meeting exists. Used by eventStatus() to distinguish
   *  recorded vs recording_failed past events. */
  linked_meeting_status?: string | null;
}

/** Envelope response for GET /api/calendar/events/v3. */
export interface CalendarEventListV3 {
  events: CalendarEventV3[];
  /** Lower-cased usually; the frontend should still LOWER both sides
   *  when matching against attendees emails. */
  current_user_email: string;
  current_user_role: "owner" | "admin" | "member";
}

export interface CalendarSyncResponse {
  synced_count: number;
  events: CalendarEvent[];
}

export interface SlackNotificationSettings {
  channel_id: string | null;
  channel_name: string | null;
  notify_on_meeting_complete: boolean;
  notify_on_crm_pushed: boolean;
  notify_on_blocker_detected: boolean;
  notify_on_team_invite: boolean;
}

export interface SlackConnection {
  id: string;
  user_id: string;
  workspace_id: string;
  workspace_name: string | null;
  scopes: string[] | null;
  notification_settings: SlackNotificationSettings | null;
  created_at: string;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

export interface ZoomConnection {
  id: string;
  user_id: string;
  zoom_email: string;
  auto_import: boolean;
  recording_preference: "bot" | "zoom_cloud" | "auto";
  cloud_recording_enabled: boolean | null;
  created_at: string;
}

export interface TeamsCloudConnection {
  id: string;
  user_id: string;
  provider_email: string;
  auto_import: boolean;
  recording_preference: "bot" | "teams_cloud" | "auto";
  tenant_id: string | null;
  subscription_id: string | null;
  subscription_expiry: string | null;
  admin_consent_granted: boolean;
  created_at: string;
}

export interface TranscriptionVersion {
  version: number;
  created_at: string;
  word_count: number;
  speaker_count: number;
  // Mirrors backend meetings.source enum; gets forwarded through transcription
  // job params at tab_audio.py:_finalize_meeting. Pre-migration-040 jobs may
  // still carry shorter values ("bot"/"zoom_cloud"/"upload") — string fallback
  // is acceptable for unknown values.
  recording_source?:
    | "bot"
    | "manual"
    | "calendar"
    | "extension"
    | "extension_tab_audio"
    | "web_recorder"
    | "upload"
    | "link_upload"
    | "zoom_cloud"
    | "teams_cloud";
}

// ---------------------------------------------------------------------------
// Call Intelligence — aggregation types from /api/intelligence/*
// ---------------------------------------------------------------------------

export interface MentionDetail {
  meeting_id: string | null;
  exact_quote: string | null;
  speaker_name: string | null;
  prospect_company: string | null;
  timestamp_in_call: string | null;
  call_date: string | null;
}

export interface PainPointAggregation {
  pain: string;
  mention_count: number;
  avg_severity?: number;
  quotes: string[];
  mentions: MentionDetail[];
  call_ids: string[];
}

export interface FeatureRequestAggregation {
  feature: string;
  request_count: number;
  urgency_breakdown?: Record<string, number>;
  mentions: MentionDetail[];
  call_ids: string[];
}

export interface CompetitorAggregation {
  competitor: string;
  mention_count: number;
  sentiment_breakdown?: Record<string, number>;
  likes: string[];
  dislikes: string[];
  mentions: MentionDetail[];
  call_ids: string[];
}

export interface ObjectionAggregation {
  objection: string;
  count: number;
  category?: string;
  resolution_rate?: number;
  mentions: MentionDetail[];
  call_ids: string[];
}

export interface QuestionAggregation {
  question: string;
  ask_count: number;
  categories: string[];
  mentions: MentionDetail[];
  call_ids: string[];
  has_unanswered: boolean;
}

export interface PMFTrendPoint {
  period_start: string;
  period_end: string;
  avg_pull_score?: number;
  avg_pain_fit?: number;
  avg_icp_fit?: number;
  call_count: number;
}

export interface DigestResponse {
  id: string;
  team_id: string;
  period_start: string;
  period_end: string;
  narrative?: string;
  metrics?: Record<string, unknown>;
  total_calls: number;
  cost_usd?: number;
  created_at?: string;
}

export interface DigestListResponse {
  digests: DigestResponse[];
  total: number;
}

export interface DigestSettingsResponse {
  id: string;
  frequency: string;
  preferred_time?: string;
  preferred_day?: number;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
}

export interface DigestSettingsUpdate {
  frequency?: string;
  preferred_time?: string;
  preferred_day?: number;
  enabled?: boolean;
}

export interface JobRunResponse {
  id: string;
  job_type: string;
  started_at?: string;
  completed_at?: string;
  status: string;
  result_summary?: string;
  error_message?: string;
  records_processed: number;
  cost_usd?: number;
}

export interface JobRunListResponse {
  runs: JobRunResponse[];
  total: number;
}

export interface IntelligenceOverview {
  call_count: number;
  date_range_start?: string;
  date_range_end?: string;
  pain_points: PainPointAggregation[];
  feature_requests: FeatureRequestAggregation[];
  competitors: CompetitorAggregation[];
  objections: ObjectionAggregation[];
  questions: QuestionAggregation[];
  pmf_trend: PMFTrendPoint[];
  current_digest?: DigestResponse;
}

export interface CallIntelligenceResponse {
  id: string;
  meeting_id: string;
  call_type: string;
  classification_confidence?: string;
  crm_extraction?: Record<string, unknown>;
  pain_points?: Record<string, unknown>[];
  feature_requests?: Record<string, unknown>[];
  competitors_mentioned?: Record<string, unknown>[];
  objections?: Record<string, unknown>[];
  buying_signals?: Record<string, unknown>[];
  red_flags?: Record<string, unknown>[];
  pmf_signals?: Record<string, unknown>;
  pull_score?: number;
  icp_fit_score?: number;
  pain_fit_score?: number;
  overall_pmf_signal?: string;
  summary?: Record<string, unknown>;
  call_health?: string;
  questions?: Record<string, unknown>[];
  deal_momentum?: string;
  validated_stage?: string;
  crm_stage_id?: string;
  crm_stage_label?: string;
  has_committed_next_step?: boolean;
  next_step_type?: string;
  total_tokens_used?: number;
  total_cost_usd?: number;
  processing_status: string;
  created_at?: string;
}

// ---------------------------------------------------------------------------
// CRM Integration types
// ---------------------------------------------------------------------------

export interface CrmConnection {
  provider: string;
  account_id: string | null;
  account_name: string | null;
  is_active: boolean;
  auto_push_enabled: boolean;
  connection_error: string | null;
  last_sync_at: string | null;
  connected_at: string | null;
  field_mapping: Record<string, string>;
  stage_mapping: Record<string, string>;
}

export interface CrmPushResult {
  id: string;
  provider: string;
  status: "pending" | "processing" | "completed" | "failed" | "skipped";
  crm_contact_id: string | null;
  crm_deal_id: string | null;
  crm_record_id: string | null;
  crm_record_url: string | null;
  pushed_data: Record<string, unknown>;
  edited_data: Record<string, unknown> | null;
  extraction_snapshot: Record<string, unknown>;
  error_message: string | null;
  retry_count: number;
  pushed_at: string | null;
  created_at: string | null;
}

export interface CrmProperty {
  name: string;
  label: string;
  type: string;
  group?: string;
  read_only?: boolean;
}

export interface CrmStage {
  id: string;
  label: string;
  pipeline: string;
  display_order: number;
}

// ---------------------------------------------------------------------------
// Deal Intelligence types
// ---------------------------------------------------------------------------

export interface DealStageCall {
  meeting_id: string;
  title: string | null;
  prospect_name: string | null;
  prospect_company: string | null;
  date: string | null;
  duration_seconds: number | null;
  pull_score: number | null;
  deal_momentum: string | null;
  validated_stage: string | null;
  crm_stage_label: string | null;
  call_health: string | null;
  has_committed_next_step: boolean | null;
}

export interface DealStageSummary {
  stage_label: string;
  stage_id: string | null;
  call_count: number;
  display_order: number | null;
}

export interface DealStagesResponse {
  has_crm: boolean;
  stages: DealStageSummary[];
  unassigned_count: number;
}

export interface DealStageCallsResponse {
  calls: DealStageCall[];
  total: number;
  stage_label: string;
}

export interface DealTimelineEntry {
  meeting_id: string;
  title: string | null;
  date: string | null;
  call_number: number | null;
  validated_stage: string | null;
  crm_stage_label: string | null;
  call_health: string | null;
  deal_momentum: string | null;
  pull_score: number | null;
  one_line_summary: string | null;
}

export interface DealTimelineResponse {
  deal_id: string;
  prospect_company: string | null;
  prospect_name: string | null;
  status: string | null;
  current_stage: string | null;
  total_calls: number;
  entries: DealTimelineEntry[];
}

// ---------------------------------------------------------------------------
// PMF Weekly Digest types
// ---------------------------------------------------------------------------

export interface PmfSignalItem {
  value: string;
  calls_mentioned: number;
  total_calls: number;
  stars: number;
  trend: string; // "up" | "down" | "same" | "new"
  prev_count: number;
  mentions: number;
}

export interface PmfSignalsResponse {
  week_start: string | null;
  total_calls: number;
  feature_demand: PmfSignalItem[];
  feature_demand_stars: number;
  objections: PmfSignalItem[];
  objections_stars: number;
  competitors: PmfSignalItem[];
  competitors_stars: number;
  buy_signal_rate: number | null;
  buy_signal_stars: number;
  buy_signal_prev_rate: number | null;
  pain_clarity_rate: number | null;
  pain_clarity_stars: number;
  pain_clarity_top_pain: string | null;
  pain_clarity_prev_rate: number | null;
  narrative: string | null;
}

export interface PmfSignalCallsResponse {
  calls: DealStageCall[];
  total: number;
  signal_type: string;
  signal_value: string | null;
}

export interface PmfHistoryEntry {
  week_start: string;
  total_calls: number;
  feature_demand_stars: number;
  objections_stars: number;
  competitors_stars: number;
  buy_signal_stars: number;
  pain_clarity_stars: number;
}

export interface PmfHistoryResponse {
  weeks: PmfHistoryEntry[];
}

// ---------------------------------------------------------------------------
// CRM Pipeline types
// ---------------------------------------------------------------------------

export interface CrmPipelineStage {
  id: string;
  label: string;
  pipeline_id: string | null;
  pipeline_name: string | null;
  display_order: number;
}

export interface CrmPipelineResponse {
  provider: string;
  stages: CrmPipelineStage[];
}


// ---------------------------------------------------------------------------
// Admin Dashboard types
// ---------------------------------------------------------------------------

export interface AdminOverview {
  total_teams: number;
  total_users: number;
  total_meetings_all_time: number;
  meetings_today: number;
  meetings_this_week: number;
  total_cost_this_month: number;
  total_audio_hours_this_month: number;
  failed_jobs_today: number;
  success_rate_today: number;
}

export interface AdminActivity {
  meeting_id: string;
  team_name: string;
  title: string;
  created_at: string | null;
  status: string;
  source: string | null;
  duration_seconds: number | null;
  cost_usd: number;
}

export interface AdminCostSummary {
  total_cost: number;
  total_meetings: number;
  avg_cost_per_meeting: number;
  total_audio_hours: number;
  asr_cost: number;
  intelligence_cost: number;
  cost_by_source: Record<string, number>;
  projected_monthly: number;
}

export interface AdminCostTrendPoint {
  period: string;
  meetings: number;
  total_cost: number;
  asr_cost: number;
  intelligence_cost: number;
}

export interface AdminTeamCostRow {
  team_id: string;
  team_name: string;
  meetings: number;
  total_cost: number;
  avg_cost_per_meeting: number;
  total_audio_hours: number;
}

export interface AdminMeetingCostRow {
  meeting_id: string | null;
  team_id: string | null;
  team_name: string;
  title: string;
  date: string | null;
  source: string;
  audio_duration_seconds: number;
  asr_cost: number;
  intelligence_cost: number;
  total_cost: number;
}

export interface AdminCustomerRow {
  team_id: string;
  team_name: string;
  member_count: number;
  meetings_this_week: number;
  last_meeting_at: string | null;
  days_inactive: number | null;
  total_cost_this_month: number;
  total_meetings?: number;
  churn_risk: "low" | "medium" | "high";
  integrations?: string[];
}

export interface AdminCustomerDetail {
  team_id: string;
  team_name: string;
  recording_sources: Record<string, number>;
  meetings_trend: Array<{ week: string; count: number }>;
  cost_trend: Array<{ week: string; cost: number }>;
  members?: Array<{ name: string; email: string; meetings_count: number; last_login: string | null }>;
  integrations_status?: Record<string, boolean>;
}

export interface AdminChurnRiskRow {
  team_id: string;
  team_name: string;
  last_meeting_at: string | null;
  days_inactive: number;
  member_count: number;
}

export interface AdminProcessingStats {
  active_jobs: number;
  waiting_jobs: number;
  completed_today: number;
  failed_today: number;
  success_rate_7d: number;
  avg_processing_seconds: number;
  queue_depth?: number;
}

export interface AdminProcessingFailure {
  job_id: string;
  meeting_id: string | null;
  team_name: string;
  meeting_title: string;
  error_message: string | null;
  failed_at: string | null;
  retry_count: number;
}
