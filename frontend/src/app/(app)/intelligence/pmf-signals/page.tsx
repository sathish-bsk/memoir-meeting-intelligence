"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type {
  PmfSignalsResponse,
  PmfSignalItem,
  PmfHistoryResponse,
  PmfHistoryEntry,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------

function Stars({ count, max = 5 }: { count: number; max?: number }) {
  return (
    <div className="flex gap-0.5">
      {Array.from({ length: max }, (_, i) => (
        <svg
          key={i}
          className={`w-4 h-4 ${i < count ? "text-amber-400" : "text-gray-200"}`}
          fill="currentColor"
          viewBox="0 0 20 20"
        >
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      ))}
    </div>
  );
}

function TrendBadge({
  trend,
  prevCount,
}: {
  trend: string;
  prevCount?: number;
}) {
  if (trend === "up")
    return (
      <span className="text-emerald-600 text-xs font-medium">
        {"\u2191"} {prevCount != null ? `(+${prevCount})` : ""}
      </span>
    );
  if (trend === "down")
    return <span className="text-red-500 text-xs font-medium">{"\u2193"}</span>;
  if (trend === "new")
    return (
      <span className="text-indigo-500 text-xs font-medium">new</span>
    );
  return (
    <span className="text-gray-400 text-xs font-medium">{"\u2192"}</span>
  );
}

function Sparkline({
  data,
  max,
  color = "#4F46E5",
}: {
  data: number[];
  max: number;
  color?: string;
}) {
  if (data.length < 2) return null;
  const h = 28;
  const w = 80;
  const step = w / (data.length - 1);
  const safeMax = max > 0 ? max : 5;
  const points = data
    .map((v, i) => `${i * step},${h - (v / safeMax) * (h - 4) - 2}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="block">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {data.map((v, i) => (
        <circle
          key={i}
          cx={i * step}
          cy={h - (v / safeMax) * (h - 4) - 2}
          r="2.5"
          fill={color}
        />
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="space-y-5 animate-pulse">
      {/* Header skeleton */}
      <div className="flex items-center gap-3 mb-6">
        <div className="w-5 h-5 rounded bg-gray-200" />
        <div>
          <div className="h-3 w-16 bg-gray-200 rounded mb-1" />
          <div className="h-6 w-32 bg-gray-200 rounded" />
        </div>
      </div>
      {/* Cards skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="bg-white rounded-xl border border-memoir-border p-5 h-56"
          >
            <div className="h-4 w-28 bg-gray-200 rounded mb-3" />
            <div className="h-4 w-16 bg-gray-100 rounded mb-4" />
            <div className="space-y-2">
              <div className="h-3 w-full bg-gray-100 rounded" />
              <div className="h-3 w-3/4 bg-gray-100 rounded" />
              <div className="h-3 w-1/2 bg-gray-100 rounded" />
            </div>
          </div>
        ))}
      </div>
      {/* Narrative skeleton */}
      <div className="bg-white rounded-xl border border-memoir-border p-5 h-20">
        <div className="h-3 w-full bg-gray-100 rounded mb-2" />
        <div className="h-3 w-2/3 bg-gray-100 rounded" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-card p-12 text-center">
      <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-indigo-50 flex items-center justify-center">
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
            d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
          />
        </svg>
      </div>
      <h3 className="text-base font-semibold text-memoir-text font-display mb-1">
        Not enough data yet
      </h3>
      <p className="text-sm text-memoir-text-muted max-w-md mx-auto">
        PMF Signals needs at least 3 analyzed calls to surface meaningful
        product-market fit patterns. Record more calls and run intelligence
        analysis to get started.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouped dimension card (Feature Demand, Objections, Competitors)
// ---------------------------------------------------------------------------

function GroupedDimensionCard({
  title,
  icon,
  items,
  stars,
  signalType,
  teamId,
}: {
  title: string;
  icon: React.ReactNode;
  items: PmfSignalItem[];
  stars: number;
  signalType: string;
  teamId: string;
}) {
  const router = useRouter();
  const topItems = items.slice(0, 5);
  const totalMentions = items.reduce((s, it) => s + it.mentions, 0);

  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 flex flex-col">
      {/* Card header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
            {icon}
          </div>
          <div>
            <h3 className="text-sm font-semibold text-memoir-text font-display leading-tight">
              {title}
            </h3>
            <p className="text-xs text-memoir-text-muted">
              {totalMentions} total mention{totalMentions !== 1 ? "s" : ""}
            </p>
          </div>
        </div>
        <Stars count={stars} />
      </div>

      {/* Items list */}
      {topItems.length === 0 ? (
        <p className="text-xs text-memoir-text-muted italic py-4 text-center flex-1">
          No signals detected this week
        </p>
      ) : (
        <div className="flex-1 space-y-2">
          {topItems.map((item, idx) => (
            <div
              key={idx}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <span className="text-xs text-memoir-text-muted w-4 text-right shrink-0">
                  {idx + 1}.
                </span>
                <span className="text-memoir-text truncate">{item.value}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-xs text-memoir-text-secondary tabular-nums">
                  {item.calls_mentioned} call{item.calls_mentioned !== 1 ? "s" : ""}
                </span>
                <TrendBadge trend={item.trend} prevCount={item.prev_count} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* View all link */}
      {items.length > 0 && (
        <button
          onClick={() =>
            router.push(
              `/intelligence/pmf-signals/drill?type=${signalType}&team=${teamId}`
            )
          }
          className="mt-3 pt-3 border-t border-memoir-border text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors text-left"
        >
          View all {items.length} {"\u2192"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Buy Signal Strength card
// ---------------------------------------------------------------------------

function BuySignalCard({
  rate,
  stars,
  prevRate,
}: {
  rate: number | null;
  stars: number;
  prevRate: number | null;
}) {
  const pct = rate != null ? Math.round(rate * 100) : null;
  const prevPct = prevRate != null ? Math.round(prevRate * 100) : null;
  const diff = pct != null && prevPct != null ? pct - prevPct : null;

  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 flex flex-col">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center shrink-0">
            <svg
              className="w-4 h-4 text-emerald-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.518l2.74-1.22m0 0l-5.94-2.281m5.94 2.28l-2.28 5.941"
              />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-memoir-text font-display leading-tight">
              Buy Signal Strength
            </h3>
            <p className="text-xs text-memoir-text-muted">
              Calls showing buying intent
            </p>
          </div>
        </div>
        <Stars count={stars} />
      </div>

      <div className="flex-1 flex flex-col items-center justify-center py-4">
        {pct != null ? (
          <>
            <span className="text-4xl font-bold text-memoir-text tabular-nums">
              {pct}%
            </span>
            <p className="text-xs text-memoir-text-muted mt-1">
              of calls contain buy signals
            </p>
            {diff != null && (
              <p className="text-xs mt-2">
                {diff > 0 ? (
                  <span className="text-emerald-600 font-medium">
                    {"\u2191"} +{diff}pp vs previous week
                  </span>
                ) : diff < 0 ? (
                  <span className="text-red-500 font-medium">
                    {"\u2193"} {diff}pp vs previous week
                  </span>
                ) : (
                  <span className="text-gray-400 font-medium">
                    {"\u2192"} Unchanged vs previous week
                  </span>
                )}
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-memoir-text-muted italic">
            No data this week
          </p>
        )}
      </div>

      {/* Progress bar */}
      {pct != null && (
        <div className="mt-auto pt-3 border-t border-memoir-border">
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pain Clarity card
// ---------------------------------------------------------------------------

function PainClarityCard({
  rate,
  stars,
  topPain,
  prevRate,
}: {
  rate: number | null;
  stars: number;
  topPain: string | null;
  prevRate: number | null;
}) {
  const pct = rate != null ? Math.round(rate * 100) : null;
  const prevPct = prevRate != null ? Math.round(prevRate * 100) : null;
  const diff = pct != null && prevPct != null ? pct - prevPct : null;

  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 flex flex-col">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center shrink-0">
            <svg
              className="w-4 h-4 text-amber-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
              />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-memoir-text font-display leading-tight">
              Pain Clarity
            </h3>
            <p className="text-xs text-memoir-text-muted">
              Pain convergence across calls
            </p>
          </div>
        </div>
        <Stars count={stars} />
      </div>

      <div className="flex-1 flex flex-col items-center justify-center py-4">
        {pct != null ? (
          <>
            <span className="text-4xl font-bold text-memoir-text tabular-nums">
              {pct}%
            </span>
            <p className="text-xs text-memoir-text-muted mt-1">
              convergence rate
            </p>
            {diff != null && (
              <p className="text-xs mt-2">
                {diff > 0 ? (
                  <span className="text-emerald-600 font-medium">
                    {"\u2191"} +{diff}pp vs previous week
                  </span>
                ) : diff < 0 ? (
                  <span className="text-red-500 font-medium">
                    {"\u2193"} {diff}pp vs previous week
                  </span>
                ) : (
                  <span className="text-gray-400 font-medium">
                    {"\u2192"} Unchanged vs previous week
                  </span>
                )}
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-memoir-text-muted italic">
            No data this week
          </p>
        )}
      </div>

      {/* Top pain */}
      {topPain && (
        <div className="mt-auto pt-3 border-t border-memoir-border">
          <p className="text-xs text-memoir-text-muted mb-1">Top pain point</p>
          <p className="text-sm text-memoir-text font-medium truncate">
            {topPain}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// History panel (sparkline trends over weeks)
// ---------------------------------------------------------------------------

function HistoryPanel({ history }: { history: PmfHistoryEntry[] }) {
  if (history.length < 2) {
    return (
      <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 text-center">
        <p className="text-xs text-memoir-text-muted">
          Need at least 2 weeks of data to show trends.
        </p>
      </div>
    );
  }

  const dimensions: {
    label: string;
    key: keyof PmfHistoryEntry;
    color: string;
  }[] = [
    { label: "Feature Demand", key: "feature_demand_stars", color: "#4F46E5" },
    { label: "Objections", key: "objections_stars", color: "#DC2626" },
    { label: "Competitors", key: "competitors_stars", color: "#7C3AED" },
    { label: "Buy Signal", key: "buy_signal_stars", color: "#059669" },
    { label: "Pain Clarity", key: "pain_clarity_stars", color: "#D97706" },
  ];

  return (
    <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5">
      <h3 className="text-sm font-semibold text-memoir-text font-display mb-4">
        Weekly Trends
      </h3>
      <div className="space-y-3">
        {dimensions.map((dim) => {
          const values = history.map(
            (w) => (w[dim.key] as number) ?? 0
          );
          const latest = values[values.length - 1];
          return (
            <div
              key={dim.key}
              className="flex items-center justify-between gap-4"
            >
              <span className="text-xs text-memoir-text-secondary w-28 shrink-0">
                {dim.label}
              </span>
              <Sparkline data={values} max={5} color={dim.color} />
              <div className="flex items-center gap-1 w-16 justify-end shrink-0">
                <Stars count={latest} max={5} />
              </div>
            </div>
          );
        })}
      </div>
      {/* Week labels */}
      <div className="flex items-center justify-between mt-3 pt-2 border-t border-memoir-border/50">
        {history.map((w, i) => (
          <span key={i} className="text-[10px] text-memoir-text-muted">
            {new Date(w.week_start).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            })}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function PMFSignalsPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  const [data, setData] = useState<PmfSignalsResponse | null>(null);
  const [history, setHistory] = useState<PmfHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const [historyWeeks, setHistoryWeeks] = useState(4);
  const [historyLoading, setHistoryLoading] = useState(false);
  const fetchedRef = useRef(false);

  // Role guard - redirect members
  useEffect(() => {
    if (user?.team_role === "member") {
      router.replace("/intelligence");
    }
  }, [user, router]);

  // Fetch weekly signals
  useEffect(() => {
    if (!activeTeamId) return;
    fetchedRef.current = false;
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId]);

  async function loadData() {
    if (!activeTeamId || fetchedRef.current) return;
    fetchedRef.current = true;
    setLoading(true);
    try {
      const result = await api.getPmfWeeklySignals(activeTeamId);
      setData(result);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  // Fetch history when toggled
  const loadHistory = useCallback(
    async (weeks: number) => {
      if (!activeTeamId) return;
      setHistoryLoading(true);
      try {
        const result = await api.getPmfHistory(activeTeamId, weeks);
        setHistory(result);
      } catch {
        setHistory(null);
      } finally {
        setHistoryLoading(false);
      }
    },
    [activeTeamId]
  );

  useEffect(() => {
    if (showHistory && activeTeamId) {
      loadHistory(historyWeeks);
    }
  }, [showHistory, historyWeeks, activeTeamId, loadHistory]);

  // Don't render for members
  if (user?.team_role === "member") return null;

  const isMinimumCalls = data != null && data.total_calls >= 3;

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/intelligence")}
            className="text-memoir-text-muted hover:text-memoir-text transition-colors"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18"
              />
            </svg>
          </button>
          <div>
            <p className="text-xs text-memoir-text-muted">Intelligence</p>
            <h1 className="text-xl font-bold text-memoir-text font-display">
              PMF Signals
            </h1>
          </div>
        </div>

        {/* History toggle + week selector */}
        {isMinimumCalls && (
          <div className="flex items-center gap-3">
            {showHistory && (
              <div className="flex items-center gap-1">
                {[4, 8].map((w) => (
                  <button
                    key={w}
                    onClick={() => setHistoryWeeks(w)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                      historyWeeks === w
                        ? "bg-memoir-indigo text-white border-memoir-indigo"
                        : "bg-white text-memoir-text-secondary border-memoir-border hover:border-memoir-indigo/30"
                    }`}
                  >
                    {w}w
                  </button>
                ))}
              </div>
            )}
            <button
              onClick={() => setShowHistory((prev) => !prev)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors flex items-center gap-1.5 ${
                showHistory
                  ? "bg-memoir-indigo text-white border-memoir-indigo"
                  : "bg-white text-memoir-text-secondary border-memoir-border hover:border-memoir-indigo/30"
              }`}
            >
              <svg
                className="w-3.5 h-3.5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
                />
              </svg>
              History
            </button>
          </div>
        )}
      </div>

      {/* Week badge */}
      {data?.week_start && (
        <div className="mb-5">
          <span className="inline-flex items-center gap-1.5 text-xs bg-indigo-50 text-indigo-700 px-3 py-1 rounded-full font-medium">
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
              />
            </svg>
            Week of{" "}
            {new Date(data.week_start).toLocaleDateString("en-US", {
              month: "long",
              day: "numeric",
              year: "numeric",
            })}
            {" \u00B7 "}
            {data.total_calls} call{data.total_calls !== 1 ? "s" : ""} analyzed
          </span>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <LoadingSkeleton />
      ) : !data || !isMinimumCalls ? (
        <EmptyState />
      ) : (
        <div className="space-y-5">
          {/* 5 Signal Cards in grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {/* 1. Feature Demand */}
            <GroupedDimensionCard
              title="Feature Demand"
              icon={
                <svg
                  className="w-4 h-4 text-indigo-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09ZM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456Z"
                  />
                </svg>
              }
              items={data.feature_demand}
              stars={data.feature_demand_stars}
              signalType="feature_demand"
              teamId={activeTeamId!}
            />

            {/* 2. Objection Frequency */}
            <GroupedDimensionCard
              title="Objection Frequency"
              icon={
                <svg
                  className="w-4 h-4 text-red-500"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 9v3.75m0-10.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z"
                  />
                </svg>
              }
              items={data.objections}
              stars={data.objections_stars}
              signalType="objections"
              teamId={activeTeamId!}
            />

            {/* 3. Competitor Mentions */}
            <GroupedDimensionCard
              title="Competitor Mentions"
              icon={
                <svg
                  className="w-4 h-4 text-violet-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0Zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0Zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0Z"
                  />
                </svg>
              }
              items={data.competitors}
              stars={data.competitors_stars}
              signalType="competitors"
              teamId={activeTeamId!}
            />

            {/* 4. Buy Signal Strength */}
            <BuySignalCard
              rate={data.buy_signal_rate}
              stars={data.buy_signal_stars}
              prevRate={data.buy_signal_prev_rate}
            />

            {/* 5. Pain Clarity */}
            <PainClarityCard
              rate={data.pain_clarity_rate}
              stars={data.pain_clarity_stars}
              topPain={data.pain_clarity_top_pain}
              prevRate={data.pain_clarity_prev_rate}
            />
          </div>

          {/* History section */}
          {showHistory && (
            <div>
              {historyLoading ? (
                <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 animate-pulse h-48" />
              ) : history?.weeks ? (
                <HistoryPanel history={history.weeks} />
              ) : (
                <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5 text-center">
                  <p className="text-xs text-memoir-text-muted">
                    No history data available yet.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Narrative paragraph */}
          {data.narrative && (
            <div className="bg-white rounded-xl border border-memoir-border shadow-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-6 h-6 rounded-md bg-indigo-50 flex items-center justify-center">
                  <svg
                    className="w-3.5 h-3.5 text-indigo-600"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
                    />
                  </svg>
                </div>
                <h3 className="text-sm font-semibold text-memoir-text font-display">
                  Weekly Summary
                </h3>
              </div>
              <p className="text-sm text-memoir-text-secondary leading-relaxed">
                {data.narrative}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
