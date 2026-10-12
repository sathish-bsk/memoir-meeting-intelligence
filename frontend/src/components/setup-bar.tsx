"use client";

import type { SetupStep } from "@/hooks/use-setup-steps";

interface SetupBarProps {
  steps: SetupStep[];
  onDismiss: () => void;
}

export function SetupBar({ steps, onDismiss }: SetupBarProps) {
  const completedCount = steps.filter((s) => s.done).length;

  // Sort: completed first, then pending
  const sorted = [...steps].sort((a, b) => {
    if (a.done === b.done) return 0;
    return a.done ? -1 : 1;
  });

  return (
    <div className="bg-memoir-indigo-light border-b border-memoir-indigo/20 px-6 py-2.5 z-40 relative">
      {/* Header row: title (left) + count + dismiss (right) */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm">{"\uD83D\uDE80"}</span>
          <span className="text-sm font-semibold text-memoir-text">Complete your setup</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-memoir-text-secondary font-medium">
            {completedCount}/{steps.length}
          </span>
          <button
            onClick={onDismiss}
            className="p-1 text-memoir-text-muted hover:text-memoir-text rounded transition-colors"
            aria-label="Dismiss setup bar"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Steps: equal-width columns, centered text, completed first */}
      <div
        className="grid mb-2"
        style={{ gridTemplateColumns: `repeat(${steps.length}, 1fr)` }}
      >
        {sorted.map((step, i) => (
          <div key={i} className="flex items-center justify-center gap-1.5">
            {step.done ? (
              <div className="w-4 h-4 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0">
                <svg className="w-2.5 h-2.5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              </div>
            ) : (
              <div className="w-4 h-4 rounded-full border-2 border-memoir-border/60 flex-shrink-0" />
            )}
            <span
              className={`text-xs whitespace-nowrap ${
                step.done
                  ? "text-memoir-text-muted"
                  : "text-memoir-text font-medium"
              }`}
            >
              {step.label}
            </span>
            {step.comingSoon && !step.done && (
              <span className="text-[9px] px-1 py-0.5 rounded-full bg-memoir-border text-memoir-text-muted font-medium">
                Soon
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Full-width progress bar */}
      <div className="w-full h-2 bg-white/60 rounded-full overflow-hidden">
        <div
          className="h-full bg-memoir-indigo rounded-full transition-all duration-500"
          style={{ width: `${(completedCount / steps.length) * 100}%` }}
        />
      </div>
    </div>
  );
}
