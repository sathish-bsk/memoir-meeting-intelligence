"use client";

export function EditedBadge({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700 ${className}`}
    >
      Edited
    </span>
  );
}
