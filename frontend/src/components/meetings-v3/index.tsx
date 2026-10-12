"use client";

/**
 * Meetings v3 list — calendar-strip UI.
 *
 * Plan: plan/meetings-events-v3-list.md §6.1.
 *
 * Differences from v2:
 *   - DateStrip with selected-day list (no infinite scroll, no grouping by date)
 *   - Scope chips: All (admin/owner only) / Hosted by me / I am a participant
 *   - No confidence column, no confidence filter
 *   - Source + Status filters retain with explicit "All" option
 *   - Existing card markup minus the ConfidenceMeter accessory + confidence
 *     left-border accent
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import type { Meeting } from "@/lib/types";
import { MEETINGS_CHANGED_EVENT } from "@/lib/utils";
import {
  CalendarShell,
  DayHeader,
  dateStripUtils,
  type DateMode,
  type ScopeValue,
} from "@/components/calendar-shell";
import { MeetingStatusBadge } from "@/components/ui/meeting-status-badge";

// Status badge lives in @/components/ui/meeting-status-badge — shared
// with meetings-v2 + the legacy /meetings page so all three surfaces
// render the same palette and label map. Other badges remain inline.

// Bot involvement (bot_job_id IS NOT NULL) wins over source: a bot meeting
// could have source=extension/manual/calendar/zoom_cloud depending on entry
// point, but the user just wants to see "Extension Bot Recording". For
// non-bot rows the source decides the label.
const SourceBadge = ({
  source,
  meetingUrl,
  botJobId,
}: {
  source: string | null;
  meetingUrl?: string | null;
  botJobId?: string | null;
}) => {
  // Source wins over bot_job_id. Tab recordings ALSO set bot_job_id to a
  // "tab-audio-<session_id>" string (as a job reference), so checking
  // bot_job_id first would mis-label tab recordings. Real bot recordings
  // have source IN ('extension', 'manual', 'calendar', 'zoom_cloud') AND
  // a non-tab-audio bot_job_id.
  if (source === "extension_tab_audio") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-50 text-emerald-600 border border-emerald-200">
        Extension Tab Recording
      </span>
    );
  }
  if (source === "web_recorder") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-purple-50 text-purple-600 border border-purple-200">
        Web Recorder
      </span>
    );
  }
  // Non-bot bot_job_id prefixes: "tab-audio-", "upload-", "link-upload-".
  // Only treat as real bot when bot_job_id has NONE of those prefixes
  // (real-bot rows carry a raw UUID).
  const isRealBot =
    !!botJobId &&
    !botJobId.startsWith("tab-audio-") &&
    !botJobId.startsWith("upload-") &&
    !botJobId.startsWith("link-upload-");
  if (isRealBot) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-600 border border-indigo-200">
        Extension Bot Recording
      </span>
    );
  }
  if (source === "calendar") {
    const isTeams = meetingUrl?.includes("teams.microsoft.com") || meetingUrl?.includes("teams.live.com");
    const isZoom = meetingUrl?.includes("zoom.us");
    const label = isTeams ? "Outlook" : isZoom ? "Zoom Cal" : "Google Cal";
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-200">
        {label}
      </span>
    );
  }
  if (source === "upload") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-violet-50 text-violet-600 border border-violet-200">
        Upload
      </span>
    );
  }
  if (source === "zoom_cloud") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-200">
        Zoom Cloud
      </span>
    );
  }
  // Fallback: source values not matched above + null. meetingUrl ignored.
  void meetingUrl;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-memoir-bg text-memoir-text-muted border border-memoir-border">
      Bot
    </span>
  );
};

// ── Helpers ───────────────────────────────────────────────────────────

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const mins = Math.round(seconds / 60);
  return `${mins} min`;
}

function formatMeetingTime(meeting: Meeting): string {
  const ref = meeting.started_at || meeting.scheduled_start_at || meeting.created_at;
  if (!ref) return "";
  const d = new Date(ref);
  const startStr = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (meeting.started_at && meeting.ended_at) {
    const endStr = new Date(meeting.ended_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    return `${startStr} – ${endStr}`;
  }
  return startStr;
}

// ── Filter dropdowns ──────────────────────────────────────────────────

// Values must match the `source` strings stored in meetings.source.
// "bot" + "manual" both render as the default Bot badge; we surface
// "bot" here as the user-friendly handle. The backend's source filter
// matches the literal column value, so manual-source meetings remain
// reachable via the All option only (consistent with v2).
const SOURCE_OPTIONS = [
  { value: "all", label: "All sources" },
  { value: "bot", label: "Bot" },
  { value: "calendar", label: "Calendar" },
  { value: "web_recorder", label: "Web Recorder" },
  { value: "extension_tab_audio", label: "Extension Tab Recording" },
  { value: "extension", label: "Extension Bot Recording" },
  { value: "zoom_cloud", label: "Zoom Cloud" },
  { value: "upload", label: "Upload" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  // "recorded" is a pseudo-status — UI label only; the API call sets
  // has_recording=true (matches the v2 behavior at meetings/page.tsx:538-541).
  { value: "recorded", label: "Recorded" },
  { value: "completed", label: "Completed" },
  { value: "recording", label: "Recording" },
  { value: "processing", label: "Processing" },
  { value: "failed", label: "Failed" },
];

function Dropdown({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="text-xs font-medium px-2 py-1 rounded border border-memoir-border bg-white text-memoir-text hover:border-memoir-indigo focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

// ── Main component ────────────────────────────────────────────────────

export function MeetingsV3() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeTeamId = useAuthStore((s) => s.activeTeamId) || "";

  // userRole comes from the server's list response (authoritative). Until
  // the first request lands we default to "member" so the `All` chip is
  // hidden during the brief pre-load window rather than showing it from
  // the (potentially stale) auth-store and snapping it away when the
  // server response arrives. Server still 403s any unauthorized scope=all
  // click regardless. See review fix M2.
  const [serverRole, setServerRole] = useState<
    "owner" | "admin" | "member" | null
  >(null);
  const userRole = serverRole ?? "member";

  // ── URL-derived state ─────────────────────────────────────────────
  // All filter + date state lives in the URL (?date, ?mode, ?scope,
  // ?source, ?status) so that navigating to a meeting detail and
  // clicking back restores the user's exact filter view. Setters use
  // router.replace to avoid creating one history entry per filter
  // change — only the meeting-detail navigation creates a back-able
  // entry, which then restores the URL on return.
  const dateMode: DateMode = (searchParams.get("mode") as DateMode) || "week";
  const selectedDate = useMemo<Date>(() => {
    const raw = searchParams.get("date");
    if (raw && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      const [y, m, d] = raw.split("-").map(Number);
      const out = new Date(y, m - 1, d);
      out.setHours(0, 0, 0, 0);
      return out;
    }
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return today;
  }, [searchParams]);
  const scope: ScopeValue =
    (searchParams.get("scope") as ScopeValue) || "hosted";
  const source = searchParams.get("source") || "all";
  const status = searchParams.get("status") || "all";

  const replaceParams = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(updates)) {
        if (v == null || v === "") next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [searchParams, router, pathname],
  );

  // Defaults are omitted from the URL to keep it short + readable.
  const setDateMode = useCallback(
    (m: DateMode) => replaceParams({ mode: m === "week" ? null : m }),
    [replaceParams],
  );
  const setSelectedDate = useCallback(
    (d: Date) => replaceParams({ date: dateStripUtils.isoDate(d) }),
    [replaceParams],
  );
  const setScope = useCallback(
    (s: ScopeValue) => replaceParams({ scope: s === "hosted" ? null : s }),
    [replaceParams],
  );
  const setSource = useCallback(
    (s: string) => replaceParams({ source: s === "all" ? null : s }),
    [replaceParams],
  );
  const setStatus = useCallback(
    (s: string) => replaceParams({ status: s === "all" ? null : s }),
    [replaceParams],
  );

  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [activity, setActivity] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);

  // "participant" → map to backend's "shared" enum. Other values pass
  // through directly.
  const apiScope: "hosted" | "shared" | "all" =
    scope === "participant" ? "shared" : scope;

  // ── Load meetings for the selected day ──
  // Send full ISO datetime range (local day boundaries as UTC) instead of
  // bare YYYY-MM-DD — fixes the off-by-one day for non-UTC users.
  const loadMeetings = useCallback(async () => {
    if (!activeTeamId) return;
    setLoading(true);
    try {
      const params: Parameters<typeof api.getMeetings>[0] = {
        team_id: activeTeamId,
        scope: apiScope,
        start_date: dateStripUtils.localStartOfDayIso(selectedDate),
        end_date: dateStripUtils.localEndOfDayIso(selectedDate),
        // Match the week-activity GROUP BY basis so the day list and
        // strip dots agree for synced calendar events whose
        // started_at != created_at (review fix #1).
        date_basis: "activity",
        per_page: 100,
      };
      if (source !== "all") params.source = source;
      if (status !== "all") {
        if (status === "recorded") {
          // Pseudo-status — UI label only; backend filters on has_recording.
          params.has_recording = true;
        } else {
          params.status = status;
        }
      }
      const res = await api.getMeetings(params);
      setMeetings(res.meetings ?? []);
      if (res.current_user_role) {
        setServerRole(res.current_user_role);
      }
    } catch (e) {
      console.error("[MeetingsV3] load failed:", e);
      setMeetings([]);
    } finally {
      setLoading(false);
    }
  }, [activeTeamId, apiScope, selectedDate, source, status]);

  // ── Load week/month activity dots ──
  const loadActivity = useCallback(async () => {
    if (!activeTeamId) return;
    const start =
      dateMode === "week"
        ? dateStripUtils.startOfWeek(selectedDate)
        : dateStripUtils.startOfMonth(selectedDate);
    const end =
      dateMode === "week"
        ? dateStripUtils.endOfWeek(selectedDate)
        : dateStripUtils.endOfMonth(selectedDate);
    try {
      const res = await api.getMeetingsWeekActivityV3({
        teamId: activeTeamId,
        scope: apiScope,
        rangeStart: start.toISOString(),
        rangeEnd: end.toISOString(),
        tzOffsetMinutes: dateStripUtils.tzOffsetMinutes(),
      });
      setActivity(res);
    } catch (e) {
      console.error("[MeetingsV3] week-activity failed:", e);
      setActivity({});
    }
  }, [activeTeamId, apiScope, selectedDate, dateMode]);

  useEffect(() => {
    loadMeetings();
  }, [loadMeetings]);

  useEffect(() => {
    loadActivity();
  }, [loadActivity]);

  // Refresh when a meeting is created elsewhere (upload modal, web-recorder
  // Start, bot scheduling) so the list picks up the new row immediately
  // instead of waiting for the user to leave and come back to /meetings.
  useEffect(() => {
    const onChanged = () => {
      loadMeetings();
      loadActivity();
    };
    window.addEventListener(MEETINGS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(MEETINGS_CHANGED_EVENT, onChanged);
  }, [loadMeetings, loadActivity]);

  const tabs = useMemo(
    () => [
      { id: "meetings", label: "Meetings", href: "/meetings" },
      { id: "events", label: "Events", href: "/events" },
    ],
    [],
  );

  const rightCta = (
    <Link
      href="/meetings/v2"
      className="px-3 py-1.5 rounded-md text-xs font-medium text-memoir-text-muted hover:text-memoir-text hover:bg-memoir-bg"
      title="Switch to the previous Meetings list"
    >
      v2 fallback ↗
    </Link>
  );

  const detailFilters = (
    <>
      <Dropdown value={source} options={SOURCE_OPTIONS} onChange={setSource} ariaLabel="Source filter" />
      <Dropdown value={status} options={STATUS_OPTIONS} onChange={setStatus} ariaLabel="Status filter" />
    </>
  );

  return (
    <CalendarShell
      tabs={tabs}
      activeTabId="meetings"
      rightCta={rightCta}
      dateMode={dateMode}
      onDateModeChange={setDateMode}
      selectedDate={selectedDate}
      onSelectedDateChange={setSelectedDate}
      activity={activity}
      scope={scope}
      onScopeChange={setScope}
      userRole={userRole}
      detailFilters={detailFilters}
    >
      <DayHeader date={selectedDate} count={meetings.length} unit="meetings" />

      {loading ? (
        <div className="text-sm text-memoir-text-muted py-8 text-center">Loading…</div>
      ) : meetings.length === 0 ? (
        <div className="text-sm text-memoir-text-muted py-12 text-center">
          No meetings on this day.
        </div>
      ) : (
        <div className="space-y-2">
          {meetings.map((m) => (
            <MeetingCard
              key={m.id}
              meeting={m}
              onClick={() => {
                // Stash the current URL (with filters + date) so the
                // detail page's "Back to Meetings" button can restore it.
                if (typeof window !== "undefined") {
                  sessionStorage.setItem(
                    "memoir_meetings_back",
                    window.location.pathname + window.location.search,
                  );
                }
                router.push(`/meetings/${m.id}`);
              }}
            />
          ))}
        </div>
      )}
    </CalendarShell>
  );
}

function MeetingCard({ meeting, onClick }: { meeting: Meeting; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      className="p-4 bg-memoir-surface border border-memoir-border rounded-card cursor-pointer hover:border-memoir-indigo/30 hover:shadow-card transition-all"
    >
      {/* Row 1: Title + Participant inline */}
      <div className="flex justify-between items-start mb-1.5">
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-memoir-text text-sm">
            {meeting.title || "Untitled Call"}
          </span>
          {meeting.primary_participant_name && (
            <span className="text-xs text-memoir-text-muted ml-1.5">
              with {meeting.primary_participant_name}
              {meeting.primary_participant_company && (
                <span> ({meeting.primary_participant_company})</span>
              )}
            </span>
          )}
        </div>
        {/* NO ConfidenceMeter — dropped in v3 per locked decision */}
      </div>
      {/* Summary excerpt */}
      {meeting.summary_excerpt && (
        <p className="text-xs text-memoir-text-muted line-clamp-2 mb-1.5">
          {meeting.summary_excerpt}
        </p>
      )}
      {/* Row 2: Status + Duration + Time + Creator + Source */}
      <div className="flex items-center gap-2 text-xs text-memoir-text-muted flex-wrap">
        <MeetingStatusBadge
          status={meeting.status}
          intelligenceStatus={meeting.intelligence_status}
        />
        {meeting.duration_seconds && (
          <>
            <span>·</span>
            <span>{formatDuration(meeting.duration_seconds)}</span>
          </>
        )}
        <span>·</span>
        <span>{formatMeetingTime(meeting)}</span>
        {meeting.created_by_name && (
          <>
            <span>·</span>
            <span className="text-memoir-text-secondary">{meeting.created_by_name}</span>
          </>
        )}
        <span>·</span>
        <SourceBadge source={meeting.source} meetingUrl={meeting.meeting_url} botJobId={meeting.bot_job_id} />
      </div>
    </div>
  );
}
