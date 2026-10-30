"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { FullPageLoader } from "@/components/shell/auth-guard";
import { tokenStore } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  useEffect(() => {
    const token = sp.get("token");
    if (token) {
      tokenStore.set(token);
      useAuth
        .getState()
        .load()
        .then(() => router.replace("/dashboard"));
    } else router.replace("/login");
  }, []);
  return <FullPageLoader />;
}
export default function GoogleCallback() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
