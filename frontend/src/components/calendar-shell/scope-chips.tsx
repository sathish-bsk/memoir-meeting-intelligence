"use client";

/**
 * ScopeChips — the [All · Hosted by me · I am a participant] row on both
 * the Meetings v3 and Events v3 tabs.
 *
 * Plan: plan/meetings-events-v3-list.md §5.3 + §11.1.
 * - Default value: "hosted" for ALL roles (locked decision §11.1).
 * - `All` chip is hidden when userRole === "member" (locked §11.3).
 */

export type ScopeValue = "hosted" | "participant" | "all";

export interface ScopeChipsProps {
  value: ScopeValue;
  onChange: (v: ScopeValue) => void;
  userRole: "owner" | "admin" | "member";
}

interface ChipDef {
  value: ScopeValue;
  label: string;
  adminOnly?: boolean;
}

const CHIPS: ChipDef[] = [
  { value: "hosted", label: "Hosted by me" },
  { value: "participant", label: "I am a participant" },
  { value: "all", label: "All", adminOnly: true },
];

export function ScopeChips({ value, onChange, userRole }: ScopeChipsProps) {
  const isAdmin = userRole === "owner" || userRole === "admin";
  const visibleChips = CHIPS.filter((c) => !c.adminOnly || isAdmin);

  return (
    <div className="inline-flex rounded-md border border-memoir-border bg-white p-0.5">
      {visibleChips.map((chip) => (
        <button
          key={chip.value}
          type="button"
          onClick={() => onChange(chip.value)}
          className={
            "px-3 py-1 rounded text-xs font-medium transition-colors " +
            (value === chip.value
              ? "bg-memoir-indigo text-white"
              : "text-memoir-text-muted hover:text-memoir-text")
          }
        >
          {chip.label}
        </button>
      ))}
    </div>
  );
}
