"use client";

/**
 * Schedule Meeting modal — Phase 1 of the notetaker-email rollout.
 *
 * Two-tile picker (Google Calendar / Microsoft Outlook). Each tile opens
 * the provider's create-event page in a new tab with the bot's invite
 * email pre-filled as an attendee. The user completes the rest of the
 * event in their own calendar UI; the invite then lands in the bot's
 * calendar via Workspace's auto-add behavior (Phase 2 picks it up from
 * there).
 *
 * The bot's invite address is read from NEXT_PUBLIC_MEMOIR_BOT_INVITE_EMAIL
 * (matches §6 of plan/notetaker-email-bot-identity.md). Defaults to
 * hello@meetmemoir.com if the env var is unset.
 */

import { useEffect, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const BOT_INVITE_EMAIL =
  process.env.NEXT_PUBLIC_MEMOIR_BOT_INVITE_EMAIL || "hello@meetmemoir.com";

function buildGoogleCalendarUrl(): string {
  // Pre-fill the notetaker as an attendee only. Title + description are
  // left to the user so the provider's create-event page is the blank
  // canvas they expect, not a half-filled template.
  const params = new URLSearchParams({
    action: "TEMPLATE",
    add: BOT_INVITE_EMAIL,
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

function buildOutlookUrl(): string {
  // Live.com (personal) endpoint — Outlook auto-detects business
  // accounts and routes to outlook.office.com transparently.
  // Same pattern: attendee only, no subject/body.
  const params = new URLSearchParams({
    path: "/calendar/action/compose",
    rru: "addevent",
    to: BOT_INVITE_EMAIL,
  });
  return `https://outlook.live.com/calendar/0/deeplink/compose?${params.toString()}`;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ScheduleMeetingModal({ open, onOpenChange }: Props) {
  // First focusable tile gets focus when modal opens (keyboard-friendly).
  const googleTileRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) {
      const t = window.setTimeout(() => googleTileRef.current?.focus(), 50);
      return () => window.clearTimeout(t);
    }
  }, [open]);

  const openProvider = (url: string) => {
    // noopener,noreferrer = security + no tab-coupling. New tab keeps
    // Memoir context intact for the user.
    window.open(url, "_blank", "noopener,noreferrer");
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Schedule Meeting</DialogTitle>
          <DialogDescription>
            Your AI Notetaker will be invited to the calendar meeting to record,
            transcribe and summarize.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-3 flex flex-col gap-2.5">
          <button
            ref={googleTileRef}
            type="button"
            onClick={() => openProvider(buildGoogleCalendarUrl())}
            className="flex items-center gap-3 rounded-lg border border-memoir-border bg-memoir-surface px-4 py-3.5 text-left text-sm font-medium text-memoir-text transition-colors hover:border-memoir-indigo/40 hover:bg-memoir-bg"
          >
            <GoogleCalendarIcon />
            <span className="flex-1">Google Calendar</span>
            <ChevronRight />
          </button>

          <button
            type="button"
            onClick={() => openProvider(buildOutlookUrl())}
            className="flex items-center gap-3 rounded-lg border border-memoir-border bg-memoir-surface px-4 py-3.5 text-left text-sm font-medium text-memoir-text transition-colors hover:border-memoir-indigo/40 hover:bg-memoir-bg"
          >
            <OutlookIcon />
            <span className="flex-1">Microsoft Outlook</span>
            <ChevronRight />
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ─── Icons ──────────────────────────────────────────────────────────

function GoogleCalendarIcon() {
  // Google Calendar brand mark — multi-color stripes + blue "31".
  // Approximates the public brand icon (white face, red/green/yellow/blue
  // accents on each edge, big blue "31" date glyph).
  return (
    <svg width={26} height={26} viewBox="0 0 48 48" aria-hidden="true">
      {/* White card body */}
      <rect x={6} y={6} width={36} height={36} fill="#fff" rx={2} />
      {/* Top red stripe */}
      <rect x={6} y={6} width={36} height={4} fill="#EA4335" rx={2} />
      {/* Right green stripe */}
      <rect x={38} y={6} width={4} height={36} fill="#34A853" rx={2} />
      {/* Bottom yellow stripe */}
      <rect x={6} y={38} width={36} height={4} fill="#FBBC04" rx={2} />
      {/* Left blue stripe */}
      <rect x={6} y={6} width={4} height={36} fill="#4285F4" rx={2} />
      {/* Frame outline */}
      <rect x={6} y={6} width={36} height={36} fill="none" stroke="rgba(0,0,0,0.04)" />
      {/* The signature "31" */}
      <text
        x={24}
        y={31}
        textAnchor="middle"
        fontFamily="'Google Sans', 'Product Sans', Arial, sans-serif"
        fontSize={14}
        fontWeight={500}
        fill="#1A73E8"
      >
        31
      </text>
    </svg>
  );
}

function OutlookIcon() {
  // Microsoft Outlook 2018+ brand mark — blue square with white "O"
  // and envelope flap. Brand color: Outlook blue #0078D4.
  return (
    <svg width={26} height={26} viewBox="0 0 48 48" aria-hidden="true">
      {/* Blue tile */}
      <rect x={4} y={6} width={26} height={36} fill="#0078D4" rx={2} />
      {/* White "O" */}
      <ellipse cx={17} cy={24} rx={6.5} ry={8.5} fill="#fff" />
      <ellipse cx={17} cy={24} rx={3} ry={4.8} fill="#0078D4" />
      {/* Envelope flap (right side) */}
      <path
        d="M30 14h14v22a2 2 0 0 1-2 2H30V14z"
        fill="#fff"
        stroke="#0078D4"
        strokeWidth={0.5}
      />
      <path
        d="M30 14l7 7 7-7"
        fill="none"
        stroke="#0078D4"
        strokeWidth={1.6}
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="text-memoir-text-muted">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
