"use client";

import { useState, useRef, useEffect } from "react";

// Subset of BCP-47 codes Whisper supports well. Matches the language
// list the TranscriptionBase prefers; auto-detect is the default and
// translates to NULL in the backend (multilingual).
const LANGUAGES: { code: string | null; label: string }[] = [
  { code: null, label: "English (Global)" },
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "ja", label: "Japanese" },
  { code: "zh", label: "Chinese" },
  { code: "hi", label: "Hindi" },
  { code: "ta", label: "Tamil" },
  { code: "ar", label: "Arabic" },
];

interface LanguagePickerProps {
  value: string | null;
  onChange: (code: string | null) => void;
  disabled?: boolean;
}

export function LanguagePicker({ value, onChange, disabled = false }: LanguagePickerProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", onDocClick);
      return () => document.removeEventListener("mousedown", onDocClick);
    }
  }, [open]);

  const selected = LANGUAGES.find((l) => l.code === value) ?? LANGUAGES[0];

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-sm text-memoir-text-secondary hover:text-memoir-text disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <circle cx="12" cy="12" r="9" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
        </svg>
        {selected.label}
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-44 bg-white border border-memoir-border rounded-lg shadow-elevated py-1 z-20">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code ?? "auto"}
              type="button"
              onClick={() => {
                onChange(lang.code);
                setOpen(false);
              }}
              className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-memoir-bg ${
                lang.code === value ? "text-memoir-indigo font-medium" : "text-memoir-text"
              }`}
            >
              {lang.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
