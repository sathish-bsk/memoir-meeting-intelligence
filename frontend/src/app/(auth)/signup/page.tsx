"use client";

import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { AuthPanel } from "@/components/auth/auth-panel";

function SignupFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/20 border-t-[#5C5CE0]" />
    </div>
  );
}

export default function SignupPage() {
  return (
    <AuthShell>
      <Suspense fallback={<SignupFallback />}>
        <AuthPanel mode="signup" />
      </Suspense>
    </AuthShell>
  );
}
