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

export default function ProcessingPage() {
  const { data: stats, isLoading: loadingStats } = useQuery({
    queryKey: ["admin-processing-stats"],
    queryFn: () => api.getAdminProcessingStats(),
    refetchInterval: 30_000,
  });

  const { data: failures, isLoading: loadingFailures } = useQuery({
    queryKey: ["admin-processing-failures"],
    queryFn: () => api.getAdminProcessingFailures(),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Queue stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Active Jobs"
          value={loadingStats ? "—" : (stats?.active_jobs ?? 0)}
          sub="currently running"
        />
        <StatCard
          label="Waiting"
          value={loadingStats ? "—" : (stats?.waiting_jobs ?? 0)}
          sub="in queue"
        />
        <StatCard
          label="Completed Today"
          value={loadingStats ? "—" : (stats?.completed_today ?? 0)}
        />
        <StatCard
          label="Failed Today"
          value={loadingStats ? "—" : (stats?.failed_today ?? 0)}
          sub={`${stats?.success_rate_7d ?? 100}% success (7d)`}
        />
      </div>

      {/* Additional metrics */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <StatCard
          label="Avg Processing Time"
          value={
            loadingStats || !stats?.avg_processing_seconds
              ? "—"
              : `${Math.round(stats.avg_processing_seconds)}s`
          }
          sub="per job"
        />
        <StatCard
          label="Queue Depth"
          value={loadingStats ? "—" : (stats?.queue_depth ?? 0)}
          sub="pending jobs total"
        />
        <StatCard
          label="Success Rate (7d)"
          value={loadingStats ? "—" : `${stats?.success_rate_7d ?? 100}%`}
        />
      </div>

      {/* Failures table */}
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-900">Recent Failures (7 days)</h2>
          <span className="text-xs text-zinc-400">{failures?.length ?? 0} failures</span>
        </div>
        {loadingFailures ? (
          <div className="p-8 text-center text-sm text-zinc-400">Loading...</div>
        ) : (failures || []).length === 0 ? (
          <div className="p-8 text-center text-sm text-zinc-400">No failures in the last 7 days.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                  <th className="px-4 py-2 text-left">Team</th>
                  <th className="px-4 py-2 text-left">Meeting</th>
                  <th className="px-4 py-2 text-left">Error</th>
                  <th className="px-4 py-2 text-right">Retries</th>
                  <th className="px-4 py-2 text-right">Failed At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-50">
                {(failures || []).map((row) => (
                  <tr key={row.job_id} className="hover:bg-zinc-50 transition-colors">
                    <td className="px-4 py-2.5 text-zinc-500 text-xs">{row.team_name || "—"}</td>
                    <td className="px-4 py-2.5 text-xs font-mono text-zinc-400 max-w-[160px] truncate">
                      {row.meeting_id ? row.meeting_id.split("-")[0] : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-red-600 max-w-[300px] truncate">
                      {row.error_message || "Unknown error"}
                    </td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.retry_count}</td>
                    <td className="px-4 py-2.5 text-right text-zinc-400 text-xs">
                      {row.failed_at ? new Date(row.failed_at).toLocaleDateString() : "—"}
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
