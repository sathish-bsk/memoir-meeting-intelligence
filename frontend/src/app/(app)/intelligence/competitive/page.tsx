"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { CompetitorAggregation } from "@/lib/types";
import DeepDiveLayout, { type DeepDiveItem } from "../components/deep-dive-layout";

export default function CompetitivePage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [items, setItems] = useState<DeepDiveItem[]>([]);
  const [totalCalls, setTotalCalls] = useState(0);
  const [loading, setLoading] = useState(true);
  const fetchedRef = useRef(false);

  // Role guard — member redirects to /intelligence
  useEffect(() => {
    if (user?.team_role === "member") {
      router.replace("/intelligence");
    }
  }, [user, router]);

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
        api.getCompetitors(activeTeamId, 365),
        api.getIntelligenceOverview(activeTeamId, 365),
      ]);
      setTotalCalls(overview.call_count);
      setItems(
        data.map((c: CompetitorAggregation) => ({
          label: c.competitor,
          count: c.mention_count,
          mentions: c.mentions,
          call_ids: c.call_ids,
          extra: {
            sentiment_breakdown: c.sentiment_breakdown,
            likes: c.likes,
            dislikes: c.dislikes,
          },
        })),
      );
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  if (user?.team_role === "member") return null;

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
      title="Competitive Intel"
      pageType="competitors"
      teamId={activeTeamId || undefined}
      items={items}
      totalCalls={totalCalls}
      emptyMessage="No competitors mentioned in your calls yet."
      renderExtra={(item) => {
        const sentiment = item.extra?.sentiment_breakdown as Record<string, number> | undefined;
        const likes = (item.extra?.likes as string[]) || [];
        const dislikes = (item.extra?.dislikes as string[]) || [];

        return (
          <div className="space-y-3">
            {/* Sentiment badges */}
            {sentiment && Object.keys(sentiment).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(sentiment).map(([k, v]) => (
                  <span
                    key={k}
                    className={`text-xs px-2 py-0.5 rounded-full ${
                      k === "positive"
                        ? "bg-emerald-50 text-emerald-600"
                        : k === "negative"
                          ? "bg-red-50 text-red-500"
                          : "bg-memoir-bg text-memoir-text-muted"
                    }`}
                  >
                    {k}: {v}
                  </span>
                ))}
              </div>
            )}

            {/* Likes / Dislikes */}
            {(likes.length > 0 || dislikes.length > 0) && (
              <div className="grid grid-cols-1 gap-2">
                {likes.filter(Boolean).length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-emerald-600 mb-1">What they like</p>
                    {likes.filter(Boolean).slice(0, 3).map((l, li) => (
                      <p key={li} className="text-xs text-memoir-text-secondary">+ {l}</p>
                    ))}
                  </div>
                )}
                {dislikes.filter(Boolean).length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-red-500 mb-1">What they dislike</p>
                    {dislikes.filter(Boolean).slice(0, 3).map((d, di) => (
                      <p key={di} className="text-xs text-memoir-text-secondary">- {d}</p>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      }}
    />
  );
}
