"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type {
  DealStagesResponse,
  DealStageSummary,
  DealStageCall,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 15;

const DATE_RANGES: { label: string; days: number | null }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "180 days", days: 180 },
  { label: "All time", days: null },
];

// ---------------------------------------------------------------------------
// Health badge colors
// ---------------------------------------------------------------------------

function healthColor(health: string | null): string {
  switch (health?.toLowerCase()) {
    case "strong":
    case "healthy":
      return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "at_risk":
    case "at risk":
    case "warning":
      return "bg-amber-50 text-amber-700 border-amber-200";
    case "stalled":
    case "cold":
    case "critical":
      return "bg-red-50 text-red-600 border-red-200";
    default:
      return "bg-gray-50 text-gray-500 border-gray-200";
  }
}

function momentumLabel(momentum: string | null): {
  text: string;
  color: string;
} {
  switch (momentum?.toLowerCase()) {
    case "accelerating":
      return { text: "Accelerating", color: "text-emerald-600" };
    case "steady":
      return { text: "Steady", color: "text-blue-600" };
    case "decelerating":
    case "slowing":
      return { text: "Slowing", color: "text-amber-600" };
    case "stalled":
      return { text: "Stalled", color: "text-red-500" };
    default:
      return { text: momentum || "--", color: "text-gray-400" };
  }
}

function pullScoreBar(score: number | null) {
  if (score == null) return null;
  const clamped = Math.max(0, Math.min(10, score));
  const pct = clamped * 10;
  let barColor = "bg-gray-300";
  if (clamped >= 7) barColor = "bg-emerald-500";
  else if (clamped >= 4) barColor = "bg-amber-400";
  else barColor = "bg-red-400";
  return (
    <div className="flex items-center gap-2 min-w-[100px]">
      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-semibold text-memoir-text tabular-nums w-5 text-right">
        {clamped}
      </span>
    </div>
  );
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "--";
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// ---------------------------------------------------------------------------
// Per-stage expandable section
// ---------------------------------------------------------------------------

interface StageData {
  calls: DealStageCall[];
  total: number;
  loading: boolean;
  hasMore: boolean;
  offset: number;
}

interface StageSectionProps {
  stage: DealStageSummary;
  teamId: string;
  days: number | null;
  isUnassigned?: boolean;
}

function StageSection({ stage, teamId, days, isUnassigned }: StageSectionProps) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<StageData>({
    calls: [],
    total: 0,
    loading: false,
    hasMore: true,
    offset: 0,
  });
  const loadedRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Load first page when expanded
  const loadCalls = useCallback(
    async (offset: number, append: boolean) => {
      if (!teamId) return;
      setData((prev) => ({ ...prev, loading: true }));
      try {
        const stageKey = isUnassigned
          ? "__unassigned__"
          : stage.stage_label;
        const res = await api.getDealStageCalls(
          teamId,
          stageKey,
          days ?? undefined,
          offset,
          PAGE_SIZE,
        );
        setData((prev) => {
          const merged = append
            ? [...prev.calls, ...res.calls]
            : res.calls;
          return {
            calls: merged,
            total: res.total,
            loading: false,
            hasMore: merged.length < res.total,
            offset: merged.length,
          };
        });
      } catch {
        setData((prev) => ({ ...prev, loading: false }));
      }
    },
    [teamId, days, stage.stage_label, isUnassigned],
  );

  // Lazy-load on first expand
  useEffect(() => {
    if (expanded && !loadedRef.current) {
      loadedRef.current = true;
      loadCalls(0, false);
    }
  }, [expanded, loadCalls]);

  // Reset when days change
  useEffect(() => {
    loadedRef.current = false;
    setData({ calls: [], total: 0, loading: false, hasMore: true, offset: 0 });
    if (expanded) {
      loadedRef.current = true;
      loadCalls(0, false);
    }
  }, [days]); // eslint-disable-line react-hooks/exhaustive-deps

  // Infinite scroll observer
  useEffect(() => {
    if (!expanded || !data.hasMore || data.loading) return;
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && data.hasMore && !data.loading) {
          loadCalls(data.offset, true);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [expanded, data.hasMore, data.loading, data.offset, loadCalls]);

  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-memoir overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50/60 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <svg
            className={`w-4 h-4 text-memoir-text-muted transition-transform duration-200 ${
              expanded ? "rotate-90" : ""
            }`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <h3 className="text-sm font-semibold text-memoir-text">
            {isUnassigned ? "Unassigned" : stage.stage_label}
          </h3>
          {isUnassigned && (
            <span className="text-xs text-memoir-text-muted">
              (no CRM stage)
            </span>
          )}
        </div>
        <span className="inline-flex items-center justify-center min-w-[28px] h-6 px-2 rounded-full bg-indigo-50 text-indigo-600 text-xs font-semibold tabular-nums">
          {stage.call_count}
        </span>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-memoir-border">
          {/* Column headers */}
          <div className="hidden md:grid md:grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr] gap-2 px-5 py-2 text-xs font-medium text-memoir-text-muted bg-gray-50/40 border-b border-memoir-border/50">
            <span>Call</span>
            <span>Date</span>
            <span>Prospect</span>
            <span>Pull Score</span>
            <span>Momentum</span>
            <span>Health</span>
          </div>

          {/* Call rows */}
          {data.calls.length === 0 && !data.loading && (
            <div className="px-5 py-6 text-center text-sm text-memoir-text-muted">
              No calls in this stage.
            </div>
          )}

          {data.calls.map((call) => {
            const mom = momentumLabel(call.deal_momentum);
            return (
              <button
                key={call.meeting_id}
                onClick={() => router.push(`/meetings/${call.meeting_id}`)}
                className="w-full text-left px-5 py-3 hover:bg-indigo-50/30 transition-colors border-b border-memoir-border/30 last:border-b-0"
              >
                {/* Desktop layout */}
                <div className="hidden md:grid md:grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr] gap-2 items-center">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-memoir-text truncate">
                      {call.title || "Untitled Call"}
                    </p>
                    {call.duration_seconds != null && (
                      <p className="text-xs text-memoir-text-muted mt-0.5">
                        {formatDuration(call.duration_seconds)}
                      </p>
                    )}
                  </div>
                  <span className="text-xs text-memoir-text-secondary tabular-nums">
                    {formatDate(call.date)}
                  </span>
                  <span className="text-xs text-memoir-text-secondary truncate">
                    {call.prospect_name || call.prospect_company || "--"}
                  </span>
                  <div>{pullScoreBar(call.pull_score)}</div>
                  <span className={`text-xs font-medium ${mom.color}`}>
                    {mom.text}
                  </span>
                  <div>
                    {call.call_health ? (
                      <span
                        className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full border ${healthColor(call.call_health)}`}
                      >
                        {call.call_health.replace(/_/g, " ")}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">--</span>
                    )}
                  </div>
                </div>

                {/* Mobile layout */}
                <div className="md:hidden space-y-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-medium text-memoir-text truncate flex-1">
                      {call.title || "Untitled Call"}
                    </p>
                    {call.call_health && (
                      <span
                        className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full border ${healthColor(call.call_health)}`}
                      >
                        {call.call_health.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-memoir-text-muted">
                    <span>{formatDate(call.date)}</span>
                    {call.prospect_name && (
                      <>
                        <span className="text-gray-300">|</span>
                        <span>{call.prospect_name}</span>
                      </>
                    )}
                    {call.duration_seconds != null && (
                      <>
                        <span className="text-gray-300">|</span>
                        <span>{formatDuration(call.duration_seconds)}</span>
                      </>
                    )}
                  </div>
                  <div className="flex items-center gap-4">
                    {call.pull_score != null && (
                      <div className="flex items-center gap-1">
                        <span className="text-xs text-memoir-text-muted">Pull:</span>
                        {pullScoreBar(call.pull_score)}
                      </div>
                    )}
                    <span className={`text-xs font-medium ${mom.color}`}>
                      {mom.text}
                    </span>
                  </div>
                </div>
              </button>
            );
          })}

          {/* Loading spinner */}
          {data.loading && (
            <div className="flex items-center justify-center py-4 gap-2">
              <div className="w-4 h-4 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
              <span className="text-xs text-memoir-text-muted">Loading calls...</span>
            </div>
          )}

          {/* Infinite scroll sentinel */}
          {data.hasMore && !data.loading && data.calls.length > 0 && (
            <div ref={sentinelRef} className="h-1" />
          )}

          {/* End indicator */}
          {!data.hasMore && data.calls.length > 0 && (
            <div className="px-5 py-2 text-center text-xs text-memoir-text-muted">
              Showing all {data.total} call{data.total !== 1 ? "s" : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="min-h-screen bg-[#F8F7F4]">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-4">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-4 w-32 bg-gray-200 rounded animate-pulse" />
        </div>
        <div className="flex items-center justify-between mb-4">
          <div className="h-7 w-48 bg-gray-200 rounded animate-pulse" />
          <div className="h-8 w-64 bg-gray-200 rounded-lg animate-pulse" />
        </div>
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="bg-white rounded-xl border border-memoir-border shadow-memoir p-5 animate-pulse"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="h-4 w-4 bg-gray-200 rounded" />
                <div className="h-4 w-36 bg-gray-200 rounded" />
              </div>
              <div className="h-6 w-8 bg-indigo-100 rounded-full" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state — no CRM connected
// ---------------------------------------------------------------------------

function NoCrmState() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const isAdmin =
    user?.team_role === "admin" || user?.team_role === "owner";

  return (
    <div className="min-h-screen bg-[#F8F7F4]">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
        <button
          onClick={() => router.push("/intelligence")}
          className="text-sm text-memoir-text-muted hover:text-indigo-600 transition-colors mb-6 flex items-center gap-1"
        >
          <span>&larr;</span> Back to Intelligence
        </button>

        <div className="bg-white rounded-xl border border-memoir-border shadow-memoir p-12 text-center">
          <div className="mx-auto w-14 h-14 rounded-full bg-indigo-50 flex items-center justify-center mb-5">
            <svg
              className="w-7 h-7 text-indigo-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m9.657-5.47a4.5 4.5 0 00-6.364-6.364L4.5 8.257a4.5 4.5 0 001.242 7.244"
              />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-memoir-text mb-2">
            Connect a CRM to see deal stages
          </h2>
          <p className="text-sm text-memoir-text-muted max-w-md mx-auto mb-6">
            Deal Intelligence groups your calls by CRM pipeline stages so you
            can track deal progression, momentum, and health at a glance.
          </p>
          {isAdmin ? (
            <button
              onClick={() => router.push("/integrations")}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
            >
              Go to Integrations
              <svg
                className="w-4 h-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"
                />
              </svg>
            </button>
          ) : (
            <p className="text-sm text-memoir-text-secondary">
              Ask your team admin to connect a CRM in Integrations.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DealIntelligencePage() {
  const router = useRouter();
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [loading, setLoading] = useState(true);
  const [stagesData, setStagesData] = useState<DealStagesResponse | null>(null);
  const [selectedDays, setSelectedDays] = useState<number | null>(90);
  const fetchedRef = useRef(false);

  const loadStages = useCallback(async () => {
    if (!activeTeamId) return;
    setLoading(true);
    try {
      const data = await api.getDealStages(
        activeTeamId,
        selectedDays ?? undefined,
      );
      setStagesData(data);
    } catch {
      setStagesData(null);
    } finally {
      setLoading(false);
    }
  }, [activeTeamId, selectedDays]);

  // Initial load + reload on team/days change
  useEffect(() => {
    if (!activeTeamId) return;
    fetchedRef.current = false;
    loadStages();
  }, [activeTeamId, loadStages]);

  // ── Loading state ──
  if (loading && !stagesData) {
    return <LoadingSkeleton />;
  }

  // ── No CRM connected ──
  if (stagesData && !stagesData.has_crm) {
    return <NoCrmState />;
  }

  // ── Sort stages by call count descending ──
  const sortedStages = [...(stagesData?.stages || [])].sort(
    (a, b) => b.call_count - a.call_count,
  );

  const totalStaged = sortedStages.reduce((sum, s) => sum + s.call_count, 0);
  const unassignedCount = stagesData?.unassigned_count ?? 0;
  const totalCalls = totalStaged + unassignedCount;

  return (
    <div className="min-h-screen bg-[#F8F7F4]">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
        {/* Back button */}
        <button
          onClick={() => router.push("/intelligence")}
          className="text-sm text-memoir-text-muted hover:text-indigo-600 transition-colors mb-6 flex items-center gap-1"
        >
          <span>&larr;</span> Back to Intelligence
        </button>

        {/* Header row */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
          <div>
            <h1 className="text-xl font-semibold text-memoir-text font-display">
              Deal Intelligence
            </h1>
            <p className="text-sm text-memoir-text-muted mt-1">
              {totalCalls} call{totalCalls !== 1 ? "s" : ""} across{" "}
              {sortedStages.length} stage{sortedStages.length !== 1 ? "s" : ""}
              {unassignedCount > 0 && ` + ${unassignedCount} unassigned`}
            </p>
          </div>

          {/* Date range selector */}
          <div className="flex items-center gap-1 bg-white rounded-lg border border-memoir-border p-0.5 shadow-sm">
            {DATE_RANGES.map((range) => (
              <button
                key={range.label}
                onClick={() => setSelectedDays(range.days)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                  selectedDays === range.days
                    ? "bg-indigo-600 text-white shadow-sm"
                    : "text-memoir-text-muted hover:text-memoir-text hover:bg-gray-50"
                }`}
              >
                {range.label}
              </button>
            ))}
          </div>
        </div>

        {/* Refreshing indicator */}
        {loading && stagesData && (
          <div className="flex items-center gap-2 mb-4 text-xs text-memoir-text-muted">
            <div className="w-3 h-3 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
            Updating...
          </div>
        )}

        {/* Stage sections */}
        <div className="space-y-3">
          {sortedStages.length === 0 && unassignedCount === 0 && (
            <div className="bg-white rounded-xl border border-memoir-border shadow-memoir p-10 text-center">
              <p className="text-sm text-memoir-text-muted">
                No calls found for this time period.
              </p>
              <p className="text-xs text-memoir-text-muted mt-1">
                Try expanding the date range or processing more calls.
              </p>
            </div>
          )}

          {sortedStages.map((stage) => (
            <StageSection
              key={stage.stage_id ?? stage.stage_label}
              stage={stage}
              teamId={activeTeamId!}
              days={selectedDays}
            />
          ))}

          {/* Unassigned section at bottom */}
          {unassignedCount > 0 && (
            <StageSection
              stage={{
                stage_label: "Unassigned",
                stage_id: null,
                call_count: unassignedCount,
                display_order: null,
              }}
              teamId={activeTeamId!}
              days={selectedDays}
              isUnassigned
            />
          )}
        </div>
      </div>
    </div>
  );
}
