"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { MentionDetail } from "@/lib/types";

export interface DeepDiveItem {
  label: string;
  count: number;
  mentions: MentionDetail[];
  call_ids: string[];
  extra?: Record<string, unknown>;
}

interface DeepDiveLayoutProps {
  title: string;
  backLabel?: string;
  backLink?: string;
  items: DeepDiveItem[];
  totalCalls: number;
  emptyMessage: string;
  renderExtra?: (item: DeepDiveItem) => React.ReactNode;
  pageType?: string;
  teamId?: string;
}

function timestampToSeconds(ts: string | null): number | null {
  if (!ts) return null;
  const parts = ts.split(":");
  if (parts.length === 2) {
    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
  }
  if (parts.length === 3) {
    return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
  }
  return null;
}

function PercentageBar({ count, total }: { count: number; total: number }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <div className="flex-1 h-2 bg-memoir-bg rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-memoir-indigo"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-sm font-semibold text-memoir-text w-12 text-right">{pct}%</span>
      </div>
      <p className="text-xs text-memoir-text-muted">
        {count} of {total} call{total !== 1 ? "s" : ""}
      </p>
    </div>
  );
}

function exportDeepDiveCSV(title: string, items: DeepDiveItem[]) {
  const rows: string[][] = [["Rank", "Item", "Mentions", "Quotes", "Speakers", "Companies"]];
  items.forEach((item, i) => {
    const quotes = item.mentions.map((m) => m.exact_quote).filter(Boolean).join("; ");
    const speakers = Array.from(new Set(item.mentions.map((m) => m.speaker_name).filter(Boolean))).join(", ");
    const companies = Array.from(new Set(item.mentions.map((m) => m.prospect_company).filter(Boolean))).join(", ");
    rows.push([String(i + 1), item.label, String(item.count), quotes, speakers, companies]);
  });

  const csv = rows.map((r) => r.map((c) => `"${(c || "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${title.toLowerCase().replace(/\s+/g, "-")}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function ShareButton({
  items,
  totalCalls,
  title,
  pageType,
  teamId,
}: {
  items: DeepDiveItem[];
  totalCalls: number;
  title: string;
  pageType?: string;
  teamId?: string;
}) {
  const [state, setState] = useState<"idle" | "generating" | "copied" | "error">("idle");

  const handleShare = async () => {
    if (!teamId || !pageType || items.length === 0) {
      // Fallback: copy current URL
      try {
        await navigator.clipboard.writeText(window.location.href);
        setState("copied");
        setTimeout(() => setState("idle"), 2000);
      } catch {
        // clipboard not available
      }
      return;
    }

    setState("generating");
    try {
      const result = await api.createIntelligenceShareLink({
        team_id: teamId,
        bucket_type: pageType,
        title,
        items: items.map((item) => ({
          label: item.label,
          count: item.count,
          mentions: item.mentions,
          call_ids: item.call_ids,
          extra: item.extra,
        })),
        total_calls: totalCalls,
      });

      const shareUrl = `${window.location.origin}${result.share_url}`;
      await navigator.clipboard.writeText(shareUrl);
      setState("copied");
      setTimeout(() => setState("idle"), 2000);
    } catch {
      // Fallback: copy current URL
      try {
        await navigator.clipboard.writeText(window.location.href);
        setState("copied");
        setTimeout(() => setState("idle"), 2000);
      } catch {
        setState("error");
        setTimeout(() => setState("idle"), 2000);
      }
    }
  };

  return (
    <button
      onClick={handleShare}
      disabled={state === "generating"}
      className="text-xs px-3 py-1.5 rounded-full border border-memoir-border bg-memoir-surface text-memoir-text-secondary hover:border-memoir-indigo/30 transition-colors flex items-center gap-1.5 disabled:opacity-50"
    >
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.935-2.186 2.25 2.25 0 0 0-3.935 2.186Z" />
      </svg>
      {state === "generating"
        ? "Generating..."
        : state === "copied"
          ? "Copied!"
          : state === "error"
            ? "Error"
            : "Share"}
    </button>
  );
}

export default function DeepDiveLayout({
  title,
  backLabel,
  backLink,
  items,
  totalCalls,
  emptyMessage,
  renderExtra,
  pageType,
  teamId,
}: DeepDiveLayoutProps) {
  const router = useRouter();
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push(backLink || "/intelligence")}
            className="text-memoir-text-muted hover:text-memoir-text transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
            </svg>
          </button>
          <div>
            <p className="text-xs text-memoir-text-muted">{backLabel || "Intelligence"}</p>
            <h1 className="text-xl font-bold text-memoir-text font-display">{title}</h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => exportDeepDiveCSV(title, items)}
            className="text-xs px-3 py-1.5 rounded-full border border-memoir-border bg-memoir-surface text-memoir-text-secondary hover:border-memoir-indigo/30 transition-colors"
          >
            Export CSV
          </button>
          <ShareButton
            items={items}
            totalCalls={totalCalls}
            title={title}
            pageType={pageType}
            teamId={teamId}
          />
        </div>
      </div>

      {/* Empty State */}
      {items.length === 0 && (
        <div className="bg-memoir-surface border border-memoir-border rounded-card p-12 text-center">
          <p className="text-sm text-memoir-text-muted">{emptyMessage}</p>
        </div>
      )}

      {/* Items */}
      <div className="space-y-4">
        {items.map((item, i) => {
          const isExpanded = expandedIdx === i;
          const uniqueCallIds = Array.from(new Set(item.call_ids));

          return (
            <div
              key={i}
              className="bg-memoir-surface border border-memoir-border rounded-card overflow-hidden"
            >
              {/* Item header */}
              <button
                onClick={() => setExpandedIdx(isExpanded ? null : i)}
                className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-memoir-bg/30 transition-colors"
              >
                <span className="text-sm font-semibold text-memoir-indigo w-8 shrink-0">
                  #{i + 1}
                </span>
                <span className="text-sm font-medium text-memoir-text flex-1">
                  {item.label}
                </span>
                <span className="text-xs text-memoir-text-muted shrink-0">
                  {item.count} mention{item.count !== 1 ? "s" : ""}
                </span>
                <svg
                  className={`w-4 h-4 text-memoir-text-muted transition-transform ${isExpanded ? "rotate-180" : ""}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                </svg>
              </button>

              {/* Expanded content — 2-column layout */}
              {isExpanded && (
                <div className="px-5 pb-5 grid grid-cols-1 md:grid-cols-5 gap-4 border-t border-memoir-border pt-4">
                  {/* Left column — quotes (3/5 width) */}
                  <div className="md:col-span-3 space-y-3">
                    <p className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider">
                      Quotes
                    </p>
                    {item.mentions.length > 0 ? (
                      <>
                        {item.mentions.slice(0, 5).map((mention, mi) => (
                          <QuoteCard key={mi} mention={mention} />
                        ))}
                        {item.mentions.length > 5 && (
                          <p className="text-xs text-memoir-text-muted mt-2">
                            Showing 5 of {item.mentions.length} quotes
                          </p>
                        )}
                      </>
                    ) : (
                      <p className="text-xs text-memoir-text-muted italic">No quotes available</p>
                    )}
                  </div>

                  {/* Right column — stats (2/5 width) */}
                  <div className="md:col-span-2 space-y-4">
                    <PercentageBar count={uniqueCallIds.length} total={totalCalls} />

                    {/* Extra metadata badges */}
                    {renderExtra && renderExtra(item)}

                    {/* View all calls link */}
                    {uniqueCallIds.length > 0 && (
                      <button
                        onClick={() => {
                          // Build timestamp map from mentions
                          const tsMap: Record<string, number> = {};
                          item.mentions.forEach((m) => {
                            if (m.meeting_id && m.timestamp_in_call && !tsMap[m.meeting_id]) {
                              const secs = timestampToSeconds(m.timestamp_in_call);
                              if (secs != null) tsMap[m.meeting_id] = secs;
                            }
                          });
                          const params = new URLSearchParams();
                          params.set("filter_ids", uniqueCallIds.join(","));
                          params.set("intel_label", title);
                          if (Object.keys(tsMap).length > 0) {
                            params.set("timestamps", JSON.stringify(tsMap));
                          }
                          router.push(`/meetings?${params.toString()}`);
                        }}
                        className="text-xs text-memoir-indigo hover:underline flex items-center gap-1"
                      >
                        View all {uniqueCallIds.length} call{uniqueCallIds.length !== 1 ? "s" : ""} &rarr;
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function QuoteCard({ mention }: { mention: MentionDetail }) {
  const router = useRouter();
  const seconds = timestampToSeconds(mention.timestamp_in_call);

  return (
    <div className="bg-memoir-bg/50 rounded-lg p-3">
      {mention.exact_quote && (
        <p className="text-sm text-memoir-text italic leading-relaxed mb-2">
          &ldquo;{mention.exact_quote}&rdquo;
        </p>
      )}
      <div className="flex items-center gap-2 text-xs text-memoir-text-muted">
        {mention.speaker_name && (
          <span className="font-medium text-memoir-text-secondary">
            — {mention.speaker_name}
            {mention.prospect_company && `, ${mention.prospect_company}`}
          </span>
        )}
        {!mention.speaker_name && mention.prospect_company && (
          <span className="font-medium text-memoir-text-secondary">
            — {mention.prospect_company}
          </span>
        )}
        {mention.timestamp_in_call && mention.meeting_id && (
          <button
            onClick={() =>
              router.push(`/meetings/${mention.meeting_id}${seconds != null ? `?t=${seconds}` : ""}`)
            }
            className="text-memoir-indigo hover:underline flex items-center gap-0.5"
          >
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
            {mention.timestamp_in_call}
          </button>
        )}
        {mention.call_date && (
          <span>{mention.call_date}</span>
        )}
      </div>
    </div>
  );
}
