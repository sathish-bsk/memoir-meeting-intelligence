"use client";

import { useState, useEffect, useRef } from "react";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { QuestionAggregation } from "@/lib/types";
import DeepDiveLayout, { type DeepDiveItem } from "../components/deep-dive-layout";

type FaqTab = "raw" | "grouped";

export default function FAQPage() {
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const [items, setItems] = useState<DeepDiveItem[]>([]);
  const [groups, setGroups] = useState<Record<string, unknown>[]>([]);
  const [groupsUpdated, setGroupsUpdated] = useState<string | null>(null);
  const [totalCalls, setTotalCalls] = useState(0);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<FaqTab>("raw");
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
      const [questionsData, faqData, overview] = await Promise.all([
        api.getQuestions(activeTeamId, 365, 50),
        api.getFaqGroups(activeTeamId),
        api.getIntelligenceOverview(activeTeamId, 365),
      ]);
      setTotalCalls(overview.call_count);
      setItems(
        questionsData.map((q: QuestionAggregation) => ({
          label: q.question,
          count: q.ask_count,
          mentions: q.mentions,
          call_ids: q.call_ids,
          extra: {
            categories: q.categories,
            has_unanswered: q.has_unanswered,
          },
        })),
      );
      setGroups(faqData.groups || []);
      setGroupsUpdated(faqData.last_updated);
    } catch {
      setItems([]);
      setGroups([]);
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
    <div>
      {/* Tabs */}
      <div className="max-w-4xl mx-auto flex items-center gap-1 mb-4 border-b border-memoir-border">
        {([
          { key: "raw" as FaqTab, label: "All Questions" },
          { key: "grouped" as FaqTab, label: `Grouped${groups.length > 0 ? ` (${groups.length})` : ""}` },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              activeTab === tab.key
                ? "border-memoir-indigo text-memoir-indigo"
                : "border-transparent text-memoir-text-secondary hover:text-memoir-text hover:border-memoir-border"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "raw" ? (
        <DeepDiveLayout
          title="FAQ + Materials"
          pageType="questions"
          teamId={activeTeamId || undefined}
          items={items}
          totalCalls={totalCalls}
          emptyMessage="No prospect questions captured yet."
          renderExtra={(item) => {
            const categories = (item.extra?.categories as string[]) || [];
            const hasUnanswered = item.extra?.has_unanswered as boolean;
            return (
              <div className="space-y-2">
                {categories.filter(Boolean).length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {categories.filter(Boolean).slice(0, 3).map((cat, ci) => (
                      <span key={ci} className="text-xs bg-memoir-bg text-memoir-text-muted px-2 py-0.5 rounded-full">
                        {cat}
                      </span>
                    ))}
                  </div>
                )}
                {hasUnanswered && (
                  <span className="inline-block text-xs text-red-500 bg-red-50 px-2 py-0.5 rounded-full">
                    Sometimes unanswered
                  </span>
                )}
              </div>
            );
          }}
        />
      ) : (
        /* Grouped tab */
        <div className="max-w-4xl mx-auto">
          {groups.length === 0 ? (
            <div className="bg-memoir-surface border border-memoir-border rounded-card p-8 text-center">
              <p className="text-sm text-memoir-text-muted">
                No grouped FAQ data yet. Generate a digest to create AI-grouped questions.
              </p>
              {groupsUpdated && (
                <p className="text-xs text-memoir-text-muted mt-2">
                  Last updated: {new Date(groupsUpdated).toLocaleString()}
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {groupsUpdated && (
                <p className="text-xs text-memoir-text-muted">
                  Last grouped: {new Date(groupsUpdated).toLocaleString()}
                </p>
              )}
              {groups.map((group, i) => {
                const g = group as {
                  canonical_question?: string;
                  variations?: string[];
                  total_asks?: number;
                  primary_category?: string;
                  unanswered_count?: number;
                };
                return (
                  <div key={i} className="bg-memoir-surface border border-memoir-border rounded-card p-5">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-semibold text-memoir-text">
                        {g.canonical_question || "Unknown"}
                      </span>
                      <div className="flex items-center gap-2">
                        {g.primary_category && (
                          <span className="text-[10px] bg-memoir-bg text-memoir-text-muted px-2 py-0.5 rounded-full">
                            {g.primary_category}
                          </span>
                        )}
                        <span className="text-xs text-memoir-text-muted">
                          {g.total_asks || 0}x asked
                        </span>
                      </div>
                    </div>
                    {g.variations && g.variations.length > 0 && (
                      <div className="mt-2">
                        <p className="text-xs font-medium text-memoir-text-muted mb-1">Variations:</p>
                        {g.variations.map((v, vi) => (
                          <p
                            key={vi}
                            className="text-xs text-memoir-text-secondary italic pl-3 border-l-2 border-memoir-border"
                          >
                            {v}
                          </p>
                        ))}
                      </div>
                    )}
                    {(g.unanswered_count || 0) > 0 && (
                      <p className="text-xs text-red-500 mt-2">
                        {g.unanswered_count} times unanswered
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
