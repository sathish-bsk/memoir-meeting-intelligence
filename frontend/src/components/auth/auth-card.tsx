"use client";

/**
 * Compact centered status card used by /join (trampoline), /verify-email,
 * and /verify-email-change. Always shows the brand mark, a state-driven
 * icon (loading/success/error), title, body message, and an optional
 * primary CTA.
 *
 * `extra` slot is provided for the manual-code-entry form on /join
 * (input + button) so we keep that single use case without inventing
 * a new component.
 */

import Link from "next/link";
import Image from "next/image";

type State = "loading" | "success" | "error";

interface CtaProps {
  label: string;
  href?: string;
  onClick?: () => void;
}

interface AuthCardProps {
  state: State;
  title?: string;
  message?: string;
  primaryCta?: CtaProps;
  secondaryCta?: CtaProps;
  extra?: React.ReactNode;
}

function StateIcon({ state }: { state: State }) {
  if (state === "loading") {
    return (
      <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-white/20 border-t-[#5C5CE0]" />
    );
  }
  if (state === "success") {
    return (
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
        <svg className="h-6 w-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      </div>
    );
  }
  return (
    <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-100">
      <svg className="h-6 w-6 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </div>
  );
}

function CtaButton({ cta, primary }: { cta: CtaProps; primary: boolean }) {
  const className = primary
    ? "inline-block w-full rounded-[10px] bg-[#5C5CE0] px-4 py-2.5 text-center text-sm font-bold text-white shadow-[0_4px_16px_rgba(92,92,224,0.35)] transition-colors hover:bg-[#4848B8]"
    : "inline-block w-full rounded-[10px] px-4 py-2 text-center text-xs font-medium text-[#7C7C8A] transition-colors hover:text-[#1B1B1F]";

  if (cta.href) {
    return (
      <Link href={cta.href} className={className}>
        {cta.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={cta.onClick} className={className}>
      {cta.label}
    </button>
  );
}

export function AuthCard({ state, title, message, primaryCta, secondaryCta, extra }: AuthCardProps) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-10">
      <div
        className="w-full max-w-[400px] rounded-[20px] px-8 py-9 text-center"
        style={{
          background: "rgba(255,255,255,0.97)",
          backdropFilter: "blur(20px)",
          boxShadow:
            "0 20px 60px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.1)",
          animation: "auth-fade-up 0.7s ease 0.15s both",
        }}
      >
        {/* Brand wordmark — in-product asset */}
        <div className="mb-6 flex items-center justify-center gap-1.5">
          <Image src="/logo-icon.svg" alt="" width={36} height={36} className="rounded-md" priority />
          <Image src="/logo.svg" alt="Memoir" width={110} height={26} priority />
        </div>

        <div role={state === "loading" ? undefined : "status"} aria-live="polite">
          <StateIcon state={state} />
          {title && (
            <h2 className="mb-2 text-[18px] font-extrabold tracking-[-0.3px] text-[#1B1B1F]">
              {title}
            </h2>
          )}
          {message && (
            <p className="mb-6 text-[13.5px] font-[450] leading-[1.6] text-[#7C7C8A]">
              {message}
            </p>
          )}
        </div>

        {extra && <div className="mb-4">{extra}</div>}

        {primaryCta && (
          <div className="mb-2">
            <CtaButton cta={primaryCta} primary />
          </div>
        )}
        {secondaryCta && <CtaButton cta={secondaryCta} primary={false} />}
      </div>
    </div>
  );
}
