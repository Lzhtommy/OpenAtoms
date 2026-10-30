"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, errMsg, tokenStore } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import type { User } from "@/lib/types";

function GoogleButton() {
  return (
    <Button
      type="button"
      variant="outline"
      className="w-full"
      onClick={async () => {
        try {
          const d = await api.get<{ url: string }>("/user/google/login");
          location.href = d.url;
        } catch (e) {
          toast.error(errMsg(e));
        }
      }}
    >
      <svg className="h-4 w-4" viewBox="0 0 48 48">
        <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
        <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
        <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
        <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
      </svg>
      Google
    </Button>
  );
}

function LoginInner() {
  const t = useT();
  const router = useRouter();
  const sp = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const d = await api.post<{ access_token: string; user: User }>("/user/login", { email, password });
      tokenStore.set(d.access_token);
      await useAuth.getState().load();
      router.replace(sp.get("next") || "/dashboard");
    } catch (err) {
      toast.error(errMsg(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthCard title={t("welcomeBack")}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div>
          <Label>{t("email")}</Label>
          <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </div>
        <div>
          <div className="flex items-center justify-between">
            <Label>{t("password")}</Label>
            <Link href="/password/forgot" className="mb-1.5 text-[12px] text-brand hover:underline">
              {t("forgot")}
            </Link>
          </div>
          <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <Button type="submit" size="lg" className="mt-2 w-full" loading={loading}>
          {t("login")}
        </Button>
        <div className="my-1 flex items-center gap-3 text-[11px] text-subtle">
          <div className="h-px flex-1 bg-line" />
          OR
          <div className="h-px flex-1 bg-line" />
        </div>
        <GoogleButton />
        <p className="mt-2 text-center text-[13px] text-muted">
          {t("noAccount")}{" "}
          <Link href={`/register${sp.get("next") ? `?next=${encodeURIComponent(sp.get("next")!)}` : ""}`} className="text-brand hover:underline">
            {t("register")}
          </Link>
        </p>
      </form>
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginInner />
    </Suspense>
  );
}
