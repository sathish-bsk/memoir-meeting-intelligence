"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { api } from "@/lib/api";

// ─── Helpers ────────────────────────────────────────────────────

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number): string {
  const m = Math.round(seconds / 60);
  return `${m} min`;
}

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const SPEAKER_COLORS = [
  "#4f46e5", "#059669", "#7c3aed", "#d97706",
  "#e11d48", "#0891b2", "#ea580c", "#2563eb",
];

function getSpeakerColor(speaker: string, map: Map<string, number>): string {
  if (!map.has(speaker)) map.set(speaker, map.size);
  return SPEAKER_COLORS[map.get(speaker)! % SPEAKER_COLORS.length];
}

// ─── Types ──────────────────────────────────────────────────────

interface SharedCallData {
  title: string;
  date: string | null;
  duration_seconds: number | null;
  source: string | null;
  views: number;
  narrative_summary: string | null;
  call_metadata: {
    call_type?: string;
    participants?: Array<{ name: string; role: string; title?: string; company?: string }>;
  } | null;
  next_steps: { next_meeting?: string; next_milestone?: string; suggested_deal_stage?: string } | null;
  action_items: Array<{ text: string; type: string }> | null;
  signals: { buying: string[]; risk: string[] } | null;
  discovery_fields: Array<{ name: string; value: string; confidence: string; speaker?: string; quote?: string }> | null;
  coaching_metrics: Record<string, unknown> | null;
  disclaimer: string | null;
  overall_confidence_score: number | null;
  transcript: {
    segments: Array<{ speaker?: string; text: string; start: number; original_start?: number }>;
    speaker_map: Record<string, { name: string }>;
  };
}

// ─── Page ───────────────────────────────────────────────────────

export default function SharedCallPage() {
  const params = useParams();
  const token = params.token as string;

  const [data, setData] = useState<SharedCallData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getSharedCall(token)
      .then((result) => { if (!cancelled) setData(result); })
      .catch((err) => { if (!cancelled) setError(err.message || "Shared call not found"); })
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
          <p className="text-sm text-gray-500">{error || "This shared call link is invalid or has been revoked."}</p>
        </div>
      </div>
    );
  }

  const speakerColorMap = new Map<string, number>();
  const repActions = data.action_items?.filter((a) => a.type === "rep") || [];
  const prospectActions = data.action_items?.filter((a) => a.type === "prospect") || [];
  const buyingSignals = data.signals?.buying || [];
  const riskSignals = data.signals?.risk || [];
  const cm = data.coaching_metrics;
  const talkRatio = cm?.talk_ratio as { rep_percentage: number; customer_percentage: number } | undefined;
  const questionCount = cm?.question_count as { rep: number; customer: number } | undefined;
  const fillerCount = cm?.filler_word_count as { rep: number; customer: number } | undefined;

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
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">
              Shared Call
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

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        {/* Title + metadata */}
        <div>
          <h1 className="text-2xl font-bold text-gray-900 font-[var(--font-display)]">{data.title}</h1>
          <div className="flex items-center gap-2 mt-2 flex-wrap text-xs text-gray-500">
            {data.date && <span>{formatDate(data.date)}</span>}
            {data.duration_seconds != null && (
              <>
                <span className="text-gray-300">&middot;</span>
                <span>{formatDuration(data.duration_seconds)}</span>
              </>
            )}
            {data.source && (
              <>
                <span className="text-gray-300">&middot;</span>
                <span className="capitalize">{data.source === "zoom_cloud" ? "Zoom Cloud" : data.source}</span>
              </>
            )}
            {data.call_metadata?.call_type && data.call_metadata.call_type !== "Other" && data.call_metadata.call_type !== "null" && (
              <>
                <span className="text-gray-300">&middot;</span>
                <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[11px] font-medium">{data.call_metadata.call_type}</span>
              </>
            )}
            {data.overall_confidence_score != null && (
              <>
                <span className="text-gray-300">&middot;</span>
                <span>Confidence: {data.overall_confidence_score}%</span>
              </>
            )}
          </div>
        </div>

        {/* Summary */}
        {data.narrative_summary && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">Summary</h2>
            <div className="text-sm text-gray-600 leading-relaxed space-y-3">
              {data.narrative_summary.split("\n\n").map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </section>
        )}

        {/* Action Items */}
        {(repActions.length > 0 || prospectActions.length > 0) && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Action Items</h2>
            {repActions.length > 0 && (
              <div className="mb-4">
                <p className="text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-2">Your To-dos</p>
                <ul className="space-y-1.5">
                  {repActions.map((a, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
                      <span className="mt-1 w-3.5 h-3.5 rounded border border-gray-300 flex-shrink-0" />
                      {a.text}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {prospectActions.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-2">Their Commitments</p>
                <ul className="space-y-1.5">
                  {prospectActions.map((a, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
                      <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-gray-400 flex-shrink-0" />
                      {a.text}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {/* Signals */}
        {(buyingSignals.length > 0 || riskSignals.length > 0) && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Signals</h2>
            <div className="flex flex-col sm:flex-row gap-4">
              {buyingSignals.length > 0 && (
                <div className="flex-1">
                  <p className="text-[11px] font-medium text-emerald-600 uppercase tracking-wider mb-2">Buying Signals</p>
                  <div className="flex flex-wrap gap-1.5">
                    {buyingSignals.map((s, i) => (
                      <span key={i} className="inline-flex items-center px-2 py-1 rounded-full text-xs bg-emerald-50 text-emerald-700 border border-emerald-200">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {riskSignals.length > 0 && (
                <div className="flex-1">
                  <p className="text-[11px] font-medium text-red-600 uppercase tracking-wider mb-2">Risk Signals</p>
                  <div className="flex flex-wrap gap-1.5">
                    {riskSignals.map((s, i) => (
                      <span key={i} className="inline-flex items-center px-2 py-1 rounded-full text-xs bg-red-50 text-red-700 border border-red-200">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Discovery Fields */}
        {data.discovery_fields && data.discovery_fields.length > 0 && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Discovery Fields</h2>
            <div className="space-y-3">
              {data.discovery_fields.map((field, i) => (
                <div key={i} className="border border-gray-100 rounded-lg p-3 bg-gray-50/50">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] font-medium text-gray-400 uppercase tracking-wider">{field.name}</span>
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                      field.confidence === "definitive" ? "bg-emerald-100 text-emerald-700" :
                      field.confidence === "indicative" ? "bg-amber-100 text-amber-700" :
                      "bg-blue-100 text-blue-700"
                    }`}>{field.confidence}</span>
                  </div>
                  <p className="text-sm font-medium text-gray-900">{field.value}</p>
                  {field.quote && (
                    <p className="text-xs text-gray-400 italic mt-1">&quot;{field.quote}&quot;</p>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Coaching Metrics */}
        {cm && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Coaching Metrics</h2>
            {talkRatio && (
              <div className="mb-4">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>You {Math.round(talkRatio.rep_percentage)}%</span>
                  <span>Them {Math.round(talkRatio.customer_percentage)}%</span>
                </div>
                <div className="flex h-2 rounded-full overflow-hidden bg-gray-200">
                  <div className="bg-indigo-500 rounded-l-full" style={{ width: `${talkRatio.rep_percentage}%` }} />
                  <div className="bg-emerald-400 rounded-r-full" style={{ width: `${talkRatio.customer_percentage}%` }} />
                </div>
              </div>
            )}
            <div className="space-y-2">
              {questionCount && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Questions asked</span>
                  <span className="font-medium text-gray-900">You: {questionCount.rep} &middot; Them: {questionCount.customer}</span>
                </div>
              )}
              {fillerCount && (fillerCount.rep > 0 || fillerCount.customer > 0) && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Filler words</span>
                  <span className="font-medium text-gray-900">You: {fillerCount.rep} &middot; Them: {fillerCount.customer}</span>
                </div>
              )}
              {cm.longest_monologue_seconds != null && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Longest monologue</span>
                  <span className="font-medium text-gray-900">{formatSeconds(cm.longest_monologue_seconds as number)}</span>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Transcript */}
        {data.transcript.segments.length > 0 && (
          <section className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Transcript</h2>
            <div className="space-y-2 max-h-[600px] overflow-y-auto pr-2">
              {data.transcript.segments.map((seg, i) => {
                const rawSpeaker = seg.speaker || "Speaker";
                const resolvedSpeaker = data.transcript.speaker_map?.[rawSpeaker]?.name || rawSpeaker;
                const color = getSpeakerColor(resolvedSpeaker, speakerColorMap);
                const time = seg.original_start != null ? seg.original_start : seg.start;
                return (
                  <div key={i} className="flex gap-3 text-sm">
                    <span className="text-[11px] font-mono text-gray-400 w-10 flex-shrink-0 pt-0.5">
                      {formatSeconds(time)}
                    </span>
                    <div className="flex-1 min-w-0">
                      <span className="font-semibold" style={{ color }}>[{resolvedSpeaker}]</span>{" "}
                      <span className="text-gray-600">{seg.text}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Disclaimer */}
        {data.disclaimer && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
            <p className="text-xs font-medium text-amber-700 mb-1">Disclaimer</p>
            <p className="text-xs text-amber-600 leading-relaxed">{data.disclaimer}</p>
          </div>
        )}

        {/* Footer */}
        <footer className="text-center py-8 border-t border-gray-200">
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
