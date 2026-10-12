"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { AuthShell } from "@/components/auth/auth-shell";
import { AuthCard } from "@/components/auth/auth-card";

function VerifyEmailChangeContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");
  const calledRef = useRef(false);

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("Missing verification token");
      return;
    }
    if (calledRef.current) return;
    calledRef.current = true;

    api
      .verifyEmailChange(token)
      .then((res) => {
        setStatus("success");
        setMessage(res.message);
      })
      .catch((err) => {
        setStatus("error");
        setMessage(
          err instanceof ApiError
            ? err.message
            : "Failed to verify email change. The link may have expired.",
        );
      });
  }, [token]);

  if (status === "loading") {
    return <AuthCard state="loading" message="Confirming your new email..." />;
  }
  if (status === "success") {
    return (
      <AuthCard
        state="success"
        title="Email changed"
        message={message}
        primaryCta={{ label: "Go to profile", href: "/settings/profile" }}
      />
    );
  }
  return (
    <AuthCard
      state="error"
      title="Verification failed"
      message={message}
      secondaryCta={{ label: "Go to settings", href: "/settings/profile" }}
    />
  );
}

export default function VerifyEmailChangePage() {
  return (
    <AuthShell>
      <Suspense fallback={<AuthCard state="loading" message="Confirming your new email..." />}>
        <VerifyEmailChangeContent />
      </Suspense>
    </AuthShell>
  );
}
