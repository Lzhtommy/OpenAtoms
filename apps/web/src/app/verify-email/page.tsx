"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { api, errMsg } from "@/lib/api";

function Inner() {
  const sp = useSearchParams();
  const [state, setState] = useState<"loading" | "ok" | string>("loading");
  useEffect(() => {
    const token = sp.get("token");
    if (!token) return setState("Missing token");
    api
      .post("/user/activation", { token })
      .then(() => setState("ok"))
      .catch((e) => setState(errMsg(e)));
  }, []);
  return (
    <AuthCard title="Email verification">
      <p className="text-[13px] text-fg-2">
        {state === "loading" ? "Verifying…" : state === "ok" ? "Your email has been verified." : state}
      </p>
      <Link href="/dashboard" className="mt-4 inline-block text-[13px] text-brand hover:underline">
        Continue →
      </Link>
    </AuthCard>
  );
}
export default function VerifyPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
