"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { AuthShell } from "@/components/auth/auth-shell";
import { AuthCard } from "@/components/auth/auth-card";

function VerifyEmailContent() {
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
      .verifyEmail(token)
      .then((res) => {
        setStatus("success");
        setMessage(res.message);
      })
      .catch((err) => {
        setStatus("error");
        setMessage(
          err instanceof ApiError
            ? err.message
            : "Failed to verify email. The link may have expired.",
        );
      });
  }, [token]);

  if (status === "loading") {
    return <AuthCard state="loading" message="Verifying your email..." />;
  }
  if (status === "success") {
    return (
      <AuthCard
        state="success"
        title="Email verified"
        message={message}
        primaryCta={{ label: "Continue to app", href: "/home" }}
      />
    );
  }
  return (
    <AuthCard
      state="error"
      title="Verification failed"
      message={message}
      secondaryCta={{ label: "Go to login", href: "/login" }}
    />
  );
}

export default function VerifyEmailPage() {
  return (
    <AuthShell>
      <Suspense fallback={<AuthCard state="loading" message="Verifying your email..." />}>
        <VerifyEmailContent />
      </Suspense>
    </AuthShell>
  );
}
