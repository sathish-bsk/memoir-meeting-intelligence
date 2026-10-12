"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

// function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
//   return (
//     <div className="bg-white border border-zinc-200 rounded-lg p-4">
//       <p className="text-xs text-zinc-500 mb-1">{label}</p>
//       <p className="text-2xl font-bold text-zinc-900">{value}</p>
//       {sub && <p className="text-xs text-zinc-400 mt-0.5">{sub}</p>}
//     </div>
//   );
// }

export default function CustomerDetailPage() {
  const { teamId } = useParams<{ teamId: string }>();

  const { data: detail, isLoading } = useQuery({
    queryKey: ["admin-customer-detail", teamId],
    queryFn: () => api.getAdminCustomerDetail(teamId),
  });

  if (isLoading) {
    return <div className="p-8 text-center text-sm text-zinc-400">Loading...</div>;
  }

  if (!detail) {
    return <div className="p-8 text-center text-sm text-zinc-400">Customer not found.</div>;
  }

  const sources: Record<string, number> = detail.recording_sources || {};
  const totalRecordings = Object.values(sources).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div>
        <h2 className="text-lg font-bold text-zinc-900">{detail.team_name}</h2>
        <p className="text-sm text-zinc-500 mt-0.5">
          {detail.members?.length ?? 0} members
          {totalRecordings > 0 && ` · ${totalRecordings} total recordings`}
        </p>
      </div>

      {/* Members */}
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100">
          <h3 className="text-sm font-semibold text-zinc-900">Members</h3>
        </div>
        <div className="divide-y divide-zinc-50">
          {(detail.members ?? []).map((m: { name: string; email: string; meetings_count: number; last_login: string | null }, i: number) => (
            <div key={i} className="flex items-center justify-between px-4 py-2.5">
              <div>
                <p className="text-sm font-medium text-zinc-900">{m.name}</p>
                <p className="text-xs text-zinc-400">{m.email}</p>
              </div>
              <div className="flex items-center gap-4 text-xs text-zinc-500">
                <span>{m.meetings_count} meetings</span>
                <span className="text-zinc-300">
                  {m.last_login ? new Date(m.last_login).toLocaleDateString() : "Never logged in"}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Recording sources */}
      {totalRecordings > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg">
          <div className="px-4 py-3 border-b border-zinc-100">
            <h3 className="text-sm font-semibold text-zinc-900">Recording Sources</h3>
          </div>
          <div className="p-4 flex flex-wrap gap-6">
            {Object.entries(sources).map(([source, count]) => (
              <div key={source} className="text-center">
                <p className="text-2xl font-bold text-zinc-900">{count}</p>
                <p className="text-xs text-zinc-400 capitalize">{source.replace("_", " ")}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Meetings trend */}
      {(detail.meetings_trend || []).length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg">
          <div className="px-4 py-3 border-b border-zinc-100">
            <h3 className="text-sm font-semibold text-zinc-900">Weekly Meeting Trend</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                  <th className="px-4 py-2 text-left">Week</th>
                  <th className="px-4 py-2 text-right">Meetings</th>
                  <th className="px-4 py-2 text-right">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-50">
                {(detail.meetings_trend ?? []).map((row: { week: string; count: number }, i: number) => {
                  const costRow = (detail.cost_trend ?? [])[i] as { week: string; cost: number } | undefined;
                  return (
                    <tr key={i} className="hover:bg-zinc-50">
                      <td className="px-4 py-2.5 text-xs font-mono text-zinc-500">
                        {row.week?.split("T")[0]}
                      </td>
                      <td className="px-4 py-2.5 text-right text-zinc-700 text-xs">{row.count}</td>
                      <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">
                        {costRow?.cost ? `$${costRow.cost.toFixed(4)}` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Integrations */}
      {detail.integrations_status && Object.keys(detail.integrations_status).length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg">
          <div className="px-4 py-3 border-b border-zinc-100">
            <h3 className="text-sm font-semibold text-zinc-900">Integrations</h3>
          </div>
          <div className="p-4 flex flex-wrap gap-3">
            {Object.entries(detail.integrations_status).map(([key, active]) => (
              <div
                key={key}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${
                  active ? "bg-green-100 text-green-700" : "bg-zinc-100 text-zinc-400"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${active ? "bg-green-500" : "bg-zinc-300"}`} />
                {key}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
