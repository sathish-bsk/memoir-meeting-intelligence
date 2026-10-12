"use client";

/**
 * CalendarShell — the shared outer chrome for the Meetings v3 + Events v3
 * tabs: tab strip across the top, then DateStrip, then ScopeChips,
 * then a slot for tab-specific detail filters, then the body content.
 *
 * Plan: plan/meetings-events-v3-list.md §5.1.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { DateStrip, DateMode, dateStripUtils } from "./date-strip";
import { ScopeChips, ScopeValue } from "./scope-chips";

export interface CalendarShellTab {
  id: string;
  label: string;
  href: string;
}

export interface CalendarShellProps {
  tabs: CalendarShellTab[];
  activeTabId: string;
  /** Render slot top-right (e.g., a "+ New call" or "+ Schedule" button). */
  rightCta?: ReactNode;

  /** DateStrip state. */
  dateMode: DateMode;
  onDateModeChange: (m: DateMode) => void;
  selectedDate: Date;
  onSelectedDateChange: (d: Date) => void;
  activity: Record<string, number>;

  /** ScopeChips state. */
  scope: ScopeValue;
  onScopeChange: (v: ScopeValue) => void;
  userRole: "owner" | "admin" | "member";

  /** Render slot for tab-specific detail filters under the scope chips. */
  detailFilters?: ReactNode;

  /** Body — day-grouped card list. */
  children: ReactNode;
}

export function CalendarShell({
  tabs,
  activeTabId,
  rightCta,
  dateMode,
  onDateModeChange,
  selectedDate,
  onSelectedDateChange,
  activity,
  scope,
  onScopeChange,
  userRole,
  detailFilters,
  children,
}: CalendarShellProps) {
  // The DateStrip's rangeStart depends on the mode:
  //  - week mode: Monday of the week containing selectedDate
  //  - month mode: 1st of the month containing selectedDate
  const rangeStart =
    dateMode === "week"
      ? dateStripUtils.startOfWeek(selectedDate)
      : dateStripUtils.startOfMonth(selectedDate);

  const handleRangeChange = (direction: -1 | 1) => {
    const next = new Date(selectedDate);
    if (dateMode === "week") {
      next.setDate(selectedDate.getDate() + direction * 7);
    } else {
      next.setMonth(selectedDate.getMonth() + direction);
    }
    onSelectedDateChange(next);
  };

  return (
    <div className="h-full flex flex-col px-6 py-6">
      {/* Tab strip + right CTA */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-1">
          {tabs.map((tab) => (
            <Link
              key={tab.id}
              href={tab.href}
              className={
                "px-4 py-2 rounded-md text-sm font-semibold transition-colors " +
                (tab.id === activeTabId
                  ? "bg-memoir-indigo/10 text-memoir-indigo"
                  : "text-memoir-text-muted hover:text-memoir-text hover:bg-memoir-bg")
              }
            >
              {tab.label}
            </Link>
          ))}
        </div>
        {rightCta && <div>{rightCta}</div>}
      </div>

      {/* DateStrip */}
      <DateStrip
        mode={dateMode}
        onModeChange={onDateModeChange}
        rangeStart={rangeStart}
        activity={activity}
        selectedDate={selectedDate}
        onDateSelect={onSelectedDateChange}
        onRangeChange={handleRangeChange}
      />

      {/* ScopeChips + detail filters */}
      <div className="flex items-center justify-between gap-3 mb-5 flex-wrap">
        <ScopeChips value={scope} onChange={onScopeChange} userRole={userRole} />
        {detailFilters && <div className="flex items-center gap-2">{detailFilters}</div>}
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-auto">{children}</div>
    </div>
  );
}
