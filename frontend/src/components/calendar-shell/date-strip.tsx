"use client";

/**
 * DateStrip — week- or month-mode calendar strip with per-day activity dots.
 *
 * Used by the Meetings v3 + Events v3 lists (plan/meetings-events-v3-list.md
 * §5.2). Hybrid behavior:
 *   - Week mode: 7 day cells with weekday header + date number + activity dots
 *   - Month mode: 5-6 row grid of date cells
 *   - Clicking a date in month mode flips back to week mode, snapped to the
 *     clicked date's week, with that date selected (locked decision §11.2)
 */

import { useMemo } from "react";

export type DateMode = "week" | "month";

export interface DateStripProps {
  mode: DateMode;
  onModeChange: (mode: DateMode) => void;
  /** Anchor date for the visible range (Monday of the week, or any date in
   *  the month for month mode). The component computes the range from this. */
  rangeStart: Date;
  /** Sparse map of ISO date (YYYY-MM-DD) → activity count. */
  activity: Record<string, number>;
  /** Date the user has clicked on; rendered with the filled-bar highlight. */
  selectedDate: Date;
  onDateSelect: (date: Date) => void;
  /** Navigate ±1 week (in week mode) or ±1 month (in month mode). */
  onRangeChange: (direction: -1 | 1) => void;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function isoDate(d: Date): string {
  // Local-date ISO (no TZ shift); keys match what we send to the backend.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function sameDay(a: Date, b: Date): boolean {
  return isoDate(a) === isoDate(b);
}

function activityDots(count: number): string {
  if (count <= 0) return "";
  if (count === 1) return "•";
  if (count <= 3) return "••";
  return "•••";
}

function startOfWeek(d: Date): Date {
  // Monday-start. JS getDay(): Sun=0, Mon=1, …, Sat=6.
  const day = d.getDay();
  const diffToMonday = day === 0 ? -6 : 1 - day;
  const out = new Date(d);
  out.setDate(d.getDate() + diffToMonday);
  out.setHours(0, 0, 0, 0);
  return out;
}

function formatWeekRange(weekStart: Date): string {
  const end = new Date(weekStart);
  end.setDate(weekStart.getDate() + 6);
  const fmt = (d: Date) =>
    d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${fmt(weekStart)} – ${fmt(end)}`;
}

function formatMonth(d: Date): string {
  return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

export function DateStrip({
  mode,
  onModeChange,
  rangeStart,
  activity,
  selectedDate,
  onDateSelect,
  onRangeChange,
}: DateStripProps) {
  const today = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }, []);

  // Header label
  const headerLabel =
    mode === "week" ? formatWeekRange(rangeStart) : formatMonth(rangeStart);

  return (
    <div className="mb-4">
      {/* Header: navigation + range label + Today + mode toggle */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onRangeChange(-1)}
            className="p-1 rounded hover:bg-memoir-bg text-memoir-text-muted hover:text-memoir-text"
            aria-label="Previous range"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <h2 className="text-sm font-semibold text-memoir-text">{headerLabel}</h2>
          <button
            type="button"
            onClick={() => onRangeChange(1)}
            className="p-1 rounded hover:bg-memoir-bg text-memoir-text-muted hover:text-memoir-text"
            aria-label="Next range"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {/* "Today" jump — shows when the selected date isn't today. */}
          {!sameDay(selectedDate, today) && (
            <button
              type="button"
              onClick={() => onDateSelect(today)}
              className="px-2 py-0.5 rounded text-xs font-semibold text-memoir-indigo border border-memoir-indigo/40 hover:bg-memoir-indigo/10"
            >
              Today
            </button>
          )}
        </div>

        {/* Mode toggle */}
        <div className="inline-flex rounded-md border border-memoir-border bg-white p-0.5 text-xs font-medium">
          {(["week", "month"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onModeChange(m)}
              className={
                mode === m
                  ? "px-2.5 py-1 rounded bg-memoir-indigo text-white"
                  : "px-2.5 py-1 rounded text-memoir-text-muted hover:text-memoir-text"
              }
            >
              {m === "week" ? "Week" : "Month"}
            </button>
          ))}
        </div>
      </div>

      {mode === "week" ? (
        <WeekGrid
          weekStart={rangeStart}
          activity={activity}
          selectedDate={selectedDate}
          today={today}
          onDateSelect={onDateSelect}
        />
      ) : (
        <MonthGrid
          monthAnchor={rangeStart}
          activity={activity}
          selectedDate={selectedDate}
          today={today}
          onDateClick={(d) => {
            // Locked §11.2: clicking a date in month view flips to week
            // mode centered on that date's week, with the date selected.
            onModeChange("week");
            onDateSelect(d);
          }}
        />
      )}
    </div>
  );
}

function WeekGrid({
  weekStart,
  activity,
  selectedDate,
  today,
  onDateSelect,
}: {
  weekStart: Date;
  activity: Record<string, number>;
  selectedDate: Date;
  today: Date;
  onDateSelect: (d: Date) => void;
}) {
  const days = useMemo(() => {
    const out: Date[] = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(weekStart);
      d.setDate(weekStart.getDate() + i);
      out.push(d);
    }
    return out;
  }, [weekStart]);

  return (
    <div className="grid grid-cols-7 gap-1">
      {days.map((d, i) => {
        const iso = isoDate(d);
        const count = activity[iso] || 0;
        const isSelected = sameDay(d, selectedDate);
        const isToday = sameDay(d, today);
        return (
          <button
            key={iso}
            type="button"
            onClick={() => onDateSelect(d)}
            className={
              "flex flex-col items-center py-2 px-1 rounded-md transition-colors " +
              (isSelected
                ? "bg-memoir-indigo/10 ring-1 ring-memoir-indigo"
                : "hover:bg-memoir-bg")
            }
          >
            <span className="text-[10px] font-medium uppercase tracking-wider text-memoir-text-muted">
              {WEEKDAY_LABELS[i]}
            </span>
            <span
              className={
                "text-base font-semibold mt-0.5 " +
                (isToday
                  ? "text-memoir-indigo"
                  : isSelected
                    ? "text-memoir-text"
                    : "text-memoir-text-secondary")
              }
            >
              {d.getDate()}
            </span>
            <span
              className={
                "text-xs mt-0.5 h-3 " +
                (count > 0 ? "text-memoir-indigo" : "text-memoir-text-muted")
              }
            >
              {count > 0 ? activityDots(count) : "—"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function MonthGrid({
  monthAnchor,
  activity,
  selectedDate,
  today,
  onDateClick,
}: {
  monthAnchor: Date;
  activity: Record<string, number>;
  selectedDate: Date;
  today: Date;
  onDateClick: (d: Date) => void;
}) {
  // Compute the 5-6 weeks visible. Start from the Monday of the week
  // containing the first day of the month; end at the Sunday of the
  // week containing the last day.
  const cells = useMemo(() => {
    const first = new Date(monthAnchor.getFullYear(), monthAnchor.getMonth(), 1);
    const last = new Date(monthAnchor.getFullYear(), monthAnchor.getMonth() + 1, 0);
    const gridStart = startOfWeek(first);
    const gridEnd = new Date(last);
    // Walk forward to Sunday
    while (gridEnd.getDay() !== 0) {
      gridEnd.setDate(gridEnd.getDate() + 1);
    }
    const out: Date[] = [];
    const cur = new Date(gridStart);
    while (cur <= gridEnd) {
      out.push(new Date(cur));
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  }, [monthAnchor]);

  const monthIdx = monthAnchor.getMonth();

  return (
    <div>
      {/* Weekday header */}
      <div className="grid grid-cols-7 gap-1 mb-1">
        {WEEKDAY_LABELS.map((l) => (
          <div
            key={l}
            className="text-[10px] font-medium uppercase tracking-wider text-memoir-text-muted text-center py-1"
          >
            {l}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d) => {
          const iso = isoDate(d);
          const count = activity[iso] || 0;
          const isSelected = sameDay(d, selectedDate);
          const isToday = sameDay(d, today);
          const isInMonth = d.getMonth() === monthIdx;
          return (
            <button
              key={iso}
              type="button"
              onClick={() => onDateClick(d)}
              className={
                "flex flex-col items-center py-1.5 px-1 rounded-md transition-colors " +
                (isSelected
                  ? "bg-memoir-indigo/10 ring-1 ring-memoir-indigo"
                  : "hover:bg-memoir-bg") +
                (isInMonth ? "" : " opacity-40")
              }
            >
              <span
                className={
                  "text-sm font-semibold " +
                  (isToday
                    ? "text-memoir-indigo"
                    : isSelected
                      ? "text-memoir-text"
                      : "text-memoir-text-secondary")
                }
              >
                {d.getDate()}
              </span>
              <span
                className={
                  "text-xs h-3 " +
                  (count > 0 ? "text-memoir-indigo" : "text-transparent")
                }
              >
                {count > 0 ? activityDots(count) : "—"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Date utilities exported for the page-level state -─────────────────

export const dateStripUtils = {
  /** Monday of the week containing `d`, at 00:00:00 local. */
  startOfWeek,
  /** First of the month of `d`, at 00:00:00 local. */
  startOfMonth: (d: Date): Date => {
    const out = new Date(d.getFullYear(), d.getMonth(), 1);
    out.setHours(0, 0, 0, 0);
    return out;
  },
  /** End of week (Sunday 23:59:59.999) for the week containing `d`. */
  endOfWeek: (d: Date): Date => {
    const start = startOfWeek(d);
    const out = new Date(start);
    out.setDate(start.getDate() + 6);
    out.setHours(23, 59, 59, 999);
    return out;
  },
  /** Last day of the month, 23:59:59.999. */
  endOfMonth: (d: Date): Date => {
    const out = new Date(d.getFullYear(), d.getMonth() + 1, 0);
    out.setHours(23, 59, 59, 999);
    return out;
  },
  /** YYYY-MM-DD in local time. */
  isoDate,
  /** Start of local day for `d`, returned as a UTC ISO string. Use this
   *  to send half-open `start_date` to the backend so the filter range
   *  is anchored to the user's local-day boundary, not UTC midnight. */
  localStartOfDayIso: (d: Date): string => {
    const out = new Date(d);
    out.setHours(0, 0, 0, 0);
    return out.toISOString();
  },
  /** End of local day for `d` (23:59:59.999), returned as UTC ISO. */
  localEndOfDayIso: (d: Date): string => {
    const out = new Date(d);
    out.setHours(23, 59, 59, 999);
    return out.toISOString();
  },
  /** Minutes east of UTC. JS getTimezoneOffset() returns minutes WEST
   *  (e.g., IST=-330), so we negate. */
  tzOffsetMinutes: (): number => -new Date().getTimezoneOffset(),
};
