"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useAutoSave } from "@/hooks/use-auto-save";
import { SaveStatusIndicator } from "./save-status";
import type { SignalData } from "@/lib/types";

interface EditableSignalsProps {
  meetingId: string;
  initialSignals: SignalData;
  readOnly?: boolean;
}

export function EditableSignals({
  meetingId,
  initialSignals,
  readOnly = false,
}: EditableSignalsProps) {
  const [buying, setBuying] = useState<string[]>(initialSignals.buying);
  const [risk, setRisk] = useState<string[]>(initialSignals.risk);
  const [editingKey, setEditingKey] = useState<{ type: "buying" | "risk"; index: number } | null>(null);
  const [editText, setEditText] = useState("");

  const saveFn = useCallback(async () => {
    await api.editMemoir(meetingId, {
      signals: { buying, risk },
    });
  }, [meetingId, buying, risk]);

  const { status, trigger } = useAutoSave({ delay: 1000, onSave: saveFn });

  const startEdit = (type: "buying" | "risk", index: number) => {
    const list = type === "buying" ? buying : risk;
    setEditingKey({ type, index });
    setEditText(list[index]);
  };

  const confirmEdit = () => {
    if (!editingKey || !editText.trim()) return;
    const { type, index } = editingKey;
    if (type === "buying") {
      const next = [...buying];
      next[index] = editText.trim();
      setBuying(next);
    } else {
      const next = [...risk];
      next[index] = editText.trim();
      setRisk(next);
    }
    setEditingKey(null);
    trigger();
  };

  const deleteSignal = (type: "buying" | "risk", index: number) => {
    if (type === "buying") {
      setBuying(buying.filter((_, i) => i !== index));
    } else {
      setRisk(risk.filter((_, i) => i !== index));
    }
    trigger();
  };

  const addSignal = (type: "buying" | "risk") => {
    const text = "New signal";
    if (type === "buying") {
      setBuying([...buying, text]);
      setEditingKey({ type, index: buying.length });
    } else {
      setRisk([...risk, text]);
      setEditingKey({ type, index: risk.length });
    }
    setEditText(text);
    trigger();
  };

  const hasSignals = buying.length > 0 || risk.length > 0;

  if (!hasSignals) return null;

  const renderSignal = (type: "buying" | "risk", text: string, index: number) => {
    const isBuying = type === "buying";
    const isEditing = editingKey?.type === type && editingKey.index === index;

    return (
      <div key={`${type}-${index}`} className="flex items-start gap-2 mb-1.5 group">
        <span className={`${isBuying ? "text-emerald-500" : "text-red-500"} text-xs mt-0.5`}>
          {isBuying ? "\u25B2" : "\u25BC"}
        </span>
        {isEditing ? (
          <input
            autoFocus
            type="text"
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") confirmEdit();
              if (e.key === "Escape") setEditingKey(null);
            }}
            onBlur={confirmEdit}
            className="flex-1 text-sm border border-memoir-indigo/30 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
          />
        ) : (
          <span
            className={`text-sm text-memoir-text-secondary rounded px-1 -mx-1 ${
              readOnly ? "" : "cursor-pointer hover:bg-memoir-bg/50"
            }`}
            onClick={readOnly ? undefined : () => startEdit(type, index)}
            title={readOnly ? undefined : "Click to edit"}
          >
            {text}
          </span>
        )}
        {!readOnly && (
          <button
            onClick={() => deleteSignal(type, index)}
            className="opacity-0 group-hover:opacity-100 text-memoir-text-muted hover:text-red-500 transition-all flex-shrink-0"
            title="Remove"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-memoir-text">Signals</h2>
          {!readOnly && <SaveStatusIndicator status={status} />}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {buying.length > 0 && (
          <div>
            <p className="text-xs font-medium text-emerald-600 uppercase tracking-wider mb-2">Buying signals</p>
            {buying.map((s, i) => renderSignal("buying", s, i))}
            {!readOnly && (
              <button
                onClick={() => addSignal("buying")}
                className="text-xs text-emerald-600 hover:text-emerald-700 transition-colors mt-1"
              >
                + Add signal
              </button>
            )}
          </div>
        )}

        {risk.length > 0 && (
          <div>
            <p className="text-xs font-medium text-red-600 uppercase tracking-wider mb-2">Risk signals</p>
            {risk.map((s, i) => renderSignal("risk", s, i))}
            {!readOnly && (
              <button
                onClick={() => addSignal("risk")}
                className="text-xs text-red-600 hover:text-red-700 transition-colors mt-1"
              >
                + Add signal
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
