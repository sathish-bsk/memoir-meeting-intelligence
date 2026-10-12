"use client";

import { useState, useEffect, useRef } from "react";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { PainPointAggregation } from "@/lib/types";
import DeepDiveLayout, { type DeepDiveItem } from "../components/deep-dive-layout";

export default function VoiceOfCustomerPage() {
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [items, setItems] = useState<DeepDiveItem[]>([]);
  const [totalCalls, setTotalCalls] = useState(0);
  const [loading, setLoading] = useState(true);
  const fetchedRef = useRef(false);

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
      const [data, overview] = await Promise.all([
        api.getPainPoints(activeTeamId, 365),
        api.getIntelligenceOverview(activeTeamId, 365),
      ]);
      setTotalCalls(overview.call_count);
      setItems(
        data.map((pp: PainPointAggregation) => ({
          label: pp.pain,
          count: pp.mention_count,
          mentions: pp.mentions,
          call_ids: pp.call_ids,
          extra: { avg_severity: pp.avg_severity },
        })),
      );
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="bg-memoir-surface border border-memoir-border rounded-card p-6 animate-pulse">
            <div className="h-4 bg-memoir-bg rounded w-1/3 mb-3" />
            <div className="h-3 bg-memoir-bg rounded w-2/3" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <DeepDiveLayout
      title="Voice of Customer"
      pageType="pain_points"
      teamId={activeTeamId || undefined}
      items={items}
      totalCalls={totalCalls}
      emptyMessage="No pain points extracted yet. This populates automatically as you record more calls."
      renderExtra={(item) =>
        item.extra?.avg_severity != null ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-memoir-text-muted">Avg severity:</span>
            <span
              className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                (item.extra.avg_severity as number) >= 4
                  ? "bg-red-50 text-red-600"
                  : (item.extra.avg_severity as number) >= 2.5
                    ? "bg-amber-50 text-amber-600"
                    : "bg-emerald-50 text-emerald-600"
              }`}
            >
              {(item.extra.avg_severity as number).toFixed(1)}/5
            </span>
          </div>
        ) : null
      }
    />
  );
}
