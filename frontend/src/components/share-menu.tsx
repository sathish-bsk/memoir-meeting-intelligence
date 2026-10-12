"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import type { TranscriptResponse, MeetingDetail } from "@/lib/types";

interface ShareMenuProps {
  onDownloadPdf: () => void;
  transcript: TranscriptResponse | null;
  meeting: MeetingDetail;
  audioSrc: string | null;
  audioDuration: number;
}

function formatSeconds(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function exportTranscriptCsv(transcript: TranscriptResponse, meeting: MeetingDetail) {
  const rows = [["Timestamp", "Speaker", "Text"]];
  transcript.segments.forEach((seg) => {
    const time = formatSeconds(seg.original_start ?? seg.start);
    const speaker = transcript.speaker_map?.[seg.speaker || ""]?.name || seg.speaker || "Speaker";
    const text = seg.text.replace(/"/g, '""');
    rows.push([time, speaker, `"${text}"`]);
  });
  const csv = rows.map((r) => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(meeting.title || "transcript").replace(/[^a-zA-Z0-9]/g, "_")}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function ShareMenu({ onDownloadPdf, transcript, meeting, audioSrc, audioDuration }: ShareMenuProps) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [generatingLink, setGeneratingLink] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const handleDownloadAudio = () => {
    if (!audioSrc) return;
    // Open the presigned URL in a new tab — the browser will download the file.
    // We can't fetch() cross-origin presigned URLs due to CORS restrictions.
    window.open(audioSrc, "_blank");
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) {
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }
  }, [open]);

  const handleCopyLink = async () => {
    setGeneratingLink(true);
    try {
      const result = await api.createShareLink(meeting.id);
      const publicUrl = `${window.location.origin}/shared/${result.share_token}`;
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: copy the current page URL
      try {
        await navigator.clipboard.writeText(window.location.href);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // clipboard not available
      }
    } finally {
      setGeneratingLink(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="px-3 py-1.5 text-xs font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:border-memoir-indigo/30 transition-colors flex items-center gap-1.5"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M7.217 10.907a2.25 2.25 0 1 0 0 2.186m0-2.186c.18.324.283.696.283 1.093s-.103.77-.283 1.093m0-2.186 9.566-5.314m-9.566 7.5 9.566 5.314m0 0a2.25 2.25 0 1 0 3.935 2.186 2.25 2.25 0 0 0-3.935-2.186Zm0-12.814a2.25 2.25 0 1 0 3.935-2.186 2.25 2.25 0 0 0-3.935 2.186Z" />
        </svg>
        Share
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-48 bg-memoir-surface border border-memoir-border rounded-card shadow-elevated z-20 py-1">
          <button
            onClick={() => { handleCopyLink(); setOpen(false); }}
            disabled={generatingLink}
            className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2 transition-colors disabled:opacity-50"
          >
            <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
            </svg>
            {generatingLink ? "Generating..." : copied ? "Copied!" : "Copy public link"}
          </button>
          <button
            onClick={() => { onDownloadPdf(); setOpen(false); }}
            className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2 transition-colors"
          >
            <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            Download PDF
          </button>
          {meeting.has_recording && audioSrc && audioDuration > 0 && (
            <button
              onClick={() => { handleDownloadAudio(); setOpen(false); }}
              className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2 transition-colors"
            >
              <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 0 1-1.632 2.163l-1.32.377a1.803 1.803 0 1 1-.99-3.467l2.31-.66a2.25 2.25 0 0 0 1.632-2.163Zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 0 1-1.632 2.163l-1.32.377a1.803 1.803 0 0 1-.99-3.467l2.31-.66A2.25 2.25 0 0 0 9 15.553Z" />
              </svg>
              Download audio
            </button>
          )}
          {transcript && transcript.segments.length > 0 && (
            <button
              onClick={() => { exportTranscriptCsv(transcript, meeting); setOpen(false); }}
              className="w-full text-left px-3 py-2 text-sm text-memoir-text hover:bg-memoir-bg flex items-center gap-2 transition-colors"
            >
              <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h7.5c.621 0 1.125-.504 1.125-1.125m-9.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125m1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125m0 3.75h-7.5A1.125 1.125 0 0 1 12 18.375m9.75-12.75c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125m19.5 0v1.5c0 .621-.504 1.125-1.125 1.125M2.25 5.625v1.5c0 .621.504 1.125 1.125 1.125m0 0h17.25m-17.25 0h7.5c.621 0 1.125.504 1.125 1.125M3.375 8.25c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125m17.25-3.75h-7.5c-.621 0-1.125.504-1.125 1.125m8.625-1.125c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125M12 10.875v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 10.875c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125M13.125 12h7.5m-7.5 0c-.621 0-1.125.504-1.125 1.125M20.625 12c.621 0 1.125.504 1.125 1.125v1.5c0 .621-.504 1.125-1.125 1.125m-17.25 0h7.5M12 14.625v-1.5m0 1.5c0 .621-.504 1.125-1.125 1.125M12 14.625c0 .621.504 1.125 1.125 1.125m-2.25 0c.621 0 1.125.504 1.125 1.125m0 0v1.5c0 .621-.504 1.125-1.125 1.125M3.375 15.75h7.5" />
              </svg>
              Export CSV
            </button>
          )}
        </div>
      )}
    </div>
  );
}
