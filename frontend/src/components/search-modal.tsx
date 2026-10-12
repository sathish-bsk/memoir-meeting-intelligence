"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { useAuthStore } from "@/store/auth";
import { api } from "@/lib/api";
import type { EnhancedSearchResult, EnhancedSearchResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Recent searches (localStorage)
// ---------------------------------------------------------------------------

function getRecentSearches(userId: string): string[] {
  try {
    const raw = localStorage.getItem(`memoir_recent_searches_${userId}`);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveRecentSearch(userId: string, query: string) {
  const existing = getRecentSearches(userId);
  const filtered = existing.filter((s) => s !== query);
  const updated = [query, ...filtered].slice(0, 10);
  localStorage.setItem(`memoir_recent_searches_${userId}`, JSON.stringify(updated));
}

function removeRecentSearch(userId: string, query: string) {
  const existing = getRecentSearches(userId);
  const updated = existing.filter((s) => s !== query);
  localStorage.setItem(`memoir_recent_searches_${userId}`, JSON.stringify(updated));
}

function clearRecentSearches(userId: string) {
  localStorage.removeItem(`memoir_recent_searches_${userId}`);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SearchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SearchModal({ open, onOpenChange }: SearchModalProps) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const activeTeamId = useAuthStore((s) => s.activeTeamId);
  const inputRef = useRef<HTMLInputElement>(null);

  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<EnhancedSearchResponse | null>(null);
  const [recentSearches, setRecentSearches] = useState<string[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [viewMode, setViewMode] = useState<"recent" | "results">("recent");

  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  // Load recent searches when modal opens
  useEffect(() => {
    if (open && user?.id) {
      setRecentSearches(getRecentSearches(user.id));
      setQuery("");
      setData(null);
      setViewMode("recent");
      setSelectedIndex(0);
      // Focus input after dialog animation
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open, user?.id]);

  // Debounced search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (!query.trim()) {
      setData(null);
      setViewMode("recent");
      setSelectedIndex(0);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      if (!activeTeamId) return;
      setLoading(true);
      try {
        const res = await api.search(query.trim(), activeTeamId, { perPage: 5 });
        setData(res);
        setViewMode("results");
        setSelectedIndex(0);
      } catch {
        setData(null);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, activeTeamId]);

  // Build flat list of selectable items for keyboard nav
  const getSelectableItems = useCallback((): Array<{
    type: "recent" | "call" | "transcript" | "view-all-call" | "view-all-transcript";
    value: string | EnhancedSearchResult;
  }> => {
    if (viewMode === "recent") {
      return recentSearches.map((s) => ({ type: "recent" as const, value: s }));
    }
    if (!data) return [];
    const items: Array<{
      type: "recent" | "call" | "transcript" | "view-all-call" | "view-all-transcript";
      value: string | EnhancedSearchResult;
    }> = [];
    for (const r of data.call_results) {
      items.push({ type: "call", value: r });
    }
    if (data.call_total > data.call_results.length) {
      items.push({ type: "view-all-call", value: `View all ${data.call_total} results` });
    }
    for (const r of data.transcript_results) {
      items.push({ type: "transcript", value: r });
    }
    if (data.transcript_total > data.transcript_results.length) {
      items.push({ type: "view-all-transcript", value: `View all ${data.transcript_total} results` });
    }
    return items;
  }, [viewMode, recentSearches, data]);

  const items = getSelectableItems();

  // Navigate to a result
  const navigateTo = useCallback(
    (result: EnhancedSearchResult) => {
      if (user?.id && query.trim()) {
        saveRecentSearch(user.id, query.trim());
      }
      // Store search context so call detail Back button returns to search results
      sessionStorage.setItem("memoir_search_back", query.trim());
      const path =
        result.timestamp_seconds != null
          ? `/meetings/${result.meeting_id}?t=${result.timestamp_seconds}`
          : `/meetings/${result.meeting_id}`;
      onOpenChange(false);
      router.push(path);
    },
    [user?.id, query, onOpenChange, router],
  );

  // Keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, items.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter" && items.length > 0) {
        e.preventDefault();
        const item = items[selectedIndex];
        if (!item) return;
        if (item.type === "recent") {
          setQuery(item.value as string);
        } else if (item.type === "view-all-call" || item.type === "view-all-transcript") {
          if (user?.id && query.trim()) saveRecentSearch(user.id, query.trim());
          onOpenChange(false);
          router.push(`/meetings?search=${encodeURIComponent(query.trim())}`);
        } else {
          navigateTo(item.value as EnhancedSearchResult);
        }
      }
    },
    [items, selectedIndex, navigateTo, user?.id, query, onOpenChange, router],
  );

  // Scroll selected item into view
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector(`[data-index="${selectedIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);


  // const hasNoResults =
  //   viewMode === "results" && data && data.call_total === 0 && data.transcript_total === 0;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-[50%] top-[20%] z-[60] w-full max-w-xl translate-x-[-50%] bg-memoir-surface border border-memoir-border rounded-xl shadow-elevated overflow-hidden focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=open]:slide-in-from-left-1/2"
          onKeyDown={handleKeyDown}
        >
          {/* Search input */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-memoir-border">
            <SearchIcon className="w-[18px] h-[18px] text-memoir-text-muted flex-shrink-0" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search meetings, transcripts..."
              className="flex-1 bg-transparent text-sm text-memoir-text placeholder:text-memoir-text-muted outline-none"
              autoComplete="off"
              spellCheck={false}
            />
            {loading && (
              <div className="w-4 h-4 border-2 border-memoir-border border-t-memoir-indigo rounded-full animate-spin flex-shrink-0" />
            )}
            <Dialog.Close asChild>
              <kbd className="text-[10px] font-mono text-memoir-text-muted bg-memoir-bg border border-memoir-border rounded px-1.5 py-0.5 cursor-pointer hover:bg-memoir-border transition-colors">
                Esc
              </kbd>
            </Dialog.Close>
          </div>

          {/* Results area */}
          <div
            ref={listRef}
            className="max-h-[400px] overflow-y-auto"

          >
            {/* View: Recent searches */}
            {viewMode === "recent" && (
              <div className="py-2">
                {recentSearches.length > 0 ? (
                  <>
                    <div className="flex items-center justify-between px-4 py-1.5">
                      <span className="text-[11px] font-semibold uppercase text-memoir-text-muted tracking-wider">
                        Recent searches
                      </span>
                      <button
                        onClick={() => {
                          if (user?.id) {
                            clearRecentSearches(user.id);
                            setRecentSearches([]);
                          }
                        }}
                        className="text-[11px] text-memoir-text-muted hover:text-memoir-indigo transition-colors"
                      >
                        Clear all
                      </button>
                    </div>
                    {recentSearches.map((s, i) => (
                      <div
                        key={s}
                        data-index={i}
                        className={`flex items-center gap-3 px-4 py-2 cursor-pointer transition-colors ${
                          selectedIndex === i
                            ? "bg-memoir-indigo/5 border-l-2 border-memoir-indigo"
                            : "border-l-2 border-transparent hover:bg-memoir-bg"
                        }`}
                        onClick={() => setQuery(s)}
                      >
                        <ClockIcon className="w-3.5 h-3.5 text-memoir-text-muted flex-shrink-0" />
                        <span className="flex-1 text-sm text-memoir-text truncate">{s}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (user?.id) {
                              removeRecentSearch(user.id, s);
                              setRecentSearches((prev) => prev.filter((x) => x !== s));
                            }
                          }}
                          className="text-memoir-text-muted hover:text-memoir-text transition-colors p-0.5"
                        >
                          <XIcon className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </>
                ) : (
                  <div className="px-4 py-8 text-center text-sm text-memoir-text-muted">
                    Start typing to search calls and transcripts
                  </div>
                )}
              </div>
            )}

            {/* View: Grouped results */}
            {viewMode === "results" && data && (
              <div className="py-2">
                {/* Call results — always show header */}
                <div className="px-4 py-1.5">
                  <span className="text-[11px] font-semibold uppercase text-memoir-text-muted tracking-wider">
                    Calls ({data.call_total})
                  </span>
                </div>
                {data.call_results.map((r, i) => (
                  <ResultItem
                    key={`call-${r.meeting_id}-${i}`}
                    result={r}
                    index={i}
                    selected={selectedIndex === i}
                    onClick={() => navigateTo(r)}
                  />
                ))}
                {data.call_total > data.call_results.length && (
                  <ViewAllLink
                    index={data.call_results.length}
                    selected={selectedIndex === data.call_results.length}
                    total={data.call_total}
                    onClick={() => {
                      if (user?.id && query.trim()) saveRecentSearch(user.id, query.trim());
                      onOpenChange(false);
                      router.push(`/meetings?search=${encodeURIComponent(query.trim())}`);
                    }}
                  />
                )}
                {data.call_total === 0 && (
                  <div className="px-4 py-2 text-xs text-memoir-text-muted">No matching calls</div>
                )}

                {/* Transcript results — always show header */}
                <div className="px-4 py-1.5 mt-1">
                  <span className="text-[11px] font-semibold uppercase text-memoir-text-muted tracking-wider">
                    Transcripts ({data.transcript_total})
                  </span>
                </div>
                {(() => {
                  const callOffset =
                    data.call_results.length +
                    (data.call_total > data.call_results.length ? 1 : 0);
                  return (
                    <>
                      {data.transcript_results.map((r, i) => (
                        <ResultItem
                          key={`tx-${r.meeting_id}-${i}`}
                          result={r}
                          index={callOffset + i}
                          selected={selectedIndex === callOffset + i}
                          onClick={() => navigateTo(r)}
                        />
                      ))}
                      {data.transcript_total > data.transcript_results.length && (
                        <ViewAllLink
                          index={callOffset + data.transcript_results.length}
                          selected={
                            selectedIndex === callOffset + data.transcript_results.length
                          }
                          total={data.transcript_total}
                          onClick={() => {
                            if (user?.id && query.trim()) saveRecentSearch(user.id, query.trim());
                            onOpenChange(false);
                            router.push(`/meetings?search=${encodeURIComponent(query.trim())}`);
                          }}
                        />
                      )}
                      {data.transcript_total === 0 && (
                        <div className="px-4 py-2 text-xs text-memoir-text-muted">No matching transcripts</div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}

          </div>

          {/* Footer */}
          <div className="flex items-center gap-4 px-4 py-2 border-t border-memoir-border text-[10px] text-memoir-text-muted">
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-memoir-bg border border-memoir-border rounded px-1 py-0.5">
                &uarr;&darr;
              </kbd>
              Navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-memoir-bg border border-memoir-border rounded px-1 py-0.5">
                &crarr;
              </kbd>
              Open
            </span>
            <span className="flex items-center gap-1">
              <kbd className="font-mono bg-memoir-bg border border-memoir-border rounded px-1 py-0.5">
                esc
              </kbd>
              Close
            </span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ResultItem({
  result,
  index,
  selected,
  onClick,
}: {
  result: EnhancedSearchResult;
  index: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      data-index={index}
      className={`flex items-start gap-3 px-4 py-2.5 cursor-pointer transition-colors ${
        selected
          ? "bg-memoir-indigo/5 border-l-2 border-memoir-indigo"
          : "border-l-2 border-transparent hover:bg-memoir-bg"
      }`}
      onClick={onClick}
    >
      <div className="mt-0.5 flex-shrink-0">
        {result.result_type === "call" ? (
          <PhoneIcon className="w-3.5 h-3.5 text-memoir-text-muted" />
        ) : (
          <TranscriptIcon className="w-3.5 h-3.5 text-memoir-text-muted" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-memoir-text truncate">
            {result.title || "Untitled Call"}
          </span>
          {result.prospect_name && (
            <span className="text-xs text-memoir-text-muted truncate hidden sm:inline">
              {result.prospect_name}
              {result.prospect_company ? ` - ${result.prospect_company}` : ""}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {result.result_type === "transcript" && (
            <p
              className="text-xs text-memoir-text-secondary leading-relaxed line-clamp-2 flex-1"
              dangerouslySetInnerHTML={{ __html: result.snippet }}
            />
          )}
          {result.result_type === "call" && (
            <p className="text-xs text-memoir-text-secondary leading-relaxed line-clamp-1 flex-1">
              {result.snippet}
            </p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 mt-0.5">
        {result.timestamp_seconds != null && (
          <span className="text-[11px] font-mono text-memoir-indigo bg-memoir-indigo/10 rounded px-1.5 py-0.5">
            {formatTimestamp(result.timestamp_seconds)}
          </span>
        )}
        {result.date && (
          <span className="text-[11px] text-memoir-text-muted whitespace-nowrap">
            {formatDate(result.date)}
          </span>
        )}
      </div>
    </div>
  );
}

function ViewAllLink({
  index,
  selected,
  total,
  onClick,
}: {
  index: number;
  selected: boolean;
  total: number;
  onClick: () => void;
}) {
  return (
    <div
      data-index={index}
      className={`px-4 py-1.5 cursor-pointer transition-colors ${
        selected ? "bg-memoir-indigo/5" : "hover:bg-memoir-bg"
      }`}
      onClick={onClick}
    >
      <span className="text-xs text-memoir-indigo hover:underline">
        View all {total} results &rarr;
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
    </svg>
  );
}

function ClockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  );
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
    </svg>
  );
}

function PhoneIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6.75c0 8.284 6.716 15 15 15h2.25a2.25 2.25 0 0 0 2.25-2.25v-1.372c0-.516-.351-.966-.852-1.091l-4.423-1.106c-.44-.11-.902.055-1.173.417l-.97 1.293c-.282.376-.769.542-1.21.38a12.035 12.035 0 0 1-7.143-7.143c-.162-.441.004-.928.38-1.21l1.293-.97c.363-.271.527-.734.417-1.173L6.963 3.102a1.125 1.125 0 0 0-1.091-.852H4.5A2.25 2.25 0 0 0 2.25 4.5v2.25Z" />
    </svg>
  );
}

function TranscriptIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H7.5m0 8.25h7.5M7.5 15.75h7.5M7.5 12h3m-6.375 9.75h10.5a2.25 2.25 0 0 0 2.25-2.25V6.108c0-.597-.237-1.17-.659-1.591l-2.858-2.859a2.25 2.25 0 0 0-1.591-.658H5.25a2.25 2.25 0 0 0-2.25 2.25v14.25a2.25 2.25 0 0 0 2.25 2.25Z" />
    </svg>
  );
}

