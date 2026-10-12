"use client";

/**
 * Events v3 list — calendar-strip UI for upcoming + past-not-recorded calendar events.
 *
 * Plan: plan/meetings-events-v3-list.md §6.2.
 *
 * Key behaviors (locked decisions §11):
 *   - Default scope: "hosted" for all roles
 *   - Default include_past: TRUE (show past not-recorded events)
 *   - Row click:
 *       recorded (has linked_meeting_id + meetings.status in {completed, processing, ...})
 *           → open /meetings/{id} in a NEW TAB
 *       upcoming → unclickable as a whole (in-row bot toggle is interactive)
 *       past unrecorded (not_recorded / bot_didnt_join) → unclickable, read-only
 *   - Hosted/Guest pill derived from attendees JSONB matching current_user_email
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ScheduleMeetingModal } from "@/components/capture/schedule-meeting-modal";

import { api } from "@/lib/api";
import { notifyMeetingsChanged } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";
import type { CalendarEventV3 } from "@/lib/types";
import {
  CalendarShell,
  DayHeader,
  dateStripUtils,
  type DateMode,
  type ScopeValue,
} from "@/components/calendar-shell";

// ── Derived event status ──────────────────────────────────────────────

type EventStatus =
  | "upcoming"
  | "recorded"
  | "recording_failed"
  | "not_recorded"
  | "bot_didnt_join";

function eventStatus(ev: CalendarEventV3, now: Date): EventStatus {
  const ended = new Date(ev.end_time) < now;

  if (ev.linked_meeting_id) {
    const s = ev.linked_meeting_status;
    if (s === "failed") return "recording_failed";
    return "recorded";
  }
  if (!ended) return "upcoming";
  if (ev.bot_scheduled) return "bot_didnt_join";
  return "not_recorded";
}

function isUserOrganizer(ev: CalendarEventV3, userEmailLower: string): boolean {
  if (!ev.attendees) return false;
  return ev.attendees.some(
    (a) => (a.email || "").toLowerCase() === userEmailLower && a.organizer === true,
  );
}

function formatPlatform(meetingUrl: string | null): string {
  if (!meetingUrl) return "—";
  const u = meetingUrl.toLowerCase();
  if (u.includes("meet.google.com")) return "Meet";
  if (u.includes("teams.microsoft.com") || u.includes("teams.live.com")) return "Teams";
  if (u.includes("zoom")) return "Zoom";
  return "Link";
}

function formatEventTimeRange(start: string, end: string): string {
  const s = new Date(start);
  const e = new Date(end);
  const fmt = (d: Date) =>
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${fmt(s)} – ${fmt(e)}`;
}

function formatDuration(start: string, end: string): string {
  const mins = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 60000);
  return `${mins} min`;
}

/** Strip HTML tags + common entities from a calendar description and
 *  collapse whitespace, so the preview line renders readable text
 *  instead of `<html><head>...` markup. Returns "" if nothing remains.
 */
function stripHtmlPreview(raw: string | null | undefined): string {
  if (!raw) return "";
  return raw
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/_{3,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ── Status badge ──────────────────────────────────────────────────────

function StatusBadge({ status }: { status: EventStatus }) {
  const styles: Record<EventStatus, string> = {
    upcoming: "bg-blue-50 text-blue-700 border-blue-200",
    recorded: "bg-emerald-50 text-emerald-700 border-emerald-200",
    recording_failed: "bg-rose-50 text-rose-700 border-rose-200",
    not_recorded: "bg-memoir-bg text-memoir-text-muted border-memoir-border",
    bot_didnt_join: "bg-rose-50 text-rose-700 border-rose-200",
  };
  const labels: Record<EventStatus, string> = {
    upcoming: "⏰ Upcoming",
    recorded: "● Recorded",
    recording_failed: "✕ Recording failed",
    not_recorded: "⊙ Not recorded",
    bot_didnt_join: "✕ Bot didn't join",
  };
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}

function HostPill({ isHost }: { isHost: boolean }) {
  return isHost ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-memoir-indigo/10 text-memoir-indigo border border-memoir-indigo/30">
      Hosted
    </span>
  ) : (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-memoir-bg text-memoir-text-muted border border-memoir-border">
      Guest
    </span>
  );
}

// ── Left-border accent by status ──────────────────────────────────────

function accentClass(status: EventStatus): string {
  switch (status) {
    case "upcoming":
      return "border-l-4 border-l-blue-400";
    case "not_recorded":
      return "border-l-4 border-l-slate-300";
    case "bot_didnt_join":
    case "recording_failed":
      return "border-l-4 border-l-rose-400";
    case "recorded":
      return "border-l-4 border-l-emerald-400";
  }
}

// ── Main component ────────────────────────────────────────────────────

export function EventsV3() {
  const activeTeamId = useAuthStore((s) => s.activeTeamId) || "";

  // ── State ──
  // ── URL-derived state ─────────────────────────────────────────────
  // Filter + date state lives in the URL so that navigating to a meeting
  // detail and clicking back restores the user's exact view (?date,
  // ?mode, ?scope, ?past). Setters use router.replace to avoid creating
  // history entries per filter change — only meeting-detail navigation
  // creates a back-able entry.
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

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
  // Default is checked (locked §11.4); URL encodes only the off case.
  const showPastNotRecorded = searchParams.get("past") !== "0";

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
  const setShowPastNotRecorded = useCallback(
    (v: boolean) => replaceParams({ past: v ? null : "0" }),
    [replaceParams],
  );

  const [events, setEvents] = useState<CalendarEventV3[]>([]);
  const [activity, setActivity] = useState<Record<string, number>>({});
  const [currentUserEmail, setCurrentUserEmail] = useState<string>("");
  const [serverRole, setServerRole] = useState<"owner" | "admin" | "member" | null>(null);
  const [loading, setLoading] = useState(false);
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set());
  // event id → error message; rendered inline on the row after a
  // failed toggle (review fix L3). Auto-clears on next successful toggle.
  const [toggleErrors, setToggleErrors] = useState<Record<string, string>>({});

  // userRole comes from the server's envelope (authoritative). Until the
  // first request lands we default to "member" — this hides the `All`
  // chip during the brief pre-load flicker rather than showing it from
  // the (potentially stale) auth-store role and snapping it away when
  // the server response arrives. Server still 403s any unauthorized
  // scope=all click regardless.
  const userRole = serverRole ?? "member";

  const apiScope: "hosted" | "participant" | "all" = scope;

  // ── Load events for the selected day ──
  // Send full ISO datetime range (local day boundaries as UTC) so the
  // filter aligns with what the user sees on the strip.
  const loadEvents = useCallback(async () => {
    if (!activeTeamId) return;
    setLoading(true);
    try {
      const res = await api.getCalendarEventsV3({
        teamId: activeTeamId,
        scope: apiScope,
        includePast: showPastNotRecorded,
        startDate: dateStripUtils.localStartOfDayIso(selectedDate),
        endDate: dateStripUtils.localEndOfDayIso(selectedDate),
      });
      setEvents(res.events);
      setCurrentUserEmail((res.current_user_email || "").toLowerCase());
      setServerRole(res.current_user_role);
    } catch (e) {
      console.error("[EventsV3] load failed:", e);
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [activeTeamId, apiScope, showPastNotRecorded, selectedDate]);

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
      const res = await api.getCalendarEventsWeekActivityV3({
        teamId: activeTeamId,
        scope: apiScope,
        rangeStart: start.toISOString(),
        rangeEnd: end.toISOString(),
        tzOffsetMinutes: dateStripUtils.tzOffsetMinutes(),
      });
      setActivity(res);
    } catch (e) {
      console.error("[EventsV3] week-activity failed:", e);
      setActivity({});
    }
  }, [activeTeamId, apiScope, selectedDate, dateMode]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    loadActivity();
  }, [loadActivity]);

  // ── Bot toggle ─────────────────────────────────────────────────────
  const toggleBot = useCallback(
    async (ev: CalendarEventV3) => {
      const id = ev.id;
      const next = !ev.bot_scheduled;
      setTogglingIds((s) => new Set(s).add(id));
      // Clear any prior error for this row before retrying.
      setToggleErrors((errs) => {
        if (!errs[id]) return errs;
        const out = { ...errs };
        delete out[id];
        return out;
      });
      // Optimistic update
      setEvents((evs) => evs.map((e) => (e.id === id ? { ...e, bot_scheduled: next } : e)));
      try {
        if (next) {
          await api.scheduleBot(id);
          notifyMeetingsChanged();
        } else {
          await api.cancelBot(id);
        }
      } catch (e) {
        console.error("[EventsV3] toggle failed:", e);
        // Revert + surface the error inline so the user sees the failure
        // instead of just a silent revert.
        setEvents((evs) => evs.map((e) => (e.id === id ? { ...e, bot_scheduled: !next } : e)));
        const msg = e instanceof Error ? e.message : "Bot toggle failed";
        setToggleErrors((errs) => ({ ...errs, [id]: msg }));
      } finally {
        setTogglingIds((s) => {
          const out = new Set(s);
          out.delete(id);
          return out;
        });
      }
    },
    [],
  );

  // ── Derived counts for DayHeader ───────────────────────────────────
  // 30s ticker: bumps a counter every 30 seconds so the eventsWithStatus
  // useMemo re-runs and status flips from "upcoming" → "not_recorded" /
  // "bot_didnt_join" within at most 30s of end_time passing, even if the
  // user doesn't otherwise touch the page. Without the tick, the memo
  // only refreshes when events change (which only happens on date/scope
  // change or explicit reload).
  const [statusTick, setStatusTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setStatusTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const eventsWithStatus = useMemo(() => {
    const now = new Date();
    return events.map((e) => ({ event: e, status: eventStatus(e, now) }));
    // statusTick is intentionally in the dep array — bumping it forces
    // the memo to re-evaluate so freshly-ended events flip status.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, statusTick]);
  const breakdown = useMemo(() => {
    let past = 0;
    let upcoming = 0;
    eventsWithStatus.forEach(({ status }) => {
      if (status === "upcoming") upcoming += 1;
      else past += 1;
    });
    return { past, upcoming };
  }, [eventsWithStatus]);

  // ── Render ─────────────────────────────────────────────────────────
  const tabs = useMemo(
    () => [
      { id: "meetings", label: "Meetings", href: "/meetings" },
      { id: "events", label: "Events", href: "/events" },
    ],
    [],
  );

  // "+ Schedule" CTA — hidden until bot calendar provisioning ships.
  // The ScheduleMeetingModal is still wired (kept for the email-invite
  // path) but no visible entry point routes to it for now.
  const [scheduleOpen, setScheduleOpen] = useState(false);
  // const rightCta = (
  //   <button
  //     type="button"
  //     onClick={() => setScheduleOpen(true)}
  //     className="px-3 py-1.5 rounded-md text-sm font-semibold bg-memoir-indigo text-white hover:bg-memoir-indigo/90"
  //   >
  //     + Schedule
  //   </button>
  // );
  const rightCta = null;

  const detailFilters = (
    <label className="inline-flex items-center gap-1.5 text-xs font-medium text-memoir-text-muted">
      <input
        type="checkbox"
        checked={showPastNotRecorded}
        onChange={(e) => setShowPastNotRecorded(e.target.checked)}
        className="rounded border-memoir-border text-memoir-indigo focus:ring-memoir-indigo"
      />
      Show past not-recorded
    </label>
  );

  return (
    <CalendarShell
      tabs={tabs}
      activeTabId="events"
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
      <DayHeader
        date={selectedDate}
        count={events.length}
        unit="events"
        breakdown={breakdown}
      />

      {loading ? (
        <div className="text-sm text-memoir-text-muted py-8 text-center">Loading…</div>
      ) : events.length === 0 ? (
        <div className="text-sm text-memoir-text-muted py-12 text-center">
          No events on this day.
        </div>
      ) : (
        <div className="space-y-2">
          {eventsWithStatus.map(({ event, status }) => (
            <EventCard
              key={event.id}
              event={event}
              status={status}
              userEmailLower={currentUserEmail}
              isToggling={togglingIds.has(event.id)}
              toggleError={toggleErrors[event.id]}
              onToggleBot={() => toggleBot(event)}
            />
          ))}
        </div>
      )}
      <ScheduleMeetingModal open={scheduleOpen} onOpenChange={setScheduleOpen} />
    </CalendarShell>
  );
}

// ── Card ──────────────────────────────────────────────────────────────

function EventCard({
  event,
  status,
  userEmailLower,
  isToggling,
  toggleError,
  onToggleBot,
}: {
  event: CalendarEventV3;
  status: EventStatus;
  userEmailLower: string;
  isToggling: boolean;
  toggleError?: string;
  onToggleBot: () => void;
}) {
  const isHost = isUserOrganizer(event, userEmailLower);
  const attendeeCount = event.attendees?.length ?? 0;

  // Row click behavior (locked §11.5):
  //   recorded → open /meetings/{id} in new tab
  //   anything else (including recording_failed) → unclickable, since
  //   the failed meeting has no content to display.
  const linkHref =
    status === "recorded" && event.linked_meeting_id
      ? `/meetings/${event.linked_meeting_id}`
      : null;

  const cardClasses =
    "p-4 bg-memoir-surface border border-memoir-border rounded-card transition-all " +
    accentClass(status) +
    (linkHref ? " cursor-pointer hover:border-memoir-indigo/30 hover:shadow-card" : "");

  const cardContent = (
    <>
      {/* Row 1: Title + with X + Hosted/Guest pill */}
      <div className="flex justify-between items-start mb-1.5 gap-3">
        <div className="flex-1 min-w-0">
          <span className="font-semibold text-memoir-text text-sm">
            {event.title || "Untitled"}
          </span>
          {attendeeCount > 0 && (
            <span className="text-xs text-memoir-text-muted ml-1.5">
              with {attendeeCount} attendee{attendeeCount === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <HostPill isHost={isHost} />
      </div>
      {/* Description excerpt — calendar descriptions can be HTML
          (Outlook/Teams ship rich text). Strip tags + collapse
          whitespace so the card renders readable preview text. */}
      {(() => {
        const text = stripHtmlPreview(event.description);
        if (!text) return null;
        return (
          <p className="text-xs text-memoir-text-muted line-clamp-2 mb-1.5">
            {text}
          </p>
        );
      })()}
      {/* Row 2: Status · Duration · Time · Platform · Bot toggle */}
      <div className="flex items-center gap-2 text-xs text-memoir-text-muted flex-wrap">
        <StatusBadge status={status} />
        <span>·</span>
        <span>{formatDuration(event.start_time, event.end_time)}</span>
        <span>·</span>
        <span>{formatEventTimeRange(event.start_time, event.end_time)}</span>
        <span>·</span>
        <span>{formatPlatform(event.meeting_url)}</span>
        {status === "upcoming" && event.meeting_url && (
          <>
            <span>·</span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                onToggleBot();
              }}
              disabled={isToggling}
              className={
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border transition-colors " +
                (event.bot_scheduled
                  ? "bg-memoir-indigo/10 text-memoir-indigo border-memoir-indigo/30 hover:bg-memoir-indigo/20"
                  : "bg-white text-memoir-text-muted border-memoir-border hover:border-memoir-indigo/30") +
                (isToggling ? " opacity-50 cursor-wait" : "")
              }
              aria-label={event.bot_scheduled ? "Cancel bot for this meeting" : "Schedule bot for this meeting"}
            >
              {event.bot_scheduled ? "✓ Bot on" : "○ Bot off"}
            </button>
          </>
        )}
      </div>
      {toggleError && (
        <div className="mt-1.5 text-[10px] text-rose-700">
          ⚠ {toggleError}
        </div>
      )}
    </>
  );

  if (linkHref) {
    return (
      <a
        href={linkHref}
        target="_blank"
        rel="noopener noreferrer"
        className={cardClasses + " block no-underline"}
      >
        {cardContent}
      </a>
    );
  }
  return <div className={cardClasses}>{cardContent}</div>;
}
