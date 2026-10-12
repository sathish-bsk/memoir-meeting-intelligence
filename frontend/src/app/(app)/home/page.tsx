"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { DashboardStats, Meeting } from "@/lib/types";
import { SetupChecklist } from "@/components/setup-checklist";
import { useSetupSteps } from "@/hooks/use-setup-steps";

// ─── Helper functions ───────────────────────────────────────────────

function formatDuration(seconds: number | null): string {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m`;
  }
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function confidenceBorderColor(score: number | null): string {
  if (score === null) return "border-l-memoir-border";
  if (score >= 70) return "border-l-emerald-400";
  if (score >= 40) return "border-l-amber-400";
  return "border-l-red-400";
}

function weekOverWeekDelta(thisWeek: number, lastWeek: number): { label: string; positive: boolean | null } {
  if (lastWeek === 0 && thisWeek === 0) return { label: "No change", positive: null };
  if (lastWeek === 0) return { label: `+${thisWeek} from last week`, positive: true };
  const pct = Math.round(((thisWeek - lastWeek) / lastWeek) * 100);
  if (pct === 0) return { label: "No change", positive: null };
  return {
    label: `${pct > 0 ? "+" : ""}${pct}% vs last week`,
    positive: pct > 0,
  };
}

import { HomeV2 } from "@/components/home-v2";

// ─── Main Page Component ────────────────────────────────────────────

export default function HomePage() {
  // Revamp v2 — opt in via NEXT_PUBLIC_HOME_V2=true env. When unset / false,
  // the existing v1 layout renders unchanged.
  if (process.env.NEXT_PUBLIC_HOME_V2 === "true") {
    return <HomeV2 />;
  }
  return <HomePageV1 />;
}

function HomePageV1() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);

  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentCalls, setRecentCalls] = useState<Meeting[]>([]);
  const [calendarConnected, setCalendarConnected] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [crmConnected, setCrmConnected] = useState(false);
  const [slackConnected, setSlackConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [extensionInstalled, setExtensionInstalled] = useState(false);
  const fetchRef = useRef(false);

  // Detect Chrome extension via DOM attribute set by content script
  useEffect(() => {
    const check = () => document.documentElement.hasAttribute("data-memoir-ext");
    setExtensionInstalled(check());
    const timer = setTimeout(() => setExtensionInstalled(check()), 1000);
    return () => clearTimeout(timer);
  }, []);

  const isFounder = user?.team_role === "owner" || user?.team_role === "admin";
  const setupSteps = useSetupSteps(calendarConnected, crmConnected, extensionInstalled, slackConnected);
  const allSetupDone = setupSteps.every((s) => s.done);
  const firstName = user?.name?.split(" ")[0] || "there";

  useEffect(() => {
    if (!activeTeamId || !user || fetchRef.current) return;
    fetchRef.current = true;

    const userId = isFounder ? undefined : user.id;

    const fetchAll = async () => {
      const results = await Promise.allSettled([
        api.getDashboardStats(activeTeamId, userId),
        api.getMeetings({
          team_id: activeTeamId,
          page: 1,
          per_page: 5,
          status: "completed",
        }),
        Promise.any([
          api.getCalendarStatus("google"),
          api.getCalendarStatus("outlook"),
        ]).catch(() => null),
        api.getCrmConnections(),
        api.getSlackStatus(),
      ]);

      if (results[0].status === "fulfilled") setStats(results[0].value);
      if (results[1].status === "fulfilled") setRecentCalls(results[1].value.meetings);
      if (results[2].status === "fulfilled" && results[2].value) {
        setCalendarConnected(true);
      }
      if (results[3].status === "fulfilled" && results[3].value && results[3].value.length > 0) {
        setCrmConnected(true);
      }
      if (results[4].status === "fulfilled" && results[4].value) {
        setSlackConnected(true);
      }
      setLoading(false);
    };

    fetchAll();
  }, [activeTeamId, user, isFounder]);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto animate-pulse">
        <div className="h-8 bg-memoir-border rounded w-64 mb-8" />
        <div className="space-y-4">
          <div className="h-24 bg-memoir-border rounded-card" />
          <div className="h-64 bg-memoir-border rounded-card" />
        </div>
      </div>
    );
  }

  const hasFirstCall = (stats?.total_meetings ?? 0) > 0;

  // Empty state — no calls yet
  if (!hasFirstCall) {
    return (
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold text-memoir-text mb-2 font-display">
          Welcome, {firstName}
        </h1>
        <p className="text-sm text-memoir-text-muted mb-8">
          {isFounder ? "Your team's command center" : "Your personal dashboard"}
        </p>

        {!allSetupDone && (
          <SetupChecklist steps={setupSteps} />
        )}

        <div className="flex flex-col items-center justify-center py-16">
          <div className="w-16 h-16 bg-memoir-indigo/10 rounded-full flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-memoir-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-memoir-text mb-2">
            Ready to record your first call
          </h2>
          <p className="text-sm text-memoir-text-muted mb-6 max-w-sm text-center">
            Connect your calendar to automatically record meetings, or upload a recording to get started.
          </p>
          <div className="flex gap-3">
            <Link
              href="/integrations"
              className="px-4 py-2 text-sm font-medium bg-memoir-indigo text-white rounded-button hover:bg-memoir-indigo/90 transition-colors"
            >
              Connect Calendar
            </Link>
            <Link
              href="/meetings"
              className="px-4 py-2 text-sm font-medium text-memoir-text border border-memoir-border rounded-button hover:bg-memoir-bg transition-colors"
            >
              Upload Recording
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const delta = weekOverWeekDelta(stats?.this_week_meetings ?? 0, stats?.last_week_meetings ?? 0);

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-memoir-text mb-2 font-display">
        Welcome back, {firstName}
      </h1>
      <p className="text-sm text-memoir-text-muted mb-8">
        {isFounder ? "Your team's command center" : "Your personal dashboard"}
      </p>

      {!allSetupDone && (
        <SetupChecklist steps={setupSteps} />
      )}

      {/* This Week at a Glance */}
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-5 mb-6">
        <h2 className="text-sm font-semibold text-memoir-text mb-4">This Week at a Glance</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="text-center">
            <p className="text-2xl font-bold text-memoir-indigo">{stats?.this_week_meetings ?? 0}</p>
            <p className="text-xs text-memoir-text-muted mt-1">Calls this week</p>
          </div>
          <div className="text-center">
            <p className="text-2xl font-bold text-memoir-text">{stats?.total_meetings ?? 0}</p>
            <p className="text-xs text-memoir-text-muted mt-1">Total calls</p>
          </div>
          <div className="text-center">
            <p className="text-2xl font-bold text-memoir-text">{Math.round(stats?.total_minutes ?? 0)}</p>
            <p className="text-xs text-memoir-text-muted mt-1">Total minutes</p>
          </div>
          <div className="text-center">
            <p className={`text-2xl font-bold ${
              delta.positive === true
                ? "text-emerald-600"
                : delta.positive === false
                ? "text-red-500"
                : "text-memoir-text-muted"
            }`}>
              {delta.positive === true ? "+" : ""}{stats?.this_week_meetings ?? 0}{delta.positive !== null && (
                <span className="text-xs font-medium ml-0.5">
                  {delta.positive === false ? "\u2193" : "\u2191"}
                </span>
              )}
            </p>
            <p className="text-xs text-memoir-text-muted mt-1">vs last week</p>
          </div>
        </div>
      </div>

      {/* Recent Calls */}
      <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-memoir-text">Recent Calls</h2>
          <Link
            href="/meetings"
            className="text-xs text-memoir-indigo hover:text-memoir-indigo/80 font-medium transition-colors"
          >
            View all calls &rarr;
          </Link>
        </div>

        {recentCalls.length === 0 ? (
          <p className="text-sm text-memoir-text-muted py-4 text-center">
            No completed calls yet this period.
          </p>
        ) : (
          <div className="space-y-1">
            {recentCalls.map((call) => (
              <button
                key={call.id}
                onClick={() => router.push(`/meetings/${call.id}`)}
                className={`w-full flex items-center gap-3 p-3 rounded-button border-l-[3px] ${confidenceBorderColor(
                  call.confidence_score
                )} hover:bg-memoir-bg transition-colors text-left`}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-memoir-text truncate">
                    {call.title || "Untitled Call"}
                  </p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {call.primary_participant_name && (
                      <span className="text-xs text-memoir-text-muted truncate">
                        {call.primary_participant_name}
                      </span>
                    )}
                    {call.primary_participant_company && (
                      <span className="text-xs text-memoir-text-muted truncate">
                        &middot; {call.primary_participant_company}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
                  <span className="text-xs text-memoir-text-muted">
                    {formatDate(call.created_at)}
                  </span>
                  <span className="text-xs text-memoir-text-muted">
                    {formatDuration(call.duration_seconds)}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
