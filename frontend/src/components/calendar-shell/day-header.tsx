"use client";

/**
 * DayHeader — the small header above the day's list of cards.
 * Example: "SAT MAY 24 · 4 meetings" or "SAT MAY 24 · 3 events (1 past, 2 upcoming)"
 *
 * Plan: plan/meetings-events-v3-list.md §5.4.
 */

export interface DayHeaderProps {
  date: Date;
  count: number;
  unit: "meetings" | "events";
  /** Optional breakdown for the events tab (past + upcoming). */
  breakdown?: { past: number; upcoming: number };
}

function isToday(d: Date): boolean {
  const today = new Date();
  return (
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  );
}

function isYesterday(d: Date): boolean {
  const y = new Date();
  y.setDate(y.getDate() - 1);
  return (
    d.getFullYear() === y.getFullYear() &&
    d.getMonth() === y.getMonth() &&
    d.getDate() === y.getDate()
  );
}

function isTomorrow(d: Date): boolean {
  const t = new Date();
  t.setDate(t.getDate() + 1);
  return (
    d.getFullYear() === t.getFullYear() &&
    d.getMonth() === t.getMonth() &&
    d.getDate() === t.getDate()
  );
}

function formatDate(d: Date): string {
  if (isToday(d)) return "Today";
  if (isYesterday(d)) return "Yesterday";
  if (isTomorrow(d)) return "Tomorrow";
  return d.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function DayHeader({ date, count, unit, breakdown }: DayHeaderProps) {
  const dateLabel = formatDate(date);
  const fullDate = date.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  const countLabel = count === 1 ? `1 ${unit.slice(0, -1)}` : `${count} ${unit}`;

  return (
    <h3 className="text-xs font-semibold text-memoir-text-muted uppercase tracking-wider mb-3">
      <span title={fullDate}>{dateLabel}</span>
      <span className="mx-1.5">·</span>
      <span>{countLabel}</span>
      {breakdown && unit === "events" && (
        <span className="ml-1.5 normal-case font-normal">
          ({breakdown.past} past, {breakdown.upcoming} upcoming)
        </span>
      )}
    </h3>
  );
}
