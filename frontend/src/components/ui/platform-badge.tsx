import * as React from "react";

import { cn } from "@/lib/utils";

export type Platform = "meet" | "zoom" | "teams" | "web_recorder" | "upload";

const PLATFORM_STYLES: Record<Platform, { bg: string; color: string; label: string }> = {
  meet: { bg: "#E8F5E9", color: "#00897B", label: "Meet" },
  zoom: { bg: "#E3F2FD", color: "#2D8CFF", label: "Zoom" },
  teams: { bg: "#EDE7F6", color: "#5B5FC7", label: "Teams" },
  // Source-derived platforms (no real meeting URL — synthesized for
  // web_recorder and upload meetings after migration 040 dropped the
  // "upload://local" placeholder).
  web_recorder: { bg: "#EEEAFE", color: "#5C5CE0", label: "Web Recorder" },
  upload: { bg: "#FFF3E0", color: "#E8683A", label: "Upload" },
};

/**
 * Resolve a Platform label from meeting source + URL together.
 *
 * Branches on `source` first so web_recorder + upload (no meeting_url
 * after migration 040) render their own first-class platform pill
 * instead of falling through to "meet" by URL-parsing accident.
 * Falls back to URL host parsing for everything that has a real URL.
 */
export function platformFromSourceOrUrl(
  source: string | null | undefined,
  url: string | null | undefined,
): Platform {
  if (source === "web_recorder") return "web_recorder";
  if (source === "upload" || source === "link_upload") return "upload";
  if (!url) return "meet";
  if (url.includes("zoom")) return "zoom";
  if (url.includes("teams.microsoft") || url.includes("teams.live")) return "teams";
  return "meet";
}

interface PlatformBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  platform: Platform;
  size?: "sm" | "md";
}

export function PlatformBadge({ platform, size = "sm", className, ...props }: PlatformBadgeProps) {
  const style = PLATFORM_STYLES[platform];
  const pad = size === "md" ? "px-2.5 py-0.5 text-[11px]" : "px-2 py-[2px] text-[10px]";
  return (
    <span
      className={cn("inline-flex items-center rounded font-bold", pad, className)}
      style={{ color: style.color, background: style.bg }}
      {...props}
    >
      {style.label}
    </span>
  );
}
