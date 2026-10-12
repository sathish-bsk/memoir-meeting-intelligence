"use client";

import { useState, useEffect, useRef } from "react";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { FeatureRequestAggregation } from "@/lib/types";
import DeepDiveLayout, { type DeepDiveItem } from "../components/deep-dive-layout";

export default function ProductFeaturesPage() {
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
        api.getFeatureRequests(activeTeamId, 365),
        api.getIntelligenceOverview(activeTeamId, 365),
      ]);
      setTotalCalls(overview.call_count);
      setItems(
        data.map((fr: FeatureRequestAggregation) => ({
          label: fr.feature,
          count: fr.request_count,
          mentions: fr.mentions,
          call_ids: fr.call_ids,
          extra: { urgency_breakdown: fr.urgency_breakdown },
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
      title="Product & Feature Requests"
      pageType="feature_requests"
      teamId={activeTeamId || undefined}
      items={items}
      totalCalls={totalCalls}
      emptyMessage="No feature requests detected yet."
      renderExtra={(item) => {
        const breakdown = item.extra?.urgency_breakdown as Record<string, number> | undefined;
        if (!breakdown || Object.keys(breakdown).length === 0) return null;
        return (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(breakdown).map(([k, v]) => (
              <span
                key={k}
                className={`text-xs px-2 py-0.5 rounded-full ${
                  k === "high" || k === "critical"
                    ? "bg-red-50 text-red-600"
                    : k === "medium"
                      ? "bg-amber-50 text-amber-600"
                      : "bg-memoir-bg text-memoir-text-muted"
                }`}
              >
                {k}: {v}
              </span>
            ))}
          </div>
        );
      }}
    />
  );
}
