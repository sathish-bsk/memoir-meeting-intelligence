"use client";

/**
 * Meetings list v2 — revamp UI (flag-gated by NEXT_PUBLIC_MEETINGS_V2=true).
 *
 * Sections:
 *  - Page header + inline stats
 *  - 60-day Activity Heatmap (SEED until /api/meetings/activity ships)
 *  - Hosted / Shared tabs (single-list fallback until backend ?scope= ships)
 *  - Filters dropdown (platform / time-range / topics)
 *  - List view (date-grouped cards) — REAL meetings
 *  - Board view (kanban by deal stage) — SEED stage labels
 *  - Bottom AI ask bar — disabled "Coming soon" until RAG endpoint
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Filter,
  List as ListIcon,
  LayoutGrid,
  Video,
  Share2,
  Globe,
} from "lucide-react";

import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { Meeting } from "@/lib/types";
import { PlatformBadge, type Platform, platformFromSourceOrUrl } from "@/components/ui/platform-badge";
import { Tag, tagToneFor } from "@/components/ui/tag";
import { MeetingStatusBadge } from "@/components/ui/meeting-status-badge";
import { MeetingPeekPanel } from "@/components/meeting-peek-panel";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function durationLabel(seconds: number | null): string {
  if (!seconds) return "—";
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

const BOARD_COLUMNS: { stage: string; tone: "blue" | "orange" | "amber" | "green" | "brand" }[] = [
  { stage: "Discovery", tone: "blue" },
  { stage: "Demo", tone: "orange" },
  { stage: "Negotiation", tone: "amber" },
  { stage: "Onboarding", tone: "green" },
  { stage: "Renewal", tone: "brand" },
];

// Stage-color reference for the kanban column header bar. Tag's tone covers
// the chip but the column also needs a left-border accent.
const STAGE_BORDER: Record<string, string> = {
  Discovery: "#3B82F6",
  Demo: "#E8683A",
  Negotiation: "#F5A623",
  Onboarding: "#00B87C",
  Renewal: "#5C5CE0",
};

function heatColor(count: number): string {
  if (count === 0) return "#F8F8FC";
  if (count === 1) return "rgba(92,92,224,0.18)";
  if (count === 2) return "rgba(92,92,224,0.42)";
  return "#5C5CE0";
}

export function MeetingsV2() {
  const router = useRouter();
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const teamRole = useAuthStore((s) => s.user?.team_role);
  const canSeeAll = teamRole === "admin" || teamRole === "owner";

  const [meetings, setMeetings] = useState<Meeting[]>([]);
  // Authoritative scope totals from the backend (`r.total`), each
  // respecting the active platform/time filters but NOT the tab.
  // Drives the "Hosted by me" / "Shared with me" chip badges so they
  // show the right number even when sitting on the other tab.
  const [hostedTotal, setHostedTotal] = useState(0);
  const [sharedTotal, setSharedTotal] = useState(0);
  // Admin/owner only — populated alongside hosted/shared on filter
  // change. Other roles don't see the All tab so this stays 0.
  const [allTotal, setAllTotal] = useState(0);
  // Static team stats for the page-header strip — intentionally NOT
  // re-fetched on filter changes so the "Total / Minutes / This week"
  // numbers stay as a stable reference even when the user narrows the
  // list below. Populated once per team from /api/meetings/stats.
  const [teamStats, setTeamStats] = useState<{ total: number; total_seconds: number; this_week: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"list" | "board">("list");
  const [tab, setTab] = useState<"hosted" | "shared" | "all">("hosted");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filterPlatform, setFilterPlatform] = useState<"all" | Platform>("all");
  const [filterTime, setFilterTime] = useState<"all" | "week" | "7d" | "30d">("all");
  const [peekMeeting, setPeekMeeting] = useState<Meeting | null>(null);
  // 60-day activity from GET /api/meetings/activity. Null = still loading;
  // empty array = endpoint returned no days (shouldn't happen but graceful).
  const [activity, setActivity] = useState<{ date: string; count: number }[] | null>(null);
  const filtersRef = useRef<HTMLDivElement>(null);

  // ── Infinite scroll state ─────────────────────────────────────────
  const PAGE_SIZE = 30;
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Backend filter params derived from the local toggle state. start_date
  // converts the "week"/"7d"/"30d" toggles into an ISO timestamp the
  // backend can compare against meetings.created_at.
  const startDateParam = useMemo(() => {
    if (filterTime === "all") return undefined;
    const days = filterTime === "30d" ? 30 : 7;
    return new Date(Date.now() - days * 86400000).toISOString();
  }, [filterTime]);
  const platformParam = filterPlatform === "all" ? undefined : (filterPlatform as "meet" | "zoom" | "teams");
  const scopeParam: "hosted" | "shared" | "all" = tab;

  // First page + refetch on any filter change. Each filter shift returns
  // the user to page 1 and replaces the list rather than appending, so
  // results don't mix across filter states.
  //
  // We also fire parallel `per_page=1` count requests for every other
  // visible tab so each chip shows the correct filtered count without
  // having to load that side's page data. Admin/owner gets three tabs
  // (hosted/shared/all); members get two.
  useEffect(() => {
    if (!activeTeamId) return;
    let cancelled = false;
    setLoading(true);
    setPage(1);
    const visibleScopes: Array<"hosted" | "shared" | "all"> = canSeeAll
      ? ["hosted", "shared", "all"]
      : ["hosted", "shared"];
    const requests = visibleScopes.map((sc) =>
      api.getMeetings({
        team_id: activeTeamId,
        page: 1,
        per_page: sc === scopeParam ? PAGE_SIZE : 1,
        scope: sc,
        platform: platformParam,
        start_date: startDateParam,
      }),
    );
    Promise.all(requests)
      .then((responses) => {
        if (cancelled) return;
        const totals: Record<"hosted" | "shared" | "all", number> = {
          hosted: 0,
          shared: 0,
          all: 0,
        };
        let activeMeetings: Meeting[] = [];
        let activeTotal = 0;
        responses.forEach((r, i) => {
          const sc = visibleScopes[i];
          totals[sc] = r.total ?? (r.meetings ?? []).length;
          if (sc === scopeParam) {
            activeMeetings = r.meetings ?? [];
            activeTotal = totals[sc];
          }
        });
        setMeetings(activeMeetings);
        setHostedTotal(totals.hosted);
        setSharedTotal(totals.shared);
        setAllTotal(totals.all);
        // hasMore = backend has more rows beyond what page 1 returned
        setHasMore(activeMeetings.length === PAGE_SIZE && activeMeetings.length < activeTotal);
      })
      .catch(() => {
        if (!cancelled) {
          setMeetings([]);
          setHasMore(false);
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [activeTeamId, scopeParam, platformParam, startDateParam, canSeeAll]);

  // Activity heatmap is independent of filters — load once per team.
  useEffect(() => {
    if (!activeTeamId) return;
    api
      .getMeetingsActivity(activeTeamId, 60)
      .then((r) => setActivity(r.days))
      .catch(() => setActivity([]));
  }, [activeTeamId]);

  // If role downgrades while the page is mounted (or the user's role is
  // resolved after first paint), clear a stale `all` selection — the
  // backend would 403 on the next list call.
  useEffect(() => {
    if (tab === "all" && !canSeeAll) setTab("hosted");
  }, [tab, canSeeAll]);

  // Team-wide stats — fetched once per team, never on filter change.
  useEffect(() => {
    if (!activeTeamId) return;
    api
      .getMeetingsStats(activeTeamId)
      .then((r) => setTeamStats(r))
      .catch(() => setTeamStats({ total: 0, total_seconds: 0, this_week: 0 }));
  }, [activeTeamId]);

  // Load next page when the sentinel scrolls into view.
  const loadMore = useCallback(async () => {
    if (!activeTeamId || !hasMore || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = page + 1;
      const r = await api.getMeetings({
        team_id: activeTeamId,
        page: next,
        per_page: PAGE_SIZE,
        scope: scopeParam,
        platform: platformParam,
        start_date: startDateParam,
      });
      const got = r.meetings ?? [];
      setMeetings((prev) => [...prev, ...got]);
      // Refresh the active-scope chip total in case the backend grew
      // between page loads. The other scopes are only refreshed on
      // filter change (cheap enough; not worth a separate ping per page).
      const currentScopeTotal =
        scopeParam === "hosted" ? hostedTotal : scopeParam === "shared" ? sharedTotal : allTotal;
      const activeTotal = r.total ?? currentScopeTotal;
      if (scopeParam === "hosted") setHostedTotal(activeTotal);
      else if (scopeParam === "shared") setSharedTotal(activeTotal);
      else setAllTotal(activeTotal);
      setPage(next);
      // Stop fetching when the latest page is short OR we've covered total
      setHasMore(
        got.length === PAGE_SIZE &&
          (meetings.length + got.length) < activeTotal,
      );
    } catch {
      // Don't flip hasMore false on transient errors — let the user
      // re-trigger by scrolling.
    } finally {
      setLoadingMore(false);
    }
  }, [
    activeTeamId,
    hasMore,
    loadingMore,
    page,
    scopeParam,
    platformParam,
    startDateParam,
    meetings.length,
    hostedTotal,
    sharedTotal,
    allTotal,
  ]);

  // IntersectionObserver on a sentinel near the bottom — fires loadMore
  // when it scrolls into view (with a 400px rootMargin so the fetch
  // starts before the user actually hits the bottom).
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) loadMore();
      },
      { rootMargin: "400px 0px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [hasMore, loadMore]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filtersRef.current && !filtersRef.current.contains(e.target as Node)) {
        setFiltersOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Real heatmap from /api/meetings/activity. While loading we show a
  // 60-cell zero-grid so the layout doesn't jump.
  const heatmap = useMemo(() => {
    if (activity && activity.length > 0) return activity;
    // Placeholder shape until the fetch resolves.
    const out: { date: string; count: number }[] = [];
    const today = new Date();
    for (let i = 59; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      out.push({ date: d.toISOString().slice(0, 10), count: 0 });
    }
    return out;
  }, [activity]);

  // All filtering moved to the backend (scope / platform / start_date
  // params on getMeetings). `meetings` reflects exactly what came back
  // for the current filter state, so the local list IS the filtered
  // list. Keep `filtered` as an alias so downstream code reads cleanly.
  const filtered = meetings;

  // Group by date for list view
  const dateGroups = useMemo(() => {
    const buckets = new Map<string, Meeting[]>();
    for (const m of filtered) {
      const key = formatDate(m.started_at || m.created_at);
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key)!.push(m);
    }
    return Array.from(buckets.entries());
  }, [filtered]);

  // Hosted/shared counts come from the backend totals (each filtered by
  // platform+time but not by tab), so they always reflect the full set
  // available for that scope under the current filter — not just what's
  // been paged in.
  const hostedCount = hostedTotal;
  const sharedCount = sharedTotal;
  // Header strip is intentionally filter-independent — comes from
  // /api/meetings/stats which ignores scope/platform/start_date.
  const totalMeetings = teamStats?.total ?? 0;
  const totalMinutes = Math.round((teamStats?.total_seconds ?? 0) / 60);
  const thisWeekCount = teamStats?.this_week ?? 0;

  return (
    <div className="flex h-full w-full overflow-hidden font-sans text-ink" style={{ fontSize: 14 }}>
      <div className="flex h-full min-w-0 flex-1 flex-col bg-surface-soft">
      <div className="flex-shrink-0 px-6 pt-6 lg:px-8">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h1 className="text-[26px] font-extrabold tracking-tight text-ink">Meetings</h1>
            <div className="mt-1.5 flex gap-4">
              {[
                { v: totalMeetings, l: "Total" },
                { v: totalMinutes, l: "Minutes" },
                { v: thisWeekCount, l: "This week" },
              ].map((s) => (
                <span key={s.l} className="text-xs font-medium text-ink-secondary">
                  <span className="text-sm font-extrabold text-ink">{s.v}</span> {s.l}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Activity heatmap — real data from /api/meetings/activity */}
        <div className="mb-4 rounded-xl border border-surface-border bg-white px-5 py-3.5 shadow-sm">
          <div className="mb-2.5 flex items-center justify-between">
            <span className="text-xs font-bold text-ink-secondary">Meeting Activity — Last 60 Days</span>
            <div className="flex items-center gap-1 text-[10px] text-ink-secondary">
              <span>Less</span>
              {[0, 1, 2, 3].map((i) => (
                <span key={i} className="h-2.5 w-2.5 rounded-sm" style={{ background: heatColor(i) }} />
              ))}
              <span>More</span>
            </div>
          </div>
          <div className="flex flex-nowrap gap-[3px] overflow-x-auto">
            {heatmap.map((d) => {
              // d.date is "YYYY-MM-DD" from backend; toLocaleDateString to humanize tooltip
              const human = new Date(d.date + "T00:00:00").toLocaleDateString("en-US", {
                weekday: "short",
                month: "short",
                day: "numeric",
              });
              return (
                <span
                  key={d.date}
                  title={`${human}: ${d.count} ${d.count === 1 ? "meeting" : "meetings"}`}
                  className="h-3 w-3 shrink-0 rounded-[3px]"
                  style={{ background: heatColor(d.count) }}
                />
              );
            })}
          </div>
        </div>

        {/* Tabs + view + filters */}
        <div className="mb-4 flex items-center justify-between">
          <div className="flex gap-2 rounded-xl border border-surface-border bg-white p-1">
            {[
              { k: "hosted" as const, l: "Hosted by me", count: hostedCount, icon: <Video className="h-3.5 w-3.5" /> },
              { k: "shared" as const, l: "Shared with me", count: sharedCount, icon: <Share2 className="h-3.5 w-3.5" /> },
              ...(canSeeAll
                ? [{ k: "all" as const, l: "All meetings", count: allTotal, icon: <Globe className="h-3.5 w-3.5" /> }]
                : []),
            ].map((t) => (
              <button
                key={t.k}
                onClick={() => setTab(t.k)}
                className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-[13px] font-bold transition-all ${
                  tab === t.k
                    ? "bg-brand text-white shadow-sm"
                    : "text-ink-secondary hover:text-ink"
                }`}
              >
                <span style={{ opacity: tab === t.k ? 1 : 0.6 }}>{t.icon}</span>
                {t.l}
                <span
                  className={`min-w-5 rounded-md px-1.5 py-px text-center text-[11px] font-extrabold ${
                    tab === t.k ? "bg-white/25 text-white" : "bg-surface-soft text-ink-secondary"
                  }`}
                >
                  {t.count}
                </span>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <div className="relative" ref={filtersRef}>
              <button
                onClick={() => setFiltersOpen((v) => !v)}
                className={`flex items-center gap-1.5 rounded-button border px-3 py-1.5 text-xs font-semibold transition-colors ${
                  filtersOpen
                    ? "border-brand bg-brand-light text-brand"
                    : "border-surface-border bg-white text-ink-secondary hover:text-ink"
                }`}
              >
                <Filter className="h-3.5 w-3.5" />
                Filters
                {(filterPlatform !== "all" || filterTime !== "all") && <span className="h-1.5 w-1.5 rounded-full bg-brand" />}
              </button>
              {filtersOpen && (
                <div className="absolute right-0 top-10 z-50 w-72 rounded-2xl border border-surface-border bg-white p-4 shadow-elevated">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="text-sm font-extrabold text-ink">Filters</span>
                    <button
                      onClick={() => {
                        setFilterPlatform("all");
                        setFilterTime("all");
                      }}
                      className="text-xs font-bold text-brand hover:underline"
                    >
                      Clear all
                    </button>
                  </div>
                  <div className="mb-3">
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-ink-secondary">Time range</div>
                    <div className="flex flex-wrap gap-1.5">
                      {([
                        { k: "all", l: "All time" },
                        { k: "week", l: "This week" },
                        { k: "7d", l: "Last 7 days" },
                        { k: "30d", l: "Last 30 days" },
                      ] as const).map((o) => (
                        <button
                          key={o.k}
                          onClick={() => setFilterTime(o.k)}
                          className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${
                            filterTime === o.k
                              ? "border-brand bg-brand-light text-brand"
                              : "border-surface-border bg-white text-ink-secondary"
                          }`}
                        >
                          {o.l}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-ink-secondary">Platform</div>
                    <div className="flex gap-1.5">
                      {([
                        { k: "all", l: "All" },
                        { k: "meet", l: "Meet" },
                        { k: "zoom", l: "Zoom" },
                        { k: "teams", l: "Teams" },
                      ] as const).map((o) => (
                        <button
                          key={o.k}
                          onClick={() => setFilterPlatform(o.k as "all" | Platform)}
                          className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${
                            filterPlatform === o.k
                              ? "border-brand bg-brand-light text-brand"
                              : "border-surface-border bg-white text-ink-secondary"
                          }`}
                        >
                          {o.l}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
            <div className="flex overflow-hidden rounded-button border border-surface-border bg-white">
              <button
                onClick={() => setView("list")}
                className={`flex items-center px-2.5 py-1.5 ${view === "list" ? "bg-brand-light" : ""}`}
                aria-label="List view"
              >
                <ListIcon className={`h-[15px] w-[15px] ${view === "list" ? "text-brand" : "text-ink-secondary"}`} />
              </button>
              <button
                onClick={() => setView("board")}
                className={`flex items-center border-l border-surface-border px-2.5 py-1.5 ${view === "board" ? "bg-brand-light" : ""}`}
                aria-label="Board view"
              >
                <LayoutGrid className={`h-[15px] w-[15px] ${view === "board" ? "text-brand" : "text-ink-secondary"}`} />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-6 pb-28 lg:px-8">
        {loading && <div className="py-8 text-center text-sm text-ink-secondary">Loading meetings…</div>}
        {!loading && filtered.length === 0 && (
          <div className="rounded-xl border border-dashed border-surface-border bg-white px-6 py-10 text-center text-sm text-ink-secondary">
            No meetings match these filters.
          </div>
        )}

        {!loading && view === "list" && (
          <div>
            {dateGroups.map(([label, group]) => (
              <div key={label} className="mb-2">
                <div className="sticky top-0 z-[2] bg-surface-soft py-3 text-xs font-bold text-ink-secondary">{label}</div>
                {group.map((m) => {
                  const platform = platformFromSourceOrUrl(m.source, m.meeting_url);
                  return (
                    <button
                      key={m.id}
                      onClick={() => setPeekMeeting(peekMeeting?.id === m.id ? null : m)}
                      className={`mb-2 flex w-full items-center gap-3 rounded-xl border bg-white px-5 py-4 text-left shadow-sm transition-all hover:shadow ${
                        peekMeeting?.id === m.id ? "border-brand/40 ring-1 ring-brand/20" : "border-surface-border hover:border-brand/40"
                      }`}
                    >
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft">
                        <Video className="h-[18px] w-[18px] text-brand" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <span className="truncate text-sm font-bold text-ink">{m.title || "Untitled"}</span>
                          <PlatformBadge platform={platform} />
                        </div>
                        <div className="flex items-center gap-1.5 text-xs font-normal text-ink-secondary">
                          <MeetingStatusBadge status={m.status} intelligenceStatus={m.intelligence_status} />
                          <span>·</span>
                          <span>{m.primary_participant_name || m.created_by_name || "—"}</span>
                          <span>·</span>
                          <span>{durationLabel(m.duration_seconds)}</span>
                          {m.started_at && (
                            <>
                              <span>·</span>
                              <span>{formatTime(m.started_at)}</span>
                            </>
                          )}
                        </div>
                      </div>
                      {m.summary_excerpt && (
                        <div className="hidden md:block">
                          <Tag label={m.summary_excerpt.slice(0, 24) + "…"} tone={tagToneFor(m.summary_excerpt) ?? "brand"} />
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}

            {/* Infinite-scroll sentinel — IntersectionObserver fires
                loadMore when this scrolls into view. Rendered only
                while there are more pages to fetch. */}
            {hasMore && (
              <div ref={sentinelRef} className="flex h-12 items-center justify-center text-xs text-ink-secondary">
                {loadingMore ? "Loading more meetings…" : ""}
              </div>
            )}
            {!hasMore && filtered.length > 0 && (
              <div className="flex h-8 items-center justify-center text-[11px] text-ink-light">
                All caught up ·{" "}
                {scopeParam === "hosted"
                  ? hostedTotal
                  : scopeParam === "shared"
                  ? sharedTotal
                  : allTotal}{" "}
                total
              </div>
            )}
          </div>
        )}

        {!loading && view === "board" && (
          <div className="flex min-h-[400px] gap-3.5 pb-5">
            {BOARD_COLUMNS.map((col) => {
              // Backend gap: meetings don't expose stage; show all in first column for now
              const items = col.stage === "Demo" ? filtered : [];
              return (
                <div key={col.stage} className="min-w-[180px] flex-1">
                  <div className="mb-3 flex items-center gap-2 py-2">
                    <span className="h-2 w-2 rounded-full" style={{ background: STAGE_BORDER[col.stage] }} />
                    <span className="text-[13px] font-extrabold text-ink">{col.stage}</span>
                    <span className="rounded-full bg-surface-soft px-1.5 py-px text-[11px] font-semibold text-ink-secondary">
                      {items.length}
                    </span>
                  </div>
                  {items.map((m) => {
                    const platform = platformFromSourceOrUrl(m.source, m.meeting_url);
                    return (
                      <button
                        key={m.id}
                        onClick={() => router.push(`/meetings/${m.id}`)}
                        className="mb-2 w-full rounded-xl border border-surface-border bg-white px-4 py-3.5 text-left shadow-sm hover:shadow"
                        style={{ borderLeftWidth: 3, borderLeftColor: STAGE_BORDER[col.stage] }}
                      >
                        <div className="mb-1.5 text-[13px] font-bold text-ink">{m.title || "Untitled"}</div>
                        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                          <MeetingStatusBadge status={m.status} intelligenceStatus={m.intelligence_status} />
                          <PlatformBadge platform={platform} />
                          <span>{durationLabel(m.duration_seconds)}</span>
                        </div>
                      </button>
                    );
                  })}
                  {items.length === 0 && (
                    <div className="rounded-xl border border-dashed border-surface-border bg-surface-soft p-5 text-center text-xs text-ink-secondary">
                      No meetings
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* AI ask bar removed — re-enable once a RAG "ask across meetings"
          backend endpoint exists. State (aiQuery) kept for that future
          re-enable; can be lifted to a flag-gated component later. */}
      </div>
      {peekMeeting && <MeetingPeekPanel meeting={peekMeeting} onClose={() => setPeekMeeting(null)} />}
    </div>
  );
}
