"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";

interface RawSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  avg_logprob?: number;
  no_speech_prob?: number;
  compression_ratio?: number;
  detected_language?: string;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

export default function RawAsrPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const id = params.id as string;
  // ?version=N pins to a specific transcription version; omitted = latest.
  // Caller (calls/[id]/page.tsx) passes selectedVersion so retranscribed
  // meetings show the right v(N) raw ASR instead of the old hardcoded v1.
  const versionParam = searchParams.get("version");
  const version = versionParam ? parseInt(versionParam, 10) : undefined;
  const [segments, setSegments] = useState<RawSegment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getRawAsr(id, version)
      .then((data) => {
        setSegments(Array.isArray(data) ? (data as unknown as RawSegment[]) : []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message || "Failed to load raw ASR output");
        setLoading(false);
      });
  }, [id, version]);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto p-8">
        <p className="text-memoir-text-muted">Loading raw ASR output...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto p-8">
        <p className="text-red-500">{error}</p>
      </div>
    );
  }

  const totalWords = segments.reduce(
    (sum, s) => sum + (s.text?.split(/\s+/).length || 0),
    0
  );

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-memoir-text">
          Raw ASR Output
        </h1>
        <p className="text-sm text-memoir-text-muted mt-1">
          Whisper output before alignment, speaker assignment, vocabulary
          correction, or punctuation refinement.
        </p>
        <p className="text-xs text-memoir-text-muted mt-1">
          {segments.length} segments &middot; {totalWords.toLocaleString()} words
        </p>
      </div>

      <div className="space-y-3">
        {segments.map((seg, i) => {
          const logprob = seg.avg_logprob ?? 0;
          const confidence =
            logprob > -0.2
              ? "text-green-600"
              : logprob > -0.5
              ? "text-memoir-text"
              : "text-orange-500";

          return (
            <div
              key={i}
              className="bg-memoir-surface border border-memoir-border rounded-lg p-3"
            >
              <div className="flex items-center gap-3 mb-1">
                <span className="text-xs font-mono text-memoir-indigo">
                  {formatTime(seg.start)}
                </span>
                <span className="text-[10px] text-memoir-text-muted">
                  {(seg.end - seg.start).toFixed(1)}s
                </span>
                {seg.detected_language && (
                  <span className="text-[10px] text-memoir-text-muted">
                    {seg.detected_language}
                  </span>
                )}
                <span
                  className={`text-[10px] font-mono ${confidence}`}
                  title={`avg_logprob: ${logprob.toFixed(3)}`}
                >
                  {logprob > -0.2
                    ? "high"
                    : logprob > -0.5
                    ? "med"
                    : "low"}
                </span>
              </div>
              <p className={`text-sm leading-relaxed ${confidence}`}>
                {seg.text}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
