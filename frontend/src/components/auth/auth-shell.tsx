"use client";

/**
 * Full-bleed dark shell for every page under (auth)/. Renders the sonar
 * canvas + gradient overlay + bottom-left brand watermark, with a
 * children slot for the page-specific content (AuthPanel or AuthCard).
 *
 * The keyframes used across auth pages are injected here once so each
 * page doesn't have to duplicate the <style> block.
 */

import Image from "next/image";
import { SonarCanvas } from "./sonar-canvas";

const KEYFRAMES = `
@keyframes auth-fade-up { from{opacity:0;transform:translateY(24px)} to{opacity:1;transform:translateY(0)} }
@keyframes auth-fade-in { from{opacity:0} to{opacity:1} }
@keyframes auth-float-y { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-6px)} }
@keyframes auth-glow-pulse { 0%,100%{box-shadow:0 0 30px rgba(92,92,224,0.15)} 50%{box-shadow:0 0 60px rgba(92,92,224,0.3)} }
`;

export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{ background: "#060610" }}
      className="relative min-h-screen overflow-hidden font-sans text-[#1B1B1F]"
    >
      <style>{KEYFRAMES}</style>

      {/* Animated background */}
      <SonarCanvas />

      {/* Vignette overlay — keeps form-side content legible */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-[1]"
        style={{
          background:
            "linear-gradient(110deg, rgba(6,6,16,0.4) 0%, rgba(6,6,16,0.7) 45%, rgba(6,6,16,0.95) 70%, rgba(6,6,16,0.98) 100%)",
        }}
      />

      {/* Content slot */}
      <div className="relative z-[2] min-h-screen">{children}</div>

      {/* Bottom-left brand watermark */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute bottom-5 left-[60px] z-[2] hidden items-center gap-2 opacity-30 sm:flex"
      >
        <Image src="/logo-white.svg" alt="" width={70} height={16} />
        <span className="text-[11px] font-medium text-white">
          Meeting intelligence, unfiltered
        </span>
      </div>
    </div>
  );
}
