"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { SpeakerProfile } from "@/lib/types";

interface Props {
  sourceProfileId: string;
  sourceName: string;
  teamId: string | undefined;
  onClose: () => void;
  onMerged: () => void;
}

interface DryRunReport {
  source_id: string;
  source_name: string;
  target_id: string;
  target_name: string;
  participants_to_move: number;
  samples_to_move: number;
  talk_seconds_to_add: number;
  meeting_count_to_add: number;
}

export function SpeakerMergeDialog({
  sourceProfileId,
  sourceName,
  teamId,
  onClose,
  onMerged,
}: Props) {
  const [profiles, setProfiles] = useState<SpeakerProfile[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [target, setTarget] = useState<SpeakerProfile | null>(null);
  const [report, setReport] = useState<DryRunReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!teamId) return;
    let cancelled = false;
    setLoading(true);
    api
      .getTeamSpeakers(teamId, query || undefined)
      .then((rows) => {
        if (!cancelled) {
          // Hide the source profile from its own merge target list.
          setProfiles((rows ?? []).filter((p) => p.id !== sourceProfileId));
        }
      })
      .catch((e) => !cancelled && setError(e?.message ?? "Failed to load profiles"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [teamId, query, sourceProfileId]);

  // Run a dry-run when target picked so user can verify before commit.
  useEffect(() => {
    if (!target) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api
      .mergeSpeakerProfile(sourceProfileId, target.id, /*dryRun=*/ true)
      .then((r) => !cancelled && setReport(r))
      .catch((e) => !cancelled && setError(e?.message ?? "Dry-run failed"));
    return () => {
      cancelled = true;
    };
  }, [target, sourceProfileId]);

  const commit = async () => {
    if (!target) return;
    setMerging(true);
    setError(null);
    try {
      await api.mergeSpeakerProfile(sourceProfileId, target.id, false);
      onMerged();
      onClose();
    } catch (e) {
      setError((e as Error)?.message ?? "Merge failed");
    } finally {
      setMerging(false);
    }
  };

  // Close on backdrop click / Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={(e) => {
        if (!dialogRef.current?.contains(e.target as Node)) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="w-full max-w-md rounded-lg border border-surface-border bg-surface shadow-xl"
      >
        <div className="border-b border-surface-border px-4 py-3">
          <div className="text-[11px] font-extrabold uppercase tracking-wider text-ink-secondary">
            Merge contact
          </div>
          <div className="mt-0.5 text-sm font-extrabold text-ink">
            &ldquo;{sourceName}&rdquo; into another contact
          </div>
          <div className="mt-0.5 text-[11px] text-ink-secondary">
            All meetings, voice samples, and talk-time move to the target.
            The source profile is deleted. This cannot be undone.
          </div>
        </div>

        {!target && (
          <>
            <div className="border-b border-surface-border p-3">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search target contact…"
                autoFocus
                className="w-full rounded-sm border border-surface-border bg-surface-soft px-2 py-1.5 text-sm outline-none focus:border-brand"
              />
            </div>
            <div className="max-h-72 overflow-y-auto">
              {loading && <div className="p-3 text-xs text-ink-secondary">Loading…</div>}
              {!loading && profiles.length === 0 && (
                <div className="p-3 text-xs text-ink-secondary">
                  No other contacts in this team.
                </div>
              )}
              {profiles.map((p) => (
                <button
                  type="button"
                  key={p.id}
                  onClick={() => setTarget(p)}
                  className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left hover:bg-surface-soft"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-ink">{p.display_name}</div>
                    {(p.email || p.company) && (
                      <div className="truncate text-[10px] text-ink-secondary">
                        {[p.email, p.company].filter(Boolean).join(" · ")}
                      </div>
                    )}
                  </div>
                  <div className="text-[10px] text-ink-secondary">
                    {p.meeting_count != null ? `${p.meeting_count} mtgs` : ""}
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {target && (
          <div className="px-4 py-3">
            <div className="rounded-md border border-surface-border bg-surface-soft p-3">
              <div className="text-[10px] font-extrabold uppercase tracking-wider text-ink-secondary">
                Target
              </div>
              <div className="text-sm font-extrabold text-ink">{target.display_name}</div>
              {(target.email || target.company) && (
                <div className="text-[11px] text-ink-secondary">
                  {[target.email, target.company].filter(Boolean).join(" · ")}
                </div>
              )}
            </div>
            {!report && !error && (
              <div className="mt-3 text-xs text-ink-secondary">Computing merge…</div>
            )}
            {report && (
              <div className="mt-3 space-y-1 text-xs text-ink">
                <div>
                  <span className="font-extrabold">{report.participants_to_move}</span> meeting
                  participation row(s) will move
                </div>
                <div>
                  <span className="font-extrabold">{report.samples_to_move}</span> voice
                  embedding sample(s) will move
                </div>
                <div>
                  +<span className="font-extrabold">{Math.round(report.talk_seconds_to_add)}s</span>{" "}
                  talk time, +<span className="font-extrabold">{report.meeting_count_to_add}</span>{" "}
                  meetings added to target
                </div>
              </div>
            )}
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setTarget(null);
                  setReport(null);
                }}
                disabled={merging}
                className="rounded-md border border-surface-border bg-surface px-3 py-1.5 text-xs font-extrabold text-ink-secondary hover:bg-surface-soft disabled:opacity-50"
              >
                Pick different target
              </button>
              <button
                type="button"
                onClick={commit}
                disabled={merging || !report}
                className="rounded-md bg-brand px-3 py-1.5 text-xs font-extrabold text-white hover:bg-brand/90 disabled:opacity-50"
              >
                {merging ? "Merging…" : "Confirm merge"}
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="border-t border-surface-border bg-red-50 px-4 py-2 text-[11px] text-red-700">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
