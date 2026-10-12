"use client";

/**
 * "New Meeting" dialog — paste a meeting URL to start recording or
 * schedule for later. Extracted from app/(app)/meetings/page.tsx so the
 * topbar Capture dropdown can open the same flow without routing.
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { api, ApiError } from "@/lib/api";

export function CreateCallModal({
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
  const [meetingUrl, setMeetingUrl] = useState("");
  const [title, setTitle] = useState("");
  const [scheduleForLater, setScheduleForLater] = useState(false);
  const [scheduleDate, setScheduleDate] = useState("");
  const [scheduleTime, setScheduleTime] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const today = new Date().toISOString().split("T")[0];

  const resetForm = () => {
    setMeetingUrl("");
    setTitle("");
    setScheduleForLater(false);
    setScheduleDate("");
    setScheduleTime("");
    setError("");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      const data: { meeting_url: string; title?: string; team_id: string; scheduled_start_at?: string } = {
        meeting_url: meetingUrl,
        team_id: teamId,
      };
      if (title.trim()) data.title = title.trim();
      if (scheduleForLater && scheduleDate && scheduleTime) {
        data.scheduled_start_at = new Date(`${scheduleDate}T${scheduleTime}`).toISOString();
      }
      await api.createMeeting(data);
      resetForm();
      onOpenChange(false);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create meeting");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) resetForm(); onOpenChange(v); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Meeting</DialogTitle>
          <DialogDescription>
            Paste a meeting link to start recording or schedule for later.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-memoir-text-secondary mb-1">
              Meeting URL <span className="text-red-500">*</span>
            </label>
            <input
              type="url"
              required
              value={meetingUrl}
              onChange={(e) => setMeetingUrl(e.target.value)}
              placeholder="https://meet.google.com/..."
              className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
            />
          </div>
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
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="schedule-later"
              checked={scheduleForLater}
              onChange={(e) => setScheduleForLater(e.target.checked)}
              className="rounded border-memoir-border"
            />
            <label htmlFor="schedule-later" className="text-sm text-memoir-text-secondary">
              Schedule for later
            </label>
          </div>
          {scheduleForLater && (
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs font-medium text-memoir-text-muted mb-1">Date</label>
                <input
                  type="date"
                  required={scheduleForLater}
                  min={today}
                  value={scheduleDate}
                  onChange={(e) => setScheduleDate(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
                />
              </div>
              <div className="flex-1">
                <label className="block text-xs font-medium text-memoir-text-muted mb-1">Time</label>
                <input
                  type="time"
                  required={scheduleForLater}
                  value={scheduleTime}
                  onChange={(e) => setScheduleTime(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-memoir-border rounded-button focus:outline-none focus:ring-2 focus:ring-memoir-indigo focus:border-transparent"
                />
              </div>
            </div>
          )}
          {error && (
            <p className="text-sm text-red-600">{error}</p>
          )}
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
              disabled={submitting || !meetingUrl}
              className="px-4 py-2 text-sm font-medium text-white bg-memoir-indigo rounded-button hover:bg-memoir-indigo-hover transition-colors disabled:opacity-50"
            >
              {submitting
                ? "Creating..."
                : scheduleForLater
                  ? "Schedule"
                  : "Start Recording"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
