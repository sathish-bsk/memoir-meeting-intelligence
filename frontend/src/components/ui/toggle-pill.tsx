"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

interface TogglePillProps {
  enabled: boolean;
  onChange?: (next: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}

/**
 * 36×20 sliding toggle pill (revamp v2 design).
 *
 * Distinct from any Radix-style switch in case we add `@radix-ui/react-switch`
 * later — this one ships without a Radix dep and matches the mockup pixel-for-pixel
 * (used in Upcoming Meetings row + right Calendar panel).
 */
export function TogglePill({ enabled, onChange, disabled, ariaLabel, className }: TogglePillProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange?.(!enabled)}
      className={cn(
        "relative inline-block h-5 w-9 shrink-0 rounded-full transition-colors duration-200",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2",
        enabled ? "bg-brand-green" : "bg-surface-border",
        disabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      <span
        className={cn(
          "absolute top-[2px] h-4 w-4 rounded-full bg-white shadow-sm transition-[left] duration-200",
          enabled ? "left-[18px]" : "left-[2px]",
        )}
      />
    </button>
  );
}
