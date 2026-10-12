"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import Link from "next/link";
import type {
  IntelligenceOverview,
  PmfSignalsResponse,
} from "@/lib/types";

const DATE_RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
  { label: "All", value: 365 },
];

function exportToCSV(overview: IntelligenceOverview) {
  const rows: string[][] = [["Category", "Item", "Count", "Details"]];
  for (const pp of overview.pain_points) {
    rows.push(["Pain Point", pp.pain, String(pp.mention_count), (pp.quotes || []).join("; ")]);
  }
  for (const fr of overview.feature_requests) {
    rows.push(["Feature Request", fr.feature, String(fr.request_count), ""]);
  }
  for (const c of overview.competitors) {
    rows.push(["Competitor", c.competitor, String(c.mention_count), `Likes: ${c.likes.join(", ")} | Dislikes: ${c.dislikes.join(", ")}`]);
  }
  for (const o of overview.objections) {
    rows.push(["Objection", o.objection, String(o.count), o.category || ""]);
  }
  for (const q of overview.questions) {
    rows.push(["Question", q.question, String(q.ask_count), q.categories.join(", ")]);
  }

  const csv = rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `intelligence-export-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Icons ──────────────────────────────────────────────────────

function SpeechBubbleIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
    </svg>
  );
}

function LightbulbIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 0 0 1.5-.189m-1.5.189a6.01 6.01 0 0 1-1.5-.189m3.75 7.478a12.06 12.06 0 0 1-4.5 0m3.75 2.383a14.406 14.406 0 0 1-3 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 1 0-7.517 0c.85.493 1.509 1.333 1.509 2.316V18" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
    </svg>
  );
}

function HandshakeIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.042 21.672 13.684 16.6m0 0-2.51 2.225.569-9.47 5.227 7.917-3.286-.672Zm-7.518-.267A8.25 8.25 0 1 1 20.25 10.5M8.288 14.212A5.25 5.25 0 1 1 17.25 10.5" />
    </svg>
  );
}

function ChartIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0-5.94-2.281m5.94 2.28-2.28 5.941" />
    </svg>
  );
}

function HelpCircleIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z" />
    </svg>
  );
}

// ─── Star Preview ───────────────────────────────────────────────

function StarPreview({ stars }: { stars: number }) {
  const filled = Math.max(0, Math.min(5, Math.round(stars)));
  return <span className="text-amber-400">{"★".repeat(filled)}{"☆".repeat(5 - filled)}</span>;
}

// ─── Bucket Config ──────────────────────────────────────────────

interface BucketExtra {
  pmfSignals?: PmfSignalsResponse | null;
}

interface BucketConfig {
  key: string;
  label: string;
  icon: React.ReactNode;
  route: string;
  adminOnly?: boolean;
  getItems: (o: IntelligenceOverview, extra?: BucketExtra) => { text: string; count: number; richPreview?: React.ReactNode }[];
  getTotal: (o: IntelligenceOverview) => number;
  getSubtitle?: (o: IntelligenceOverview, extra?: BucketExtra) => string | null;
}

const BUCKETS: BucketConfig[] = [
  {
    key: "pain_points",
    label: "Voice of Customer",
    icon: <SpeechBubbleIcon />,
    route: "/intelligence/voice-of-customer",
    getItems: (o) => o.pain_points.slice(0, 3).map((p) => ({ text: p.pain, count: p.mention_count })),
    getTotal: (o) => o.pain_points.reduce((s, p) => s + p.mention_count, 0),
  },
  {
    key: "feature_requests",
    label: "Product & Features",
    icon: <LightbulbIcon />,
    route: "/intelligence/product-features",
    getItems: (o) => o.feature_requests.slice(0, 3).map((f) => ({ text: f.feature, count: f.request_count })),
    getTotal: (o) => o.feature_requests.reduce((s, f) => s + f.request_count, 0),
  },
  {
    key: "competitors",
    label: "Competitive Intel",
    icon: <ShieldIcon />,
    route: "/intelligence/competitive",
    adminOnly: true,
    getItems: (o) => o.competitors.slice(0, 3).map((c) => ({ text: c.competitor, count: c.mention_count })),
    getTotal: (o) => o.competitors.reduce((s, c) => s + c.mention_count, 0),
  },
  {
    key: "objections",
    label: "Deal Intelligence",
    icon: <HandshakeIcon />,
    route: "/intelligence/deal-intelligence",
    getItems: (o) => o.objections.slice(0, 3).map((obj) => ({ text: obj.objection, count: obj.count })),
    getTotal: (o) => o.objections.reduce((s, obj) => s + obj.count, 0),
    getSubtitle: () => "CRM stage grouping available in deep dive",
  },
  {
    key: "pmf_trend",
    label: "PMF Signals",
    icon: <ChartIcon />,
    route: "/intelligence/pmf-signals",
    adminOnly: true,
    getItems: (o, extra) => {
      const pmf = extra?.pmfSignals;
      if (pmf && pmf.total_calls > 0) {
        return [
          { text: "Feature Demand", count: pmf.feature_demand_stars, richPreview: <><StarPreview stars={pmf.feature_demand_stars} /> <span className="text-memoir-text-muted">Feature Demand</span></> },
          { text: "Buy Signals", count: pmf.buy_signal_stars, richPreview: <><StarPreview stars={pmf.buy_signal_stars} /> <span className="text-memoir-text-muted">Buy Signals</span></> },
          { text: "Pain Clarity", count: pmf.pain_clarity_stars, richPreview: <><StarPreview stars={pmf.pain_clarity_stars} /> <span className="text-memoir-text-muted">Pain Clarity</span></> },
        ];
      }
      // Fallback to old pmf_trend averages
      const latest = o.pmf_trend[o.pmf_trend.length - 1];
      if (!latest) return [];
      return [
        { text: `Pull Score: ${latest.avg_pull_score?.toFixed(1) || "-"}/10`, count: latest.call_count },
        { text: `Pain Fit: ${latest.avg_pain_fit?.toFixed(1) || "-"}/10`, count: latest.call_count },
        { text: `ICP Fit: ${latest.avg_icp_fit?.toFixed(1) || "-"}/10`, count: latest.call_count },
      ];
    },
    getTotal: (o) => o.pmf_trend.reduce((s, p) => s + p.call_count, 0),
    getSubtitle: (o, extra) => {
      const pmf = extra?.pmfSignals;
      if (!pmf || pmf.total_calls < 3) return "3+ calls needed for weekly digest";
      return null;
    },
  },
  {
    key: "questions",
    label: "FAQ",
    icon: <HelpCircleIcon />,
    route: "/intelligence/faq",
    getItems: (o) => o.questions.slice(0, 3).map((q) => ({ text: q.question, count: q.ask_count })),
    getTotal: (o) => o.questions.reduce((s, q) => s + q.ask_count, 0),
  },
];

// ─── Main Page ──────────────────────────────────────────────────

export default function IntelligencePage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [overview, setOverview] = useState<IntelligenceOverview | null>(null);
  const [pmfSignals, setPmfSignals] = useState<PmfSignalsResponse | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const fetchedRef = useRef(false);

  const isFounder = user?.team_role === "owner" || user?.team_role === "admin";

  useEffect(() => {
    if (!activeTeamId) return;
    fetchedRef.current = false;
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTeamId, days]);

  async function loadData() {
    if (!activeTeamId || fetchedRef.current) return;
    fetchedRef.current = true;
    setLoading(true);
    try {
      const [overviewData, pmfData] = await Promise.all([
        api.getIntelligenceOverview(activeTeamId, days),
        api.getPmfWeeklySignals(activeTeamId).catch(() => null),
      ]);
      setOverview(overviewData);
      setPmfSignals(pmfData);
    } catch {
      setOverview(null);
      setPmfSignals(null);
    } finally {
      setLoading(false);
    }
  }

  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-memoir-text font-display">Intelligence</h1>
            <p className="text-sm text-memoir-text-muted mt-0.5">Insights from your sales calls</p>
          </div>
        </div>
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-memoir-surface border border-memoir-border rounded-card p-6 animate-pulse">
              <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
              <div className="h-3 bg-memoir-bg rounded w-2/3" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!overview || overview.call_count === 0) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-memoir-text font-display">Intelligence</h1>
            <p className="text-sm text-memoir-text-muted mt-0.5">Insights from your sales calls</p>
          </div>
        </div>
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-12 text-center">
          <div className="w-12 h-12 bg-memoir-indigo-light rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-memoir-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5m.75-9 3-3 2.148 2.148A12.061 12.061 0 0 1 16.5 7.605" />
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-memoir-text mb-1">Your Intelligence tab is waiting</h3>
          <p className="text-sm text-memoir-text-muted max-w-sm mx-auto mb-4">
            Record your first sales call to start building intelligence.
          </p>
          <button
            onClick={() => router.push("/meetings")}
            className="px-4 py-2 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo-hover transition-colors"
          >
            Go to Calls &rarr;
          </button>
        </div>
      </div>
    );
  }

  const visibleBuckets = BUCKETS.filter((b) => !b.adminOnly || isFounder);

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-memoir-text font-display">Intelligence</h1>
          <p className="text-sm text-memoir-text-muted mt-0.5">
            {overview.call_count} call{overview.call_count !== 1 ? "s" : ""} analyzed
            {overview.date_range_start && ` since ${overview.date_range_start}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {DATE_RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => { fetchedRef.current = false; setDays(opt.value); }}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                days === opt.value
                  ? "bg-memoir-indigo text-white border-memoir-indigo"
                  : "bg-memoir-surface text-memoir-text-secondary border-memoir-border hover:border-memoir-indigo/30"
              }`}
            >
              {opt.label}
            </button>
          ))}
          <button
            onClick={() => exportToCSV(overview)}
            className="ml-2 text-xs px-3 py-1.5 rounded-full border border-memoir-border bg-memoir-surface text-memoir-text-secondary hover:border-memoir-indigo/30 transition-colors"
          >
            Export CSV
          </button>
        </div>
      </div>

      {/* Digest raw text hidden — data is surfaced through the deep-dive pages instead */}

      {/* Early-stage banner */}
      {overview.call_count > 0 && overview.call_count < 5 && (
        <div className="bg-memoir-indigo-light border border-memoir-indigo/20 rounded-card p-4 mb-4 text-center">
          <p className="text-sm text-memoir-indigo">
            {overview.call_count <= 2
              ? "Building your intelligence... More data coming as you record calls."
              : `Record ${5 - overview.call_count} more call${5 - overview.call_count !== 1 ? "s" : ""} to unlock full trend analysis.`}
          </p>
        </div>
      )}

      {/* Digest schedule link */}
      <div className="text-right mb-2">
        <Link
          href="/settings/notifications"
          className="text-xs text-memoir-indigo hover:text-memoir-indigo/80 font-medium transition-colors"
        >
          Digest schedule &rarr; Notification Settings
        </Link>
      </div>

      {/* Expandable Table */}
      <div className="bg-memoir-surface border border-memoir-border rounded-card overflow-hidden">
        {visibleBuckets.map((bucket, idx) => {
          const extra: BucketExtra = { pmfSignals };
          const items = bucket.getItems(overview, extra);
          const totalMentions = bucket.getTotal(overview);
          const subtitle = bucket.getSubtitle?.(overview, extra);
          const isOpen = expanded.has(bucket.key);
          const isLast = idx === visibleBuckets.length - 1;

          return (
            <div key={bucket.key} className={!isLast ? "border-b border-memoir-border" : ""}>
              {/* Row header — always visible */}
              <button
                onClick={() => toggleExpanded(bucket.key)}
                className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-memoir-bg/50 transition-colors"
              >
                {/* Expand chevron */}
                <svg
                  className={`w-4 h-4 text-memoir-text-muted transition-transform ${isOpen ? "rotate-90" : ""}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                </svg>

                {/* Icon */}
                <span className="text-memoir-indigo shrink-0">{bucket.icon}</span>

                {/* Label + count */}
                <span className="text-sm font-medium text-memoir-text flex-1">
                  {bucket.label}
                  <span className="ml-2 text-xs text-memoir-text-muted font-normal">
                    ({totalMentions})
                  </span>
                </span>

                {/* Preview items (collapsed only) */}
                {!isOpen && items.length > 0 && (
                  <span className="text-xs text-memoir-text-secondary truncate max-w-[40%] hidden sm:inline">
                    {items.some((it) => it.richPreview)
                      ? items.map((it, i) => (
                          <span key={i}>{i > 0 && " · "}{it.richPreview || it.text}</span>
                        ))
                      : items.map((it) => it.text).join(", ")
                    }
                  </span>
                )}
                {!isOpen && subtitle && (
                  <span className="text-[10px] text-memoir-text-muted hidden sm:inline shrink-0">
                    {subtitle}
                  </span>
                )}

                {/* Deep dive arrow */}
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    router.push(bucket.route);
                  }}
                  className="text-xs text-memoir-indigo hover:underline shrink-0 cursor-pointer"
                >
                  Deep dive &rarr;
                </span>
              </button>

              {/* Expanded content — top 3 items */}
              {isOpen && (
                <div className="px-5 pb-4 pl-14">
                  {subtitle && (
                    <p className="text-xs text-memoir-text-muted mb-2">{subtitle}</p>
                  )}
                  {items.length > 0 ? (
                    <div className="space-y-2">
                      {items.map((item, i) => (
                        <div key={i} className="flex items-center justify-between">
                          <span className="text-sm text-memoir-text-secondary truncate">
                            {item.richPreview || item.text}
                          </span>
                          {!item.richPreview && (
                            <span className="text-xs text-memoir-text-muted shrink-0 ml-3">
                              {item.count}x
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-memoir-text-muted">No data yet — record more calls to populate this section.</p>
                  )}
                  <button
                    onClick={() => router.push(bucket.route)}
                    className="mt-3 text-xs text-memoir-indigo hover:underline"
                  >
                    View all &rarr;
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
