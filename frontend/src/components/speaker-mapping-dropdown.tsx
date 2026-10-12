"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { SpeakerProfile } from "@/lib/types";

interface Props {
  meetingId: string;
  teamId: string | undefined;
  diarizationLabel: string | undefined | null;
  currentName: string;
  onAssigned: () => void;
}

export function SpeakerMappingDropdown({
  meetingId,
  teamId,
  diarizationLabel,
  currentName,
  onAssigned,
}: Props) {
  const [open, setOpen] = useState(false);
  const [profiles, setProfiles] = useState<SpeakerProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyProfileId, setBusyProfileId] = useState<string | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open || !teamId) return;
    let cancelled = false;
    setLoading(true);
    api
      .getTeamSpeakers(teamId, query || undefined)
      .then((rows) => {
        if (!cancelled) setProfiles(rows ?? []);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message ?? "Failed to load profiles");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, teamId, query]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!popoverRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const assign = async (profile: SpeakerProfile) => {
    if (!diarizationLabel) {
      setError("This speaker has no diarization label — cannot reassign.");
      return;
    }
    setBusyProfileId(profile.id);
    setError(null);
    try {
      await api.assignSpeaker(meetingId, {
        diarization_label: diarizationLabel,
        speaker_name: profile.display_name,
        speaker_profile_id: profile.id,
      });
      setOpen(false);
      onAssigned();
    } catch (e) {
      setError((e as Error)?.message ?? "Assignment failed");
    } finally {
      setBusyProfileId(null);
    }
  };

  if (!teamId) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-label="Reassign speaker"
        className="rounded p-1 text-ink-secondary hover:bg-surface-soft hover:text-ink"
        title="Map to a known speaker"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="5" cy="12" r="1.5" fill="currentColor" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" />
          <circle cx="19" cy="12" r="1.5" fill="currentColor" />
        </svg>
      </button>
      {open && (
        <div
          ref={popoverRef}
          className="absolute right-0 top-7 z-50 w-64 rounded-md border border-surface-border bg-surface shadow-lg"
        >
          <div className="border-b border-surface-border p-2">
            <div className="mb-1 text-[10px] font-extrabold uppercase tracking-wider text-ink-secondary">
              Map &ldquo;{currentName}&rdquo; to
            </div>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search team speakers…"
              autoFocus
              className="w-full rounded-sm border border-surface-border bg-surface-soft px-2 py-1 text-xs outline-none focus:border-brand"
            />
          </div>
          <div className="max-h-64 overflow-y-auto">
            {loading && <div className="p-2 text-xs text-ink-secondary">Loading…</div>}
            {!loading && profiles.length === 0 && (
              <div className="p-2 text-xs text-ink-secondary">
                No matching speakers. Existing speakers in this meeting are auto-mapped; this list shows team-wide voice profiles for cross-meeting reuse.
              </div>
            )}
            {profiles.map((p) => (
              <button
                type="button"
                key={p.id}
                onClick={() => assign(p)}
                disabled={busyProfileId === p.id}
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs hover:bg-surface-soft disabled:opacity-50"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold text-ink">{p.display_name}</div>
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
          {error && (
            <div className="border-t border-surface-border p-2 text-[11px] text-red-600">
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
