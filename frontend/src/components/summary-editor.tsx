"use client";

import { useState, useCallback, useRef, type MutableRefObject } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import StarterKit from "@tiptap/starter-kit";
import Highlight from "@tiptap/extension-highlight";
import Placeholder from "@tiptap/extension-placeholder";
import { api } from "@/lib/api";
import { useAutoSave } from "@/hooks/use-auto-save";
import { SaveStatusIndicator } from "./save-status";
import { EditedBadge } from "./edited-badge";

interface SummaryEditorProps {
  meetingId: string;
  initialContent: string | null;
  isEdited?: boolean;
  onReset?: () => Promise<void>;
}

export function SummaryEditor({
  meetingId,
  initialContent,
  isEdited = false,
  onReset,
}: SummaryEditorProps) {
  const [edited, setEdited] = useState(isEdited);
  const [resetting, setResetting] = useState(false);

  // Use a ref so the save callback always has the current editor instance
  const editorRef = useRef<ReturnType<typeof useEditor>>(null) as MutableRefObject<ReturnType<typeof useEditor> | null>;

  const saveFn = useCallback(async () => {
    const ed = editorRef.current;
    if (!ed) return;
    // Convert HTML back to plain text (paragraphs separated by double newlines)
    const html = ed.getHTML();
    const div = document.createElement("div");
    div.innerHTML = html;
    const paragraphs = Array.from(div.querySelectorAll("p"))
      .map((p) => p.textContent || "")
      .filter((t) => t.trim());
    const plainText = paragraphs.join("\n\n");
    await api.editMemoir(meetingId, { narrative_summary: plainText });
  }, [meetingId]);

  const { status, trigger } = useAutoSave({ delay: 1500, onSave: saveFn });

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        codeBlock: false,
        code: false,
        horizontalRule: false,
        listItem: false,
        bulletList: false,
        orderedList: false,
      }),
      Highlight.configure({ multicolor: false }),
      Placeholder.configure({ placeholder: "No summary available\u2026" }),
    ],
    content: initialContent
      ? initialContent
          .split("\n\n")
          .map((p) => `<p>${p}</p>`)
          .join("")
      : "",
    editorProps: {
      attributes: {
        class:
          "prose prose-sm max-w-none focus:outline-none text-sm text-memoir-text-secondary leading-relaxed [&_p]:mb-3 [&_p:last-child]:mb-0 [&_mark]:bg-yellow-200",
      },
    },
    onUpdate: () => {
      setEdited(true);
      trigger();
    },
  });

  // Keep the ref in sync
  editorRef.current = editor;

  const handleReset = async () => {
    if (!onReset || resetting) return;
    setResetting(true);
    try {
      await onReset();
      setEdited(false);
    } catch {
      // reset failed — stay in edited state
    } finally {
      setResetting(false);
    }
  };

  if (!editor) return null;

  return (
    <div className="bg-memoir-surface border border-memoir-border rounded-card p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-memoir-text">Summary</h2>
          <SaveStatusIndicator status={status} />
        </div>
        {edited && (
          <div className="flex items-center gap-2">
            <EditedBadge />
            {onReset && (
              <button
                onClick={handleReset}
                disabled={resetting}
                className="text-xs text-memoir-text-muted hover:text-memoir-indigo transition-colors disabled:opacity-50"
              >
                {resetting ? "Resetting..." : "Reset to original"}
              </button>
            )}
          </div>
        )}
      </div>

      <BubbleMenu editor={editor}>
        <div className="flex items-center gap-0.5 bg-memoir-surface border border-memoir-border rounded-lg shadow-elevated px-1 py-0.5">
          <button
            onClick={() => editor.chain().focus().toggleBold().run()}
            className={`px-2 py-1 text-xs font-bold rounded transition-colors ${
              editor.isActive("bold")
                ? "bg-memoir-indigo/10 text-memoir-indigo"
                : "text-memoir-text-secondary hover:bg-memoir-bg"
            }`}
          >
            B
          </button>
          <button
            onClick={() => editor.chain().focus().toggleHighlight().run()}
            className={`px-2 py-1 text-xs rounded transition-colors ${
              editor.isActive("highlight")
                ? "bg-yellow-200 text-amber-800"
                : "text-memoir-text-secondary hover:bg-memoir-bg"
            }`}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10" />
            </svg>
          </button>
        </div>
      </BubbleMenu>

      <EditorContent editor={editor} />
    </div>
  );
}
