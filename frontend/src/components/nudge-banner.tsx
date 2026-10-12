"use client";

interface NudgeBannerProps {
  id: string;
  message: string;
  ctaText: string;
  ctaHref: string;
  variant?: "info" | "warning";
  onDismiss: (id: string) => void;
}

export function NudgeBanner({ id, message, ctaText, ctaHref, variant = "info", onDismiss }: NudgeBannerProps) {
  const bg = variant === "warning" ? "bg-amber-50 border-amber-200" : "bg-memoir-indigo-light border-memoir-indigo/20";
  const text = variant === "warning" ? "text-amber-800" : "text-memoir-indigo";
  const ctaColor = variant === "warning"
    ? "text-amber-700 hover:text-amber-900 font-semibold"
    : "text-memoir-indigo hover:text-memoir-indigo-hover font-semibold";

  return (
    <div className={`flex items-center justify-between gap-3 px-4 py-3 border rounded-card ${bg}`}>
      <div className="flex items-center gap-2 min-w-0">
        <svg className={`w-4 h-4 flex-shrink-0 ${text}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
        </svg>
        <span className={`text-sm ${text}`}>
          {message}{" "}
          <a href={ctaHref} className={`underline underline-offset-2 ${ctaColor}`}>
            {ctaText}
          </a>
        </span>
      </div>
      <button
        onClick={() => onDismiss(id)}
        className={`flex-shrink-0 p-1 rounded hover:bg-black/5 transition-colors ${text}`}
        aria-label="Dismiss"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
