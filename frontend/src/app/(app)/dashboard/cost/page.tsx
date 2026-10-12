"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-4">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-zinc-900">{value}</p>
      {sub && <p className="text-xs text-zinc-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function CostPage() {
  const [period, setPeriod] = useState<"month" | "week" | "day">("month");
  const [granularity, setGranularity] = useState<"daily" | "weekly" | "monthly">("daily");

  const { data: summary, isLoading: loadingSummary } = useQuery({
    queryKey: ["admin-cost-summary", period],
    queryFn: () => api.getAdminCostSummary(period),
  });

  const { data: trends } = useQuery({
    queryKey: ["admin-cost-trends", granularity],
    queryFn: () => api.getAdminCostTrends(granularity),
  });

  const { data: byTeam } = useQuery({
    queryKey: ["admin-cost-by-team", period],
    queryFn: () => api.getAdminCostByTeam(period),
  });

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Period toggle */}
      <div className="flex items-center gap-2">
        {(["month", "week", "day"] as const).map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
              period === p ? "bg-indigo-600 text-white" : "bg-white border border-zinc-200 text-zinc-600 hover:bg-zinc-50"
            }`}
          >
            {p === "month" ? "This Month" : p === "week" ? "This Week" : "Today"}
          </button>
        ))}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total Cost"
          value={loadingSummary ? "—" : `$${summary?.total_cost?.toFixed(2) ?? "0.00"}`}
          sub={`Projected: $${summary?.projected_monthly?.toFixed(2) ?? "0"}/mo`}
        />
        <StatCard
          label="Meetings"
          value={loadingSummary ? "—" : String(summary?.total_meetings ?? 0)}
          sub={`$${summary?.avg_cost_per_meeting?.toFixed(4) ?? "0"} avg/meeting`}
        />
        <StatCard
          label="ASR Cost"
          value={loadingSummary ? "—" : `$${summary?.asr_cost?.toFixed(4) ?? "0"}`}
          sub={`${summary?.total_audio_hours?.toFixed(1) ?? 0} audio hrs`}
        />
        <StatCard
          label="Intelligence Cost"
          value={loadingSummary ? "—" : `$${summary?.intelligence_cost?.toFixed(4) ?? "0"}`}
        />
      </div>

      {/* Cost trend */}
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-900">Cost Trend</h2>
          <div className="flex gap-1">
            {(["daily", "weekly", "monthly"] as const).map((g) => (
              <button
                key={g}
                onClick={() => setGranularity(g)}
                className={`px-2.5 py-1 text-xs rounded transition-colors ${
                  granularity === g ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-500 hover:bg-zinc-200"
                }`}
              >
                {g.charAt(0).toUpperCase() + g.slice(1)}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                <th className="px-4 py-2 text-left">Period</th>
                <th className="px-4 py-2 text-right">Meetings</th>
                <th className="px-4 py-2 text-right">ASR Cost</th>
                <th className="px-4 py-2 text-right">Intelligence Cost</th>
                <th className="px-4 py-2 text-right">Total Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-50">
              {(trends || []).map((row, i) => (
                <tr key={i} className="hover:bg-zinc-50 transition-colors">
                  <td className="px-4 py-2.5 text-zinc-600 text-xs font-mono">{row.period?.split("T")[0]}</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.meetings}</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">${row.asr_cost.toFixed(4)}</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">${row.intelligence_cost.toFixed(4)}</td>
                  <td className="px-4 py-2.5 text-right font-medium text-zinc-900 text-xs">${row.total_cost.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* By recording source */}
      {summary?.cost_by_source && Object.keys(summary.cost_by_source).length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg">
          <div className="px-4 py-3 border-b border-zinc-100">
            <h2 className="text-sm font-semibold text-zinc-900">Cost by Recording Source</h2>
          </div>
          <div className="p-4 flex flex-wrap gap-4">
            {Object.entries(summary.cost_by_source).map(([source, cost]) => (
              <div key={source} className="text-center">
                <p className="text-lg font-bold text-zinc-900">${(cost as number).toFixed(4)}</p>
                <p className="text-xs text-zinc-400">{source}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Per-team table */}
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100">
          <h2 className="text-sm font-semibold text-zinc-900">Cost by Team</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                <th className="px-4 py-2 text-left">Team</th>
                <th className="px-4 py-2 text-right">Meetings</th>
                <th className="px-4 py-2 text-right">Audio Hours</th>
                <th className="px-4 py-2 text-right">Avg/Meeting</th>
                <th className="px-4 py-2 text-right">Total Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-50">
              {(byTeam || []).map((row) => (
                <tr key={row.team_id} className="hover:bg-zinc-50 transition-colors">
                  <td className="px-4 py-2.5 font-medium text-zinc-900">{row.team_name}</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.meetings}</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.total_audio_hours.toFixed(1)}h</td>
                  <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">${row.avg_cost_per_meeting.toFixed(4)}</td>
                  <td className="px-4 py-2.5 text-right font-semibold text-zinc-900 text-sm">${row.total_cost.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
