"use client";

/**
 * "Upload Recording" dialog — opens first, then lets the user pick a
 * file from inside the modal. Title is optional. Posts via
 * api.uploadRecording.
 *
 * Self-contained — both the topbar Capture dropdown and the legacy
 * /meetings v1 "Upload file" button just open this modal with no file
 * pre-selected; the user picks (or drops) a file in here.
 */

import { useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { api, ApiError } from "@/lib/api";

const ACCEPT = "audio/*,video/*,.wav,.mp3,.m4a,.ogg,.webm,.mp4,.mov,.mkv";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function UploadCallModal({
  open,
  onOpenChange,
  teamId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teamId: string;
  onCreated: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const resetForm = () => {
    setFile(null);
    setTitle("");
    setError("");
    setDragOver(false);
  };

  const pickFile = () => fileInputRef.current?.click();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) setFile(f);
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setError("");
    setSubmitting(true);
    try {
      await api.uploadRecording(file, teamId, title.trim() || undefined);
      resetForm();
      onOpenChange(false);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to upload recording");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) resetForm(); onOpenChange(v); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload Recording</DialogTitle>
          <DialogDescription>
            Upload an audio or video file for transcription.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* File picker / preview area */}
          {file ? (
            <div className="p-3 bg-memoir-bg border border-memoir-border rounded-button">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-memoir-border rounded-button flex items-center justify-center flex-shrink-0">
                  <svg className="w-4 h-4 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
                  </svg>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-memoir-text truncate">{file.name}</p>
                  <p className="text-xs text-memoir-text-muted">{formatFileSize(file.size)}</p>
                </div>
                <button
                  type="button"
                  onClick={pickFile}
                  className="shrink-0 text-xs font-medium text-memoir-indigo hover:underline"
                >
                  Change
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={pickFile}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              className={`w-full p-5 border-2 border-dashed rounded-button text-center transition-colors ${
                dragOver
                  ? "border-memoir-indigo bg-memoir-indigo/5"
                  : "border-memoir-border bg-memoir-bg hover:border-memoir-indigo/40"
              }`}
            >
              <svg className="w-6 h-6 mx-auto mb-2 text-memoir-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
              </svg>
              <p className="text-sm font-medium text-memoir-text">Click to choose a file</p>
              <p className="text-xs text-memoir-text-muted mt-1">or drag and drop here — audio or video</p>
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            onChange={handleFileChange}
            className="hidden"
          />

          <div>
            <label className="block text-sm font-medium text-memoir-text-secondary mb-1">
              Title <span className="text-memoir-text-muted">(optional)</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Sales Demo with Acme"
              className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <DialogFooter>
            <button
              type="button"
              onClick={() => { resetForm(); onOpenChange(false); }}
              className="px-4 py-2 text-sm font-medium text-memoir-text-secondary bg-memoir-surface border border-memoir-border rounded-button hover:bg-memoir-bg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !file}
              className="px-4 py-2 text-sm font-medium text-white bg-memoir-indigo rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
            >
              {submitting ? "Uploading..." : "Transcribe"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
