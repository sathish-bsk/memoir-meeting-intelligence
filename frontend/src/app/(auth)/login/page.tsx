"use client";

import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { AuthPanel } from "@/components/auth/auth-panel";

function LoginFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/20 border-t-[#5C5CE0]" />
    </div>
  );
}

export default function LoginPage() {
  return (
    <AuthShell>
      <Suspense fallback={<LoginFallback />}>
        <AuthPanel mode="signin" />
      </Suspense>
    </AuthShell>
  );
}
