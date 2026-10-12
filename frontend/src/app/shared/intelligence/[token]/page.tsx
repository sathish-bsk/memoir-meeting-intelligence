"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { api } from "@/lib/api";

// ─── Types ───────────────────────────────────────────────────────

interface MentionDetail {
  exact_quote?: string;
  speaker_name?: string;
  prospect_company?: string;
  timestamp_in_call?: string;
  call_date?: string;
  meeting_id?: string;
}

interface SharedItem {
  label: string;
  count: number;
  mentions: MentionDetail[];
  call_ids: string[];
  extra?: Record<string, unknown>;
}

interface SharedIntelligenceData {
  title: string;
  bucket_type: string | null;
  items: SharedItem[];
  total_calls: number;
  views: number;
  created_at: string | null;
}

// ─── Helpers ─────────────────────────────────────────────────────

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function timestampToSeconds(ts: string | null): number | null {
  if (!ts) return null;
  const parts = ts.split(":");
  if (parts.length === 2) return parseInt(parts[0]) * 60 + parseInt(parts[1]);
  if (parts.length === 3) return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
  return null;
}

const BUCKET_LABELS: Record<string, string> = {
  pain_points: "Voice of Customer",
  feature_requests: "Product & Feature Requests",
  competitors: "Competitive Intel",
  objections: "Deal Intelligence",
  questions: "FAQ + Materials",
};

// ─── Page ────────────────────────────────────────────────────────

export default function SharedIntelligencePage() {
  const params = useParams();
  const token = params.token as string;

  const [data, setData] = useState<SharedIntelligenceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getSharedIntelligence(token)
      .then((result: SharedIntelligenceData) => { if (!cancelled) setData(result); })
      .catch((err: { message?: string }) => { if (!cancelled) setError(err.message || "Shared intelligence not found"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [token]);

  if (loading) {
    return (
      <div className="min-h-screen bg-[#F8F7F4] flex items-center justify-center">
        <div className="animate-spin w-6 h-6 border-2 border-gray-300 border-t-indigo-600 rounded-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-[#F8F7F4] flex items-center justify-center px-4">
        <div className="bg-white border border-gray-200 rounded-xl p-8 max-w-sm w-full text-center shadow-sm">
          <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
            </svg>
          </div>
          <h1 className="text-lg font-bold text-gray-900 mb-2">Link Not Found</h1>
          <p className="text-sm text-gray-500">{error || "This shared intelligence link is invalid or has been revoked."}</p>
        </div>
      </div>
    );
  }

  const bucketLabel = data.bucket_type ? (BUCKET_LABELS[data.bucket_type] || data.bucket_type) : "Intelligence";

  return (
    <div className="min-h-screen bg-[#F8F7F4]">
      {/* Header */}
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Image src="/logo-icon.svg" alt="" width={28} height={28} className="rounded-md" />
            <Image src="/logo.svg" alt="Memoir" width={80} height={20} />
          </div>
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-violet-50 text-violet-700 border border-violet-200">
              Shared Intelligence
            </span>
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
              </svg>
              {data.views} {data.views === 1 ? "view" : "views"}
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8">
        {/* Title + metadata */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900 font-[var(--font-display)]">{data.title}</h1>
          <div className="flex items-center gap-2 mt-2 flex-wrap text-xs text-gray-500">
            <span className="px-1.5 py-0.5 bg-violet-50 text-violet-700 rounded text-[11px] font-medium">
              {bucketLabel}
            </span>
            <span className="text-gray-300">&middot;</span>
            <span>{data.total_calls} call{data.total_calls !== 1 ? "s" : ""} analyzed</span>
            {data.created_at && (
              <>
                <span className="text-gray-300">&middot;</span>
                <span>Shared {formatDate(data.created_at)}</span>
              </>
            )}
          </div>
        </div>

        {/* Items */}
        {data.items.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-xl p-12 text-center shadow-sm">
            <p className="text-sm text-gray-500">No data available.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {data.items.map((item, i) => {
              const isExpanded = expandedIdx === i;
              const uniqueCallIds = Array.from(new Set(item.call_ids || []));
              const pct = data.total_calls > 0 ? Math.round((uniqueCallIds.length / data.total_calls) * 100) : 0;

              return (
                <div
                  key={i}
                  className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm"
                >
                  {/* Item header */}
                  <button
                    onClick={() => setExpandedIdx(isExpanded ? null : i)}
                    className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-sm font-semibold text-indigo-600 w-8 shrink-0">
                      #{i + 1}
                    </span>
                    <span className="text-sm font-medium text-gray-900 flex-1">
                      {item.label}
                    </span>
                    <span className="text-xs text-gray-400 shrink-0">
                      {item.count} mention{item.count !== 1 ? "s" : ""}
                    </span>
                    <svg
                      className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? "rotate-180" : ""}`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                    </svg>
                  </button>

                  {/* Expanded content */}
                  {isExpanded && (
                    <div className="px-5 pb-5 grid grid-cols-1 md:grid-cols-5 gap-4 border-t border-gray-100 pt-4">
                      {/* Left column — quotes */}
                      <div className="md:col-span-3 space-y-3">
                        <p className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                          Quotes
                        </p>
                        {(item.mentions || []).length > 0 ? (
                          <>
                            {item.mentions.slice(0, 5).map((mention, mi) => (
                              <PublicQuoteCard key={mi} mention={mention} />
                            ))}
                            {item.mentions.length > 5 && (
                              <p className="text-xs text-gray-400 mt-2">
                                Showing 5 of {item.mentions.length} quotes
                              </p>
                            )}
                          </>
                        ) : (
                          <p className="text-xs text-gray-400 italic">No quotes available</p>
                        )}
                      </div>

                      {/* Right column — stats */}
                      <div className="md:col-span-2 space-y-4">
                        {/* Percentage bar */}
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full bg-indigo-500"
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <span className="text-sm font-semibold text-gray-900 w-12 text-right">{pct}%</span>
                          </div>
                          <p className="text-xs text-gray-400">
                            {uniqueCallIds.length} of {data.total_calls} call{data.total_calls !== 1 ? "s" : ""}
                          </p>
                        </div>

                        {/* Extra metadata */}
                        {item.extra && <ExtraBadges extra={item.extra} bucketType={data.bucket_type} />}

                        {/* View all calls link (requires login) */}
                        {uniqueCallIds.length > 0 && (
                          <a
                            href={`/meetings?filter_ids=${uniqueCallIds.join(",")}&intel_label=${encodeURIComponent(data.title)}`}
                            className="text-xs text-indigo-600 hover:underline flex items-center gap-1"
                          >
                            View all {uniqueCallIds.length} call{uniqueCallIds.length !== 1 ? "s" : ""} &rarr;
                            <svg className="w-3 h-3 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                            </svg>
                          </a>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Footer */}
        <footer className="text-center py-8 border-t border-gray-200 mt-8">
          <div className="flex items-center justify-center gap-1.5 mb-2">
            <Image src="/logo-icon.svg" alt="" width={20} height={20} className="rounded" />
            <span className="text-sm font-medium text-gray-500">Powered by Memoir</span>
          </div>
          <p className="text-xs text-gray-400">AI-powered conversation intelligence for sales teams</p>
        </footer>
      </main>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────

function PublicQuoteCard({ mention }: { mention: MentionDetail }) {
  const seconds = timestampToSeconds(mention.timestamp_in_call ?? null);
  const callDetailHref = mention.meeting_id
    ? `/meetings/${mention.meeting_id}${seconds != null ? `?t=${seconds}` : ""}`
    : null;

  return (
    <div className="bg-gray-50 rounded-lg p-3">
      {mention.exact_quote && (
        <p className="text-sm text-gray-700 italic leading-relaxed mb-2">
          &ldquo;{mention.exact_quote}&rdquo;
        </p>
      )}
      <div className="flex items-center gap-2 text-xs text-gray-400">
        {mention.speaker_name && (
          <span className="font-medium text-gray-500">
            — {mention.speaker_name}
            {mention.prospect_company && `, ${mention.prospect_company}`}
          </span>
        )}
        {!mention.speaker_name && mention.prospect_company && (
          <span className="font-medium text-gray-500">
            — {mention.prospect_company}
          </span>
        )}
        {mention.timestamp_in_call && callDetailHref && (
          <a
            href={callDetailHref}
            className="text-indigo-600 hover:underline flex items-center gap-0.5"
          >
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
            {mention.timestamp_in_call}
          </a>
        )}
        {mention.timestamp_in_call && !callDetailHref && (
          <span className="text-gray-400 flex items-center gap-0.5">
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
            {mention.timestamp_in_call}
          </span>
        )}
        {mention.call_date && (
          <span>{mention.call_date}</span>
        )}
      </div>
    </div>
  );
}

function ExtraBadges({
  extra,
  bucketType,
}: {
  extra: Record<string, unknown>;
  bucketType: string | null;
}) {
  if (!bucketType) return null;

  // Pain points: avg_severity
  if (bucketType === "pain_points" && extra.avg_severity != null) {
    const sev = extra.avg_severity as number;
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">Avg severity:</span>
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
            sev >= 4 ? "bg-red-50 text-red-600" : sev >= 2.5 ? "bg-amber-50 text-amber-600" : "bg-emerald-50 text-emerald-600"
          }`}
        >
          {sev.toFixed(1)}/5
        </span>
      </div>
    );
  }

  // Feature requests: urgency_breakdown
  if (bucketType === "feature_requests" && extra.urgency_breakdown) {
    const breakdown = extra.urgency_breakdown as Record<string, number>;
    if (Object.keys(breakdown).length === 0) return null;
    return (
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(breakdown).map(([k, v]) => (
          <span
            key={k}
            className={`text-xs px-2 py-0.5 rounded-full ${
              k === "high" || k === "critical" ? "bg-red-50 text-red-600" : k === "medium" ? "bg-amber-50 text-amber-600" : "bg-gray-100 text-gray-500"
            }`}
          >
            {k}: {v}
          </span>
        ))}
      </div>
    );
  }

  // Competitors: sentiment_breakdown + likes/dislikes
  if (bucketType === "competitors") {
    const sentiment = extra.sentiment_breakdown as Record<string, number> | undefined;
    const likes = (extra.likes as string[]) || [];
    const dislikes = (extra.dislikes as string[]) || [];

    return (
      <div className="space-y-3">
        {sentiment && Object.keys(sentiment).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(sentiment).map(([k, v]) => (
              <span
                key={k}
                className={`text-xs px-2 py-0.5 rounded-full ${
                  k === "positive" ? "bg-emerald-50 text-emerald-600" : k === "negative" ? "bg-red-50 text-red-500" : "bg-gray-100 text-gray-500"
                }`}
              >
                {k}: {v}
              </span>
            ))}
          </div>
        )}
        {(likes.filter(Boolean).length > 0 || dislikes.filter(Boolean).length > 0) && (
          <div className="grid grid-cols-1 gap-2">
            {likes.filter(Boolean).length > 0 && (
              <div>
                <p className="text-xs font-medium text-emerald-600 mb-1">What they like</p>
                {likes.filter(Boolean).slice(0, 3).map((l, li) => (
                  <p key={li} className="text-xs text-gray-500">+ {l}</p>
                ))}
              </div>
            )}
            {dislikes.filter(Boolean).length > 0 && (
              <div>
                <p className="text-xs font-medium text-red-500 mb-1">What they dislike</p>
                {dislikes.filter(Boolean).slice(0, 3).map((d, di) => (
                  <p key={di} className="text-xs text-gray-500">- {d}</p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // Objections: category + resolution_rate
  if (bucketType === "objections") {
    const category = extra.category as string | undefined;
    const resolution = extra.resolution_rate as number | undefined;
    return (
      <div className="space-y-2">
        {category && (
          <span className="inline-block text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
            {category}
          </span>
        )}
        {resolution != null && (
          <div>
            <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
              <span>Resolution rate</span>
              <span className="font-semibold text-gray-900">{Math.round(resolution * 100)}%</span>
            </div>
            <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-emerald-400"
                style={{ width: `${resolution * 100}%` }}
              />
            </div>
          </div>
        )}
      </div>
    );
  }

  // Questions: categories + has_unanswered
  if (bucketType === "questions") {
    const categories = (extra.categories as string[]) || [];
    const hasUnanswered = extra.has_unanswered as boolean;
    return (
      <div className="space-y-2">
        {categories.filter(Boolean).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {categories.filter(Boolean).slice(0, 3).map((cat, ci) => (
              <span key={ci} className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
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
  }

  return null;
}
