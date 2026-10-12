"use client";

import type { SaveStatus } from "@/hooks/use-auto-save";

const statusConfig: Record<SaveStatus, { text: string; color: string } | null> = {
  idle: null,
  saving: { text: "Saving\u2026", color: "text-memoir-text-muted" },
  saved: { text: "Saved", color: "text-emerald-600" },
  error: { text: "Failed to save", color: "text-red-500" },
};

export function SaveStatusIndicator({ status }: { status: SaveStatus }) {
  const config = statusConfig[status];
  if (!config) return null;

  return (
    <span className={`text-xs font-medium ${config.color} transition-opacity`}>
      {config.text}
    </span>
  );
}
