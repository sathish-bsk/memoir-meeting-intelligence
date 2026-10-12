"use client";

import type { SetupStep } from "@/hooks/use-setup-steps";

interface SetupChecklistProps {
  steps: SetupStep[];
  onDismiss?: () => void;
}

export function SetupChecklist({ steps, onDismiss }: SetupChecklistProps) {
  const completedCount = steps.filter((s) => s.done).length;
  const nextStep = steps.find((s) => !s.done && !s.comingSoon);

  return (
    <div className="bg-memoir-indigo-light border-l-[3px] border-memoir-indigo rounded-card p-5 mb-6">
      {/* Header row */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-base">&#x1F680;</span>
          <h3 className="text-sm font-semibold text-memoir-text">
            {completedCount === steps.length ? "You\u2019re all set!" : "Getting started"}
          </h3>
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="p-1 text-memoir-text-muted hover:text-memoir-text rounded transition-colors flex-shrink-0"
            aria-label="Dismiss"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Full-width progress bar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-1.5 bg-white/60 rounded-full overflow-hidden">
          <div
            className="h-full bg-memoir-indigo rounded-full transition-all duration-500"
            style={{ width: `${(completedCount / steps.length) * 100}%` }}
          />
        </div>
        <span className="text-xs text-memoir-text-secondary flex-shrink-0">{completedCount} of {steps.length}</span>
      </div>

      {/* Two-column steps grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-2.5">
            {step.done ? (
              <div className="w-5 h-5 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0">
                <svg className="w-3 h-3 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              </div>
            ) : (
              <div className="w-5 h-5 rounded-full border-2 border-memoir-border flex-shrink-0" />
            )}
            <span className={`text-xs ${step.done ? "text-memoir-text-muted" : "text-memoir-text font-medium"}`}>
              {step.label}
            </span>
            {step.comingSoon && !step.done && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-memoir-border text-memoir-text-muted font-medium">
                Coming soon
              </span>
            )}
            {step.href && !step.done && (
              <a
                href={step.href}
                {...(step.href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                className="text-[10px] text-memoir-indigo hover:underline font-medium"
              >
                Connect &rarr;
              </a>
            )}
          </div>
        ))}
      </div>

      {/* Next step CTA */}
      {nextStep && (
        <div className="flex items-center gap-2 mt-3 pt-3 border-t border-memoir-indigo/10">
          <span className="text-xs text-memoir-text-secondary">Next:</span>
          <span className="text-xs font-medium text-memoir-text">{nextStep.label}</span>
          {nextStep.href && (
            <a
              href={nextStep.href}
              {...(nextStep.href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              className="text-xs font-medium text-memoir-indigo hover:text-memoir-indigo/80 transition-colors"
            >
              Set up now &rarr;
            </a>
          )}
        </div>
      )}
    </div>
  );
}
