"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useAutoSave } from "@/hooks/use-auto-save";
import { SaveStatusIndicator } from "./save-status";
import type { ActionItem } from "@/lib/types";

interface EditableActionItemsProps {
  meetingId: string;
  initialItems: ActionItem[];
  readOnly?: boolean;
}

export function EditableActionItems({
  meetingId,
  initialItems,
  readOnly = false,
}: EditableActionItemsProps) {
  const [items, setItems] = useState<ActionItem[]>(initialItems);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editText, setEditText] = useState("");

  const saveFn = useCallback(async () => {
    await api.editMemoir(meetingId, {
      action_items: items.map((a) => ({ text: a.text, type: a.type || "rep" })),
    });
  }, [meetingId, items]);

  const { status, trigger } = useAutoSave({ delay: 1000, onSave: saveFn });

  const startEdit = (index: number) => {
    setEditingIndex(index);
    setEditText(items[index].text);
  };

  const confirmEdit = (index: number) => {
    if (!editText.trim()) return;
    const next = [...items];
    next[index] = { ...next[index], text: editText.trim() };
    setItems(next);
    setEditingIndex(null);
    trigger();
  };

  const deleteItem = (index: number) => {
    const next = items.filter((_, i) => i !== index);
    setItems(next);
    trigger();
  };

  const addItem = (type: "rep" | "prospect") => {
    const next = [...items, { text: "New action item", type }];
    setItems(next);
    setEditingIndex(next.length - 1);
    setEditText("New action item");
    trigger();
  };

  const repItems = items.filter((a) => a.type === "rep");
  const prospectItems = items.filter((a) => a.type === "prospect");

  const renderItem = (item: ActionItem, globalIndex: number, isRep: boolean) => {
    const isEditing = editingIndex === globalIndex;

    return (
      <div key={globalIndex} className="flex items-start gap-2 mb-2 group">
        {isRep ? (
          <div className="w-4 h-4 mt-0.5 border-2 border-memoir-border rounded flex-shrink-0" />
        ) : (
          <span className="text-memoir-text-muted text-sm">&bull;</span>
        )}
        {isEditing ? (
          <div className="flex-1 flex items-center gap-1">
            <input
              autoFocus
              type="text"
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmEdit(globalIndex);
                if (e.key === "Escape") setEditingIndex(null);
              }}
              onBlur={() => confirmEdit(globalIndex)}
              className="flex-1 text-sm border border-memoir-indigo/30 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-memoir-indigo"
            />
          </div>
        ) : (
          <span
            className={`text-sm rounded px-1 -mx-1 ${
              isRep ? "text-memoir-text" : "text-memoir-text-secondary"
            } ${readOnly ? "" : "cursor-pointer hover:bg-memoir-bg/50"}`}
            onClick={readOnly ? undefined : () => startEdit(globalIndex)}
            title={readOnly ? undefined : "Click to edit"}
          >
            {item.text}
          </span>
        )}
        {!readOnly && (
          <button
            onClick={() => deleteItem(globalIndex)}
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

  // Build global index mapping
  const getGlobalIndex = (type: "rep" | "prospect", localIndex: number): number => {
    let count = 0;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type === type) {
        if (count === localIndex) return i;
        count++;
      }
    }
    return -1;
  };

  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-memoir-text">Action Items</h2>
          {!readOnly && <SaveStatusIndicator status={status} />}
        </div>
      </div>

      {repItems.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-2">Your to-dos</p>
          {repItems.map((item, localIdx) =>
            renderItem(item, getGlobalIndex("rep", localIdx), true),
          )}
        </div>
      )}

      {prospectItems.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-memoir-text-muted uppercase tracking-wider mb-2">Their commitments</p>
          {prospectItems.map((item, localIdx) =>
            renderItem(item, getGlobalIndex("prospect", localIdx), false),
          )}
        </div>
      )}

      {!readOnly && (
        <div className="flex items-center gap-2 pt-2 border-t border-memoir-border/60">
          <button
            onClick={() => addItem("rep")}
            className="text-xs text-memoir-indigo hover:text-memoir-indigo-hover transition-colors"
          >
            + Add to-do
          </button>
          <span className="text-memoir-border">|</span>
          <button
            onClick={() => addItem("prospect")}
            className="text-xs text-memoir-indigo hover:text-memoir-indigo-hover transition-colors"
          >
            + Add commitment
          </button>
        </div>
      )}
    </div>
  );
}
