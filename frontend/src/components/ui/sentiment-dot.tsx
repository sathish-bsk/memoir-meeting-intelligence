import * as React from "react";

import { cn } from "@/lib/utils";

export type Sentiment = "positive" | "neutral" | "negative";

const SENTIMENT_TONE: Record<Sentiment, string> = {
  positive: "bg-brand-green",
  neutral: "bg-brand-amber",
  negative: "bg-brand-red",
};

interface SentimentDotProps extends React.HTMLAttributes<HTMLSpanElement> {
  sentiment: Sentiment;
  size?: "sm" | "md";
}

export function SentimentDot({ sentiment, size = "sm", className, ...props }: SentimentDotProps) {
  const dim = size === "md" ? "h-2.5 w-2.5" : "h-2 w-2";
  return (
    <span
      role="img"
      aria-label={`sentiment ${sentiment}`}
      className={cn("inline-block shrink-0 rounded-full", dim, SENTIMENT_TONE[sentiment], className)}
      {...props}
    />
  );
}
