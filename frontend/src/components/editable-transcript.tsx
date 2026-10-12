"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { TranscriptResponse, SpeakerProfile } from "@/lib/types";
import { EditedBadge } from "./edited-badge";
import { SaveStatusIndicator } from "./save-status";
import type { SaveStatus } from "@/hooks/use-auto-save";

const SPEAKER_COLORS = [
  "#4f46e5", "#059669", "#7c3aed", "#d97706",
  "#e11d48", "#0891b2", "#ea580c", "#2563eb",
];

function getSpeakerColor(speaker: string, map: Map<string, number>): string {
  if (!map.has(speaker)) map.set(speaker, map.size);
  return SPEAKER_COLORS[map.get(speaker)! % SPEAKER_COLORS.length];
}

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

interface EditableTranscriptProps {
  meetingId: string;
  transcript: TranscriptResponse;
  onSeek: (time: number) => void;
  onTranscriptUpdate: (t: TranscriptResponse) => void;
  // Speaker editing props (pass-through from parent)
  editingSpeaker: string | null;
  speakerInput: string;
  teamSpeakers: SpeakerProfile[];
  savingSpeaker: boolean;
  speakerInputRef: React.RefObject<HTMLInputElement>;
  onSpeakerClick: (speaker: string) => void;
  onSpeakerInputChange: (value: string) => void;
  onSpeakerSave: (original: string, newName: string, profileId?: string) => void;
  onSpeakerEditCancel: () => void;
  readOnly?: boolean;
}

export function EditableTranscript({
  meetingId,
  transcript,
  onSeek,
  onTranscriptUpdate,
  editingSpeaker,
  speakerInput,
  teamSpeakers,
  savingSpeaker,
  speakerInputRef,
  onSpeakerClick,
  onSpeakerInputChange,
  onSpeakerSave,
  onSpeakerEditCancel,
  readOnly = false,
}: EditableTranscriptProps) {
  const [editedSegments, setEditedSegments] = useState<Set<number>>(new Set());
  const [segmentStatuses, setSegmentStatuses] = useState<Map<number, SaveStatus>>(new Map());
  const [resetting, setResetting] = useState(false);
  const timerRefs = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const hasEdits = editedSegments.size > 0 || transcript.is_edited;

  const handleSegmentInput = useCallback(
    (index: number, newText: string) => {
      // Clear previous timer for this segment
      const prev = timerRefs.current.get(index);
      if (prev) clearTimeout(prev);

      setSegmentStatuses((s) => new Map(s).set(index, "saving"));

      const timer = setTimeout(async () => {
        try {
          await api.editTranscriptSegment(meetingId, index, newText);
          setEditedSegments((s) => new Set(s).add(index));
          setSegmentStatuses((s) => {
            const next = new Map(s);
            next.set(index, "saved");
            return next;
          });
          // Update parent state
          const updated = { ...transcript };
          updated.segments = [...updated.segments];
          updated.segments[index] = { ...updated.segments[index], text: newText };
          onTranscriptUpdate(updated);
          setTimeout(() => {
            setSegmentStatuses((s) => {
              const next = new Map(s);
              next.delete(index);
              return next;
            });
          }, 2000);
        } catch {
          setSegmentStatuses((s) => {
            const next = new Map(s);
            next.set(index, "error");
            return next;
          });
          setTimeout(() => {
            setSegmentStatuses((s) => {
              const next = new Map(s);
              next.delete(index);
              return next;
            });
          }, 3000);
        }
      }, 1500);

      timerRefs.current.set(index, timer);
    },
    [meetingId, transcript, onTranscriptUpdate],
  );

  const handleReset = async () => {
    setResetting(true);
    try {
      await api.resetTranscript(meetingId);
      // Reload transcript
      const t = await api.getTranscript(meetingId);
      onTranscriptUpdate(t);
      setEditedSegments(new Set());
      setSegmentStatuses(new Map());
    } catch {
      // silent
    } finally {
      setResetting(false);
    }
  };

  const speakerToLabel = new Map<string, string>();
  if (transcript.speaker_map) {
    for (const [label, entry] of Object.entries(transcript.speaker_map)) {
      speakerToLabel.set(entry.name, label);
    }
  }

  const speakerColorMap = new Map<string, number>();

  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-memoir-text">
            Transcript
            <span className="text-xs font-normal text-memoir-text-muted ml-2">
              {transcript.word_count.toLocaleString()} words &middot; {transcript.speaker_count} speaker{transcript.speaker_count !== 1 ? "s" : ""}
            </span>
          </h2>
          {hasEdits && <EditedBadge />}
        </div>
        <div className="flex items-center gap-2">
          {!readOnly && hasEdits && (
            <button
              onClick={handleReset}
              disabled={resetting}
              className="text-xs text-memoir-text-muted hover:text-memoir-indigo transition-colors disabled:opacity-50"
            >
              {resetting ? "Resetting\u2026" : "Reset to original"}
            </button>
          )}
        </div>
      </div>

      <div className="space-y-3">
        {transcript.segments.length === 0 ? (
          <p className="text-sm text-memoir-text-muted italic">No transcript segments.</p>
        ) : (
          transcript.segments.map((seg, i) => {
            const time = seg.original_start != null ? seg.original_start : seg.start;
            const rawLabel = seg.speaker || "Speaker";
            const mappedName = transcript.speaker_map?.[rawLabel]?.name;
            const speaker = mappedName || rawLabel;
            const color = getSpeakerColor(speaker, speakerColorMap);
            const isEditing = editingSpeaker === speaker || editingSpeaker === rawLabel;
            const isUnidentified = /^SPEAKER_\d+$/i.test(speaker);
            const isFirstOfSpeaker = i === transcript.segments.findIndex((s) => s.speaker === rawLabel);
            const isSegmentEdited = editedSegments.has(i);
            const segStatus = segmentStatuses.get(i);

            return (
              <div key={i} className="flex gap-3 group">
                <button
                  onClick={() => onSeek(time)}
                  className="text-xs text-memoir-text-muted font-mono w-12 flex-shrink-0 pt-0.5 hover:text-memoir-indigo transition-colors text-left"
                >
                  {formatSeconds(time)}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 mb-0.5">
                    {isEditing && isFirstOfSpeaker ? (
                      <span className="inline-flex items-center gap-1 relative">
                        <input
                          ref={speakerInputRef}
                          type="text"
                          value={speakerInput}
                          onChange={(e) => onSpeakerInputChange(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") onSpeakerSave(speaker, speakerInput);
                            if (e.key === "Escape") onSpeakerEditCancel();
                          }}
                          placeholder="Type name..."
                          className="text-xs border border-memoir-indigo/30 rounded px-2 py-0.5 w-36 focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
                        />
                        {speakerInput.length > 0 && (
                          <button
                            onClick={() => onSpeakerSave(speaker, speakerInput)}
                            disabled={savingSpeaker}
                            className="text-xs bg-memoir-indigo text-white px-2 py-0.5 rounded hover:bg-memoir-indigo-hover disabled:opacity-50"
                          >
                            {savingSpeaker ? "..." : "Save"}
                          </button>
                        )}
                        <button
                          onClick={onSpeakerEditCancel}
                          className="text-xs text-memoir-text-muted hover:text-memoir-text px-1"
                        >
                          Cancel
                        </button>
                        {teamSpeakers.length > 0 && speakerInput.length === 0 && (
                          <div className="absolute top-6 left-0 z-10 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated py-1 w-52 max-h-40 overflow-y-auto">
                            {teamSpeakers.map((sp) => (
                              <button
                                key={sp.id}
                                onClick={() => onSpeakerSave(speaker, sp.display_name, sp.id)}
                                className="w-full text-left px-3 py-1.5 text-xs hover:bg-memoir-bg"
                              >
                                <span className="font-medium text-memoir-text">{sp.display_name}</span>
                                {sp.email && <span className="text-memoir-text-muted ml-1">{sp.email}</span>}
                              </button>
                            ))}
                          </div>
                        )}
                      </span>
                    ) : (
                      <span
                        className={`font-semibold text-sm ${isUnidentified ? "opacity-75" : ""} ${readOnly ? "" : "cursor-pointer hover:underline"}`}
                        style={{ color }}
                        onClick={readOnly ? undefined : () => onSpeakerClick(speaker)}
                        title={readOnly ? undefined : (isUnidentified ? "Click to identify this speaker" : "Click to rename")}
                      >
                        [{speaker}]
                      </span>
                    )}
                    {isSegmentEdited && <EditedBadge />}
                    {segStatus && <SaveStatusIndicator status={segStatus} />}
                  </div>
                  <span
                    contentEditable={!readOnly}
                    suppressContentEditableWarning
                    className={`text-sm text-memoir-text-secondary outline-none rounded px-1 -mx-1 block ${
                      readOnly ? "" : "focus:bg-memoir-bg/50"
                    }`}
                    onBlur={readOnly ? undefined : (e) => {
                      const newText = e.currentTarget.textContent || "";
                      if (newText !== seg.text) {
                        handleSegmentInput(i, newText);
                      }
                    }}
                    onKeyDown={readOnly ? undefined : (e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        (e.target as HTMLElement).blur();
                      }
                    }}
                  >
                    {seg.text}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
