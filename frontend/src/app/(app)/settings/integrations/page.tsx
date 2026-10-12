"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function RedirectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const params = searchParams.toString();
    router.replace(`/integrations${params ? `?${params}` : ""}`);
  }, [router, searchParams]);

  return null;
}

export default function IntegrationsRedirect() {
  return (
    <Suspense fallback={null}>
      <RedirectContent />
    </Suspense>
  );
}
