import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const tagVariants = cva(
  "inline-flex items-center whitespace-nowrap rounded-md px-2 py-[3px] text-[10.5px] font-bold",
  {
    variants: {
      tone: {
        brand: "bg-brand-light text-brand",
        orange: "bg-brand-orange-light text-brand-orange",
        green: "bg-brand-green-light text-brand-green",
        amber: "bg-brand-amber-light text-brand-amber",
        red: "bg-brand-red-light text-brand-red",
        blue: "bg-brand-blue-light text-brand-blue",
        neutral: "bg-surface-soft text-ink-secondary",
      },
    },
    defaultVariants: {
      tone: "brand",
    },
  },
);

export interface TagProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof tagVariants> {
  label: React.ReactNode;
}

export function Tag({ label, tone, className, ...props }: TagProps) {
  return (
    <span className={cn(tagVariants({ tone }), className)} {...props}>
      {label}
    </span>
  );
}

// Heuristic mapping from meeting-tag text to a tone. Centralized so list/peek/detail surfaces agree.
export function tagToneFor(tag: string): VariantProps<typeof tagVariants>["tone"] {
  const lower = tag.toLowerCase();
  if (lower.includes("action")) return "orange";
  if (lower.includes("competitor") || lower.includes("budget") || lower.includes("objection")) return "red";
  if (lower.includes("hot") || lower.includes("renewal") || lower.includes("buying")) return "green";
  return "brand";
}
