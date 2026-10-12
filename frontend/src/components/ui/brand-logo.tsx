import * as React from "react";

import { cn } from "@/lib/utils";

interface BrandLogoProps {
  /** Pixel size of the logo glyph (square). Default 34 (matches sidebar header). */
  size?: number;
  /** Show the "Memoir" wordmark next to the glyph. */
  withWordmark?: boolean;
  className?: string;
}

/**
 * Memoir brand glyph: a rounded-square `m` rendered in DM Serif Display.
 *
 * Used in: sidebar header, topbar mobile collapse, extension popup header,
 * and the right Calendar panel header. Keep usage consistent — this is the
 * only place the `m` glyph should appear.
 */
export function BrandLogo({ size = 34, withWordmark = false, className }: BrandLogoProps) {
  const fontSize = Math.round(size * 0.55);
  const radius = Math.round(size * 0.3);
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <span
        className="flex shrink-0 items-center justify-center bg-brand text-white"
        style={{ width: size, height: size, borderRadius: radius, fontSize, lineHeight: 1 }}
      >
        <span className="font-serif font-normal">m</span>
      </span>
      {withWordmark && (
        <span
          className="font-serif text-ink"
          style={{ fontSize: Math.round(size * 0.62), letterSpacing: -0.5 }}
        >
          Memoir
        </span>
      )}
    </span>
  );
}
