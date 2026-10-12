"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-4">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-zinc-900">{value}</p>
      {sub && <p className="text-xs text-zinc-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function RiskBadge({ risk }: { risk: "low" | "medium" | "high" }) {
  const map = { low: "bg-green-100 text-green-700", medium: "bg-amber-100 text-amber-700", high: "bg-red-100 text-red-700" };
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${map[risk]}`}>{risk}</span>;
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed: "text-green-600",
    processing: "text-blue-600",
    failed: "text-red-600",
    pending: "text-zinc-400",
    recording: "text-purple-600",
  };
  return <span className={`text-xs font-medium ${map[status] || "text-zinc-500"}`}>{status}</span>;
}

export default function OverviewPage() {
  const { data: overview, isLoading: loadingOverview } = useQuery({
    queryKey: ["admin-overview"],
    queryFn: () => api.getAdminOverview(),
    refetchInterval: 60_000,
  });

  const { data: activity, isLoading: loadingActivity } = useQuery({
    queryKey: ["admin-activity"],
    queryFn: () => api.getAdminRecentActivity(15),
    refetchInterval: 60_000,
  });

  const { data: churn } = useQuery({
    queryKey: ["admin-churn-risk"],
    queryFn: () => api.getAdminChurnRisk(),
    refetchInterval: 300_000,
  });

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <StatCard label="Teams" value={loadingOverview ? "—" : (overview?.total_teams ?? 0)} />
        <StatCard label="Total Users" value={loadingOverview ? "—" : (overview?.total_users ?? 0)} />
        <StatCard label="Meetings Today" value={loadingOverview ? "—" : (overview?.meetings_today ?? 0)} sub={`${overview?.meetings_this_week ?? 0} this week`} />
        <StatCard
          label="Cost This Month"
          value={loadingOverview ? "—" : `$${overview?.total_cost_this_month?.toFixed(2) ?? "0.00"}`}
          sub={`${overview?.total_audio_hours_this_month?.toFixed(1) ?? 0} audio hrs`}
        />
        <StatCard
          label="Failed Today"
          value={loadingOverview ? "—" : (overview?.failed_jobs_today ?? 0)}
          sub={`${overview?.success_rate_today ?? 100}% success rate`}
        />
      </div>

      {/* Churn risk panel */}
      {churn && churn.length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg">
          <div className="px-4 py-3 border-b border-zinc-100">
            <h2 className="text-sm font-semibold text-zinc-900">Churn Risk — Inactive Teams</h2>
          </div>
          <div className="divide-y divide-zinc-50">
            {churn.slice(0, 5).map((row) => (
              <div key={row.team_id} className="flex items-center justify-between px-4 py-2.5">
                <div>
                  <span className="text-sm font-medium text-zinc-900">{row.team_name}</span>
                  <span className="text-xs text-zinc-400 ml-2">{row.member_count} members</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-zinc-500">{row.days_inactive}d inactive</span>
                  <RiskBadge risk={row.days_inactive > 14 ? "high" : "medium"} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent activity */}
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100">
          <h2 className="text-sm font-semibold text-zinc-900">Recent Activity</h2>
        </div>
        {loadingActivity ? (
          <div className="p-8 text-center text-sm text-zinc-400">Loading...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                  <th className="px-4 py-2 text-left">Team</th>
                  <th className="px-4 py-2 text-left">Meeting</th>
                  <th className="px-4 py-2 text-left">Source</th>
                  <th className="px-4 py-2 text-left">Duration</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-right">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-50">
                {(activity || []).map((row) => (
                  <tr key={row.meeting_id} className="hover:bg-zinc-50 transition-colors">
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">{row.team_name}</td>
                    <td className="px-4 py-2.5 font-medium text-zinc-900 max-w-[200px] truncate">{row.title}</td>
                    <td className="px-4 py-2.5 text-zinc-400 text-xs">{row.source || "—"}</td>
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">
                      {row.duration_seconds ? `${Math.round(row.duration_seconds / 60)}m` : "—"}
                    </td>
                    <td className="px-4 py-2.5"><StatusBadge status={row.status} /></td>
                    <td className="px-4 py-2.5 text-right text-xs text-zinc-500">
                      {row.cost_usd > 0 ? `$${row.cost_usd.toFixed(4)}` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
