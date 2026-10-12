"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

function RiskBadge({ risk }: { risk: "low" | "medium" | "high" }) {
  const map = {
    low: "bg-green-100 text-green-700",
    medium: "bg-amber-100 text-amber-700",
    high: "bg-red-100 text-red-700",
  };
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${map[risk]}`}>{risk}</span>;
}

export default function CustomersPage() {
  const router = useRouter();

  const { data: customers, isLoading } = useQuery({
    queryKey: ["admin-customers"],
    queryFn: () => api.getAdminCustomers(),
    refetchInterval: 300_000,
  });

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="bg-white border border-zinc-200 rounded-lg">
        <div className="px-4 py-3 border-b border-zinc-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-zinc-900">All Customers</h2>
          <span className="text-xs text-zinc-400">{customers?.length ?? 0} teams</span>
        </div>
        {isLoading ? (
          <div className="p-8 text-center text-sm text-zinc-400">Loading...</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-xs text-zinc-400 uppercase tracking-wider">
                  <th className="px-4 py-2 text-left">Team</th>
                  <th className="px-4 py-2 text-right">Members</th>
                  <th className="px-4 py-2 text-right">This Week</th>
                  <th className="px-4 py-2 text-right">Total Meetings</th>
                  <th className="px-4 py-2 text-right">Cost/Mo</th>
                  <th className="px-4 py-2 text-left">Last Active</th>
                  <th className="px-4 py-2 text-left">Integrations</th>
                  <th className="px-4 py-2 text-center">Risk</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-50">
                {(customers || []).map((row) => (
                  <tr
                    key={row.team_id}
                    className="hover:bg-zinc-50 transition-colors cursor-pointer"
                    onClick={() => router.push(`/dashboard/customers/${row.team_id}`)}
                  >
                    <td className="px-4 py-2.5 font-medium text-zinc-900">{row.team_name}</td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.member_count}</td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.meetings_this_week}</td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">{row.total_meetings}</td>
                    <td className="px-4 py-2.5 text-right text-zinc-500 text-xs">
                      {row.total_cost_this_month > 0 ? `$${row.total_cost_this_month.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-zinc-400">
                      {row.last_meeting_at
                        ? new Date(row.last_meeting_at).toLocaleDateString()
                        : "Never"}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex gap-1 flex-wrap">
                        {(row.integrations || [] as string[]).map((i: string) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 bg-zinc-100 text-zinc-500 rounded text-[10px] font-mono"
                          >
                            {i}
                          </span>
                        ))}
                        {(!row.integrations || row.integrations.length === 0) && (
                          <span className="text-xs text-zinc-300">—</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <RiskBadge risk={row.churn_risk} />
                    </td>
                  </tr>
                ))}
                {(customers || []).length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-sm text-zinc-400">
                      No customers yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
