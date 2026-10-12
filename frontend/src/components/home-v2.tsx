"use client";

/**
 * Home v2 — revamp UI (flag-gated by NEXT_PUBLIC_HOME_V2=true).
 *
 * Sections:
 *  - Greeting block (uses logged-in user name)
 *  - Onboarding card (reuses useSetupSteps hook from v1)
 *  - Intelligence Spotlight (3 cards) — SEED DATA until /api/dashboard/spotlight ships
 *  - Weekly Pulse (4 stats) — partial real data (DashboardStats), rest SEED
 *  - Recent / Upcoming tabs — recent uses real API, upcoming uses calendar API
 *  - Right Calendar panel (collapsible 290px)
 *
 * Backend gaps (per plan/ui_revamp.md):
 *  - GET /api/dashboard/spotlight (Intelligence Spotlight)
 *  - GET /api/dashboard/pulse (Weekly Pulse extras)
 * Sections that depend on those endpoints render seed data with a "Sample" tag
 * so reviewers can see the layout without misleading numbers.
 */

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import {
  Sparkles,
  Calendar,
  Flame,
  AlertTriangle,
  Target,
  ChevronRight,
  ChevronLeft,
  Plus,
  Globe,
  Mic,
  Video,
  CheckCircle2,
} from "lucide-react";

import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import { notifyMeetingsChanged } from "@/lib/utils";
import type { DashboardStats, Meeting, CalendarEvent } from "@/lib/types";
import { useSetupSteps } from "@/hooks/use-setup-steps";
import { PlatformBadge, type Platform, platformFromSourceOrUrl } from "@/components/ui/platform-badge";
import { Tag } from "@/components/ui/tag";
import { TogglePill } from "@/components/ui/toggle-pill";
import { SentimentDot, type Sentiment } from "@/components/ui/sentiment-dot";
import { ScheduleMeetingModal } from "@/components/capture/schedule-meeting-modal";

function greetingPrefix(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

// Legacy helper kept for URL-only callers (calendar events have no source).
// Use platformFromSourceOrUrl(m.source, m.meeting_url) where source is available.
function platformFromUrl(url: string | null): Platform {
  return platformFromSourceOrUrl(null, url);
}

function timeOfDay(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function relativeDateLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (day.getTime() === today.getTime()) return "Today";
  if (day.getTime() === tomorrow.getTime()) return "Tomorrow";
  if (day.getTime() === yesterday.getTime()) return "Yesterday";
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function isoDateLocal(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Seed data — replace when /api/dashboard/spotlight lands.
const SPOTLIGHT_SEED = [
  {
    icon: <Flame className="h-[18px] w-[18px]" style={{ stroke: "#E8683A" }} fill="#F5A623" />,
    title: "Competitor Mentions",
    detail: "3 prospects mentioned Fireflies.ai this week",
    badge: "Trending",
    tone: "orange" as const,
  },
  {
    icon: <AlertTriangle className="h-[18px] w-[18px]" style={{ stroke: "#F5A623" }} />,
    title: "Recurring Objection",
    detail: '"Pricing too high" raised in 2 discovery calls',
    badge: "Action needed",
    tone: "amber" as const,
  },
  {
    icon: <Target className="h-[18px] w-[18px]" style={{ stroke: "#00B87C" }} />,
    title: "Buying Signal",
    detail: "Acme Corp asked about enterprise plan & SSO",
    badge: "Hot lead",
    tone: "green" as const,
  },
];

export function HomeV2() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentMeetings, setRecentMeetings] = useState<Meeting[]>([]);
  const [upcoming, setUpcoming] = useState<CalendarEvent[]>([]);
  const [calendarConnected, setCalendarConnected] = useState(false);
  const [calendarTokenInvalid, setCalendarTokenInvalid] = useState(false);
  // Global "bot auto-joins every meeting" preference on the calendar connection.
  // When true, the per-event toggle reflects ON for any future event with a
  // meeting_url, even though calendar_events.bot_scheduled is still false
  // server-side (the auto-scheduler flips it ~5min before start).
  const [calendarAutoRecord, setCalendarAutoRecord] = useState(false);
  const [crmConnected, setCrmConnected] = useState(false);
  const [togglingEventIds, setTogglingEventIds] = useState<Set<string>>(new Set());
  const [scheduleOpen, setScheduleOpen] = useState(false);

  // "Will the bot join?" for an event — combines explicit per-event dispatch
  // (`bot_scheduled`) with the connection's global `auto_record` flag.
  const willBotJoin = (event: CalendarEvent): boolean => {
    if (event.bot_scheduled) return true;
    if (calendarAutoRecord && event.meeting_url) return true;
    return false;
  };

  // Optimistic toggle: flip bot_scheduled locally, call API; on failure revert.
  // When `auto_record` is enabled on the calendar connection, per-event
  // opt-out isn't supported by the current backend — show an alert pointing
  // the user to the global setting instead of failing silently.
  const toggleBotForEvent = async (event: CalendarEvent) => {
    if (togglingEventIds.has(event.id)) return;
    const currentlyOn = willBotJoin(event);
    const next = !currentlyOn;
    if (!next && calendarAutoRecord && !event.bot_scheduled) {
      alert(
        "This meeting is covered by your global auto-record setting. To skip it, turn off \"Automatically record meetings\" in Settings → Integrations.",
      );
      return;
    }
    setTogglingEventIds((prev) => new Set(prev).add(event.id));
    setUpcoming((prev) => prev.map((e) => (e.id === event.id ? { ...e, bot_scheduled: next } : e)));
    try {
      if (next) {
        await api.scheduleBot(event.id, event.title);
        notifyMeetingsChanged();
      } else {
        await api.cancelBot(event.id);
      }
    } catch {
      setUpcoming((prev) => prev.map((e) => (e.id === event.id ? { ...e, bot_scheduled: !next } : e)));
    } finally {
      setTogglingEventIds((prev) => {
        const copy = new Set(prev);
        copy.delete(event.id);
        return copy;
      });
    }
  };
  const [slackConnected, setSlackConnected] = useState(false);
  const [extensionInstalled, setExtensionInstalled] = useState(false);
  // Right Calendar preview is collapsed by default; the floating
  // "N Upcoming" tab on the page edge expands it.
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"recent" | "upcoming">("recent");
  const [showOnboarding, setShowOnboarding] = useState(true);
  const fetchRef = useRef(false);

  useEffect(() => {
    const check = () => document.documentElement.hasAttribute("data-memoir-ext");
    setExtensionInstalled(check());
    const t = setTimeout(() => setExtensionInstalled(check()), 1000);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (fetchRef.current || !activeTeamId) return;
    fetchRef.current = true;
    api.getDashboardStats(activeTeamId).then(setStats).catch(() => {});
    api
      .getMeetings({ team_id: activeTeamId, per_page: 5 })
      .then((r) => setRecentMeetings(r.meetings ?? []))
      .catch(() => {});
    api
      .getCalendarEvents(activeTeamId)
      .then((events) => setUpcoming(events.slice(0, 5)))
      .catch(() => {});
    // Probe status first to know which provider exists, then fire-and-forget a
    // sync so stale tokens flip token_invalid=true server-side. After the sync
    // settles (success OR 401), re-probe status to refresh the UI banner.
    const probeCalendar = async () => {
      let conn = null;
      try {
        conn = await Promise.any([
          api.getCalendarStatus("google"),
          api.getCalendarStatus("outlook"),
        ]);
      } catch {
        // No connection at all — nothing to probe.
        return;
      }
      if (!conn) return;
      setCalendarConnected(!conn.token_invalid);
      setCalendarTokenInvalid(conn.token_invalid);
      setCalendarAutoRecord(conn.auto_record);
      // If the token is already known-invalid, no need to re-attempt.
      if (conn.token_invalid) return;
      // Fire-and-forget sync. On invalid_grant the server marks
      // token_invalid=true; we re-fetch status either way.
      api.syncCalendar(conn.provider).catch(() => {}).finally(() => {
        api.getCalendarStatus(conn.provider).then((fresh) => {
          if (fresh) {
            setCalendarConnected(!fresh.token_invalid);
            setCalendarTokenInvalid(fresh.token_invalid);
            setCalendarAutoRecord(fresh.auto_record);
          }
        }).catch(() => {});
      });
    };
    probeCalendar();
    api
      .getCrmConnections()
      .then((conns) => {
        if (conns.length > 0) setCrmConnected(true);
      })
      .catch(() => {});
    api.getSlackStatus().then(() => setSlackConnected(true)).catch(() => {});
  }, [activeTeamId]);

  const steps = useSetupSteps(calendarConnected, crmConnected, extensionInstalled, slackConnected);
  const completed = steps.filter((s) => s.done).length;
  const allDone = completed === steps.length;
  const firstName = user?.name?.split(" ")[0] ?? "there";

  // "Meetings this week" = past meetings (this_week_meetings from stats) +
  // upcoming calendar events that fall within the current week (Sun-Sat),
  // skipping any that already have a linked Memoir meeting to avoid double-count.
  const weekEndMs = (() => {
    const d = new Date();
    const daysUntilSun = 7 - d.getDay(); // 0 = Sunday today, 7 = next Sun
    d.setDate(d.getDate() + (daysUntilSun === 7 ? 0 : daysUntilSun));
    d.setHours(23, 59, 59, 999);
    return d.getTime();
  })();
  const upcomingThisWeek = upcoming.filter((ev) => {
    if (ev.linked_meeting_id) return false;
    const t = new Date(ev.start_time).getTime();
    return t >= Date.now() && t <= weekEndMs;
  }).length;
  const meetingsThisWeek = (stats?.this_week_meetings ?? 0) + upcomingThisWeek;

  const pulseCards = [
    {
      label: "Meetings this week",
      value: meetingsThisWeek,
      sub:
        upcomingThisWeek > 0
          ? `${upcomingThisWeek} upcoming`
          : stats && stats.last_week_meetings >= 0
            ? `${stats.this_week_meetings - stats.last_week_meetings >= 0 ? "+" : ""}${
                stats.this_week_meetings - stats.last_week_meetings
              } vs last week`
            : "—",
      subTone: "green" as const,
      icon: <Video className="h-[18px] w-[18px] text-brand" />,
    },
    {
      label: "Avg talk ratio",
      value: "—",
      sub: "Pending data",
      subTone: "neutral" as const,
      icon: <Mic className="h-[18px] w-[18px] text-brand" />,
    },
    {
      label: "Action items",
      value: "—",
      sub: "Pending data",
      subTone: "neutral" as const,
      icon: <CheckCircle2 className="h-[18px] w-[18px] text-brand" />,
    },
    {
      label: "Topics covered",
      value: "—",
      sub: "Pending data",
      subTone: "neutral" as const,
      icon: <Sparkles className="h-[18px] w-[18px] text-brand" />,
    },
  ];

  return (
    <div className="flex h-full font-sans text-ink" style={{ fontSize: 14 }}>
      <main className="flex-1 overflow-auto bg-surface-soft px-6 py-8 lg:px-9">
        <div className="mb-7">
          <h1 className="text-[28px] font-extrabold tracking-tight text-ink">
            {greetingPrefix()}, {firstName}
          </h1>
          <p className="mt-1.5 text-sm font-medium text-ink-secondary">
            You have{" "}
            <span className="font-bold text-brand">{meetingsThisWeek} meetings</span> this week
            {!allDone && (
              <>
                {" "}· <span className="font-bold text-brand-orange">{steps.length - completed} setup steps</span> remaining
              </>
            )}
          </p>
        </div>

        {showOnboarding && !allDone && (
          <div className="relative mb-5 rounded-2xl border border-surface-border bg-white px-6 py-5 shadow-sm">
            <button
              onClick={() => setShowOnboarding(false)}
              className="absolute right-4 top-3.5 text-xl font-light leading-none text-ink-secondary hover:text-ink"
              aria-label="Dismiss onboarding"
            >
              ×
            </button>
            <div className="mb-3.5 flex items-center gap-2">
              <span className="text-lg">🚀</span>
              <span className="text-[15px] font-extrabold text-ink">Getting started</span>
              <span className="ml-auto text-xs font-semibold text-ink-secondary">
                {completed} of {steps.length}
              </span>
            </div>
            <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-surface-soft">
              <div
                className="h-full rounded-full bg-gradient-to-r from-brand to-[#8B8BF5]"
                style={{ width: `${(completed / steps.length) * 100}%` }}
              />
            </div>
            <div className="grid grid-cols-1 gap-x-8 gap-y-2.5 md:grid-cols-2">
              {steps.map((step) => (
                <div key={step.label} className="flex items-center gap-2 text-[13px] font-medium">
                  {step.done ? (
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-green-light">
                      <CheckCircle2 className="h-3 w-3 text-brand-green" />
                    </div>
                  ) : (
                    <div className="h-5 w-5 shrink-0 rounded-full border-2 border-surface-border" />
                  )}
                  <span className={step.done ? "text-ink-secondary" : "text-ink"}>{step.label}</span>
                  {!step.done && (
                    <a
                      href={step.href ?? "#"}
                      className="text-xs font-bold text-brand hover:underline"
                    >
                      Connect →
                    </a>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mb-5 rounded-2xl border border-surface-border bg-white px-7 py-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="h-[18px] w-[18px] text-brand" />
              <span className="text-[15px] font-extrabold text-ink">Intelligence Spotlight</span>
              <Tag tone="neutral" label="Sample" />
            </div>
            <button
              onClick={() => router.push("/intelligence")}
              className="flex items-center gap-1 text-xs font-bold text-brand hover:underline"
            >
              View all insights <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3.5 md:grid-cols-3">
            {SPOTLIGHT_SEED.map((item) => (
              <div
                key={item.title}
                className="cursor-pointer rounded-xl border border-surface-border bg-surface-soft px-5 py-4 transition-shadow hover:shadow-sm"
              >
                <div className="mb-2.5 flex items-center gap-2">
                  {item.icon}
                  <span className="text-[13px] font-bold text-ink">{item.title}</span>
                </div>
                <p className="mb-3 text-[12.5px] font-normal leading-relaxed text-ink-secondary">{item.detail}</p>
                <Tag label={item.badge} tone={item.tone} />
              </div>
            ))}
          </div>
        </div>

        <div className="mb-5 grid grid-cols-2 gap-3.5 md:grid-cols-4">
          {pulseCards.map((card) => (
            <div key={card.label} className="rounded-xl border border-surface-border bg-white px-5 py-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-xs font-semibold text-ink-secondary">{card.label}</span>
                {card.icon}
              </div>
              <div className="mb-1 text-[30px] font-extrabold leading-none tracking-tight text-ink">{card.value}</div>
              <div
                className={`text-xs font-semibold ${
                  card.subTone === "green" ? "text-brand-green" : "text-ink-secondary"
                }`}
              >
                {card.sub}
              </div>
            </div>
          ))}
        </div>

        <div className="overflow-hidden rounded-2xl border border-surface-border bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-surface-border px-6 py-3">
            <div className="flex gap-0">
              {(["recent", "upcoming"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`border-b-[2.5px] px-4 py-2 text-[13.5px] capitalize transition-colors ${
                    activeTab === tab
                      ? "border-brand font-extrabold text-brand"
                      : "border-transparent font-medium text-ink-secondary hover:text-ink"
                  }`}
                >
                  {tab === "recent" ? "Recent Meetings" : "Upcoming"}
                </button>
              ))}
            </div>
            <button
              onClick={() => {
                if (activeTab === "upcoming") {
                  // Target the closest upcoming event's date so the calendar
                  // strip lands on a day where the user immediately sees
                  // their next meeting (matches what they were just looking at).
                  const closest = upcoming
                    .map((e) => new Date(e.start_time))
                    .filter((d) => !isNaN(d.getTime()))
                    .sort((a, b) => a.getTime() - b.getTime())[0];
                  const dateParam = closest ? `?date=${isoDateLocal(closest)}` : "";
                  router.push(`/events${dateParam}`);
                } else {
                  router.push("/meetings");
                }
              }}
              className="flex items-center gap-1 text-xs font-bold text-brand hover:underline"
            >
              View all <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {activeTab === "recent" && (
            <div>
              {recentMeetings.length === 0 && (
                <div className="px-6 py-8 text-center text-sm text-ink-secondary">
                  No meetings yet. Install the extension or join a call to get started.
                </div>
              )}
              {recentMeetings.map((m, i) => {
                const platform = platformFromSourceOrUrl(m.source, m.meeting_url);
                const sentiment: Sentiment = (m.confidence_score ?? 50) >= 70 ? "positive" : (m.confidence_score ?? 0) >= 40 ? "neutral" : "negative";
                return (
                  <button
                    key={m.id}
                    onClick={() => router.push(`/meetings/${m.id}`)}
                    className={`flex w-full items-center gap-3 px-6 py-3 text-left transition-colors hover:bg-surface-soft ${
                      i < recentMeetings.length - 1 ? "border-b border-surface-border" : ""
                    }`}
                  >
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
                      <Video className="h-[18px] w-[18px] text-brand" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="mb-0.5 truncate text-[13.5px] font-bold text-ink">{m.title || "Untitled meeting"}</div>
                      <div className="truncate text-xs font-normal text-ink-secondary">
                        {m.created_by_name || "—"}
                      </div>
                    </div>
                    {/* Fixed-width right columns so each cell aligns vertically across rows */}
                    <div className="flex w-16 shrink-0 justify-center">
                      <PlatformBadge platform={platform} />
                    </div>
                    <div className="flex w-4 shrink-0 justify-center">
                      <SentimentDot sentiment={sentiment} />
                    </div>
                    <div className="w-14 shrink-0 text-right text-xs font-semibold tabular-nums text-ink-secondary">
                      {m.started_at ? new Date(m.started_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—"}
                    </div>
                    <div className="w-10 shrink-0 text-right text-[11px] font-normal tabular-nums text-ink-secondary">
                      {m.duration_seconds ? `${Math.round(m.duration_seconds / 60)}m` : "—"}
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {activeTab === "upcoming" && (
            <div>
              {upcoming.length === 0 && (
                <div className="px-6 py-8 text-center text-sm text-ink-secondary">
                  {calendarConnected ? "No upcoming meetings." : "Connect your calendar to see upcoming meetings."}
                </div>
              )}
              {upcoming.map((event, i) => {
                const platform = platformFromUrl(event.meeting_url ?? null);
                return (
                  <div
                    key={event.id}
                    className={`flex items-center gap-3 px-6 py-3 ${
                      i < upcoming.length - 1 ? "border-b border-surface-border" : ""
                    }`}
                  >
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
                      <Calendar className="h-[18px] w-[18px] text-brand" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-bold text-ink">{event.title || "Untitled"}</div>
                      <div className="text-xs font-normal text-ink-secondary">
                        {timeOfDay(event.start_time)} · {relativeDateLabel(event.start_time)}
                      </div>
                    </div>
                    <div className="flex w-16 shrink-0 justify-center">
                      <PlatformBadge platform={platform} />
                    </div>
                    <div className="flex w-9 shrink-0 justify-center">
                      <TogglePill
                        enabled={willBotJoin(event)}
                        onChange={() => toggleBotForEvent(event)}
                        disabled={togglingEventIds.has(event.id) || !event.meeting_url}
                        ariaLabel={willBotJoin(event) ? "Cancel bot for this meeting" : "Schedule bot for this meeting"}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>

      {/* Right calendar panel — mockup uses bgSoft panel with white cards */}
      <aside
        className={`shrink-0 overflow-hidden border-l border-surface-border bg-surface-soft transition-[width] duration-300 ${
          calendarOpen ? "w-[290px]" : "w-0"
        }`}
      >
        <div className="relative" style={{ minWidth: 290 }}>
          <button
            onClick={() => setCalendarOpen(false)}
            className="absolute -left-3.5 top-5 flex h-7 w-7 items-center justify-center rounded-full border border-surface-border bg-white shadow-sm hover:bg-surface-soft"
            aria-label="Collapse calendar"
          >
            <ChevronRight className="h-3.5 w-3.5 text-ink-secondary" />
          </button>
          <div className="px-5 py-6">
            <div className="mb-5 flex items-center gap-2">
              <Image src="/logo-icon.svg" alt="" width={26} height={26} className="rounded-md" />
              <span className="text-sm font-extrabold text-ink">Memoir Notetaker</span>
            </div>
            <button
              onClick={() => router.push("/integrations")}
              className="mb-3 flex w-full items-center gap-3 rounded-xl border border-surface-border bg-white px-4 py-3 text-left transition-colors hover:bg-brand-light/40"
            >
              <Calendar className="h-[15px] w-[15px] shrink-0 text-ink-secondary" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] font-bold text-ink">Calendar settings</span>
                  {calendarConnected && <CheckCircle2 className="h-3.5 w-3.5 text-brand-green" />}
                  {calendarTokenInvalid && (
                    <span className="rounded-full bg-amber-100 px-1.5 py-px text-[9px] font-bold text-amber-800">
                      Reconnect
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] font-medium text-ink-secondary">
                  Auto-join settings
                </div>
              </div>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-secondary" />
            </button>
            <div className="mb-5 flex items-center gap-2 rounded-xl border border-surface-border bg-white px-4 py-3 text-[13px] font-semibold text-ink">
              <Globe className="h-[15px] w-[15px] text-ink-secondary" />
              Meeting language
              <span className="ml-auto text-xs font-bold text-brand">English</span>
            </div>

            {/* Today's meetings only — the "today" header above the list
                belongs over actually-today events, not the full upcoming
                array. Otherwise events from other days get visually
                attributed to today. */}
            {(() => {
              const todayStart = new Date();
              todayStart.setHours(0, 0, 0, 0);
              const todayEnd = new Date(todayStart);
              todayEnd.setDate(todayEnd.getDate() + 1);
              const todaysEvents = upcoming.filter((ev) => {
                const t = new Date(ev.start_time).getTime();
                return t >= todayStart.getTime() && t < todayEnd.getTime();
              });
              return (
                <>
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <Calendar className="h-[15px] w-[15px] text-ink" />
                      <span className="text-[13px] font-extrabold text-ink">
                        {todaysEvents.length} {todaysEvents.length === 1 ? "Meeting" : "Meetings"}
                      </span>
                    </div>
                    <button
                      onClick={() => setScheduleOpen(true)}
                      className="flex items-center"
                      aria-label="Schedule meeting"
                      title="Schedule a meeting for the bot to join"
                    >
                      <Plus className="h-4 w-4 text-brand" />
                    </button>
                  </div>

                  <div className="mb-3 text-xs font-semibold text-ink-secondary">
                    {new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
                  </div>

                  {todaysEvents.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-surface-border bg-surface-soft px-4 py-5 text-center text-xs text-ink-secondary">
                      No meetings today.
                    </div>
                  ) : (
                    todaysEvents.map((event) => {
                      const platform = platformFromUrl(event.meeting_url ?? null);
                      return (
                        <div
                          key={event.id}
                          className="mb-2 rounded-xl border border-surface-border bg-white px-4 py-3"
                        >
                          <div className="mb-1.5 flex items-center justify-between">
                            <span className="text-xs font-semibold text-ink-secondary">{timeOfDay(event.start_time)}</span>
                            <div className="flex items-center gap-2">
                              <PlatformBadge platform={platform} />
                              <TogglePill
                                enabled={willBotJoin(event)}
                                onChange={() => toggleBotForEvent(event)}
                                disabled={togglingEventIds.has(event.id) || !event.meeting_url}
                                ariaLabel={willBotJoin(event) ? "Cancel bot for this meeting" : "Schedule bot for this meeting"}
                              />
                            </div>
                          </div>
                          <div className="text-[13.5px] font-bold text-ink">{event.title || "Untitled"}</div>
                        </div>
                      );
                    })
                  )}
                </>
              );
            })()}
          </div>
        </div>
      </aside>

      {!calendarOpen && (
        <button
          onClick={() => setCalendarOpen(true)}
          className="fixed right-0 top-1/2 z-10 flex -translate-y-1/2 items-center gap-2 rounded-l-xl border border-r-0 border-surface-border bg-white px-2.5 py-3.5 text-xs font-bold text-brand shadow-sm hover:bg-surface-soft"
        >
          <Calendar className="h-3.5 w-3.5" />
          <span className="[writing-mode:vertical-rl] [text-orientation:mixed]">
            {upcoming.length} Upcoming
          </span>
          <ChevronLeft className="h-3 w-3" />
        </button>
      )}

      <ScheduleMeetingModal open={scheduleOpen} onOpenChange={setScheduleOpen} />
    </div>
  );
}
