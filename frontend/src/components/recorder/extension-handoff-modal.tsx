"use client";

const CHROME_STORE_URL =
  "https://chromewebstore.google.com/detail/hbjhbdfiagiodfdpclbipljkoocmemea?utm_source=record-handoff-modal";

export interface ExtensionHandoffModalProps {
  open: boolean;
  onClose: () => void;
  onConfirmMicOnly: () => void;
}

export function ExtensionHandoffModal({
  open,
  onClose,
  onConfirmMicOnly,
}: ExtensionHandoffModalProps) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="memoir-ext-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-sm bg-white rounded-xl shadow-xl p-6">
        <h2
          id="memoir-ext-modal-title"
          className="text-lg font-medium text-memoir-text mb-2"
        >
          Install the Memoir extension
        </h2>
        <p className="text-sm text-memoir-text-secondary mb-5">
          Get one-click meeting recording with all participants&apos; audio
          captured automatically. Without the extension, we can only record
          your microphone.
        </p>

        <div className="flex flex-col gap-2">
          <a
            href={CHROME_STORE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full inline-flex items-center justify-center gap-1.5 py-2.5 px-3 rounded-button bg-memoir-indigo hover:bg-memoir-indigo-dark text-white text-sm font-semibold"
          >
            Install extension
            <svg
              className="w-4 h-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M14 5h5v5M19 5L9 15M4 19h6"
              />
            </svg>
          </a>
          <button
            type="button"
            onClick={onConfirmMicOnly}
            className="w-full py-2.5 px-3 rounded-button border border-memoir-border bg-white hover:bg-memoir-bg text-memoir-text text-sm font-medium"
          >
            Record mic only
          </button>
          <button
            type="button"
            onClick={onClose}
            className="mt-1 text-xs text-memoir-text-muted hover:text-memoir-text-secondary"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
