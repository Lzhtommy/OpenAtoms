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

function RegisterInner() {
  const t = useT();
  const router = useRouter();
  const sp = useSearchParams();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 6) return toast.error("Password must be at least 6 characters");
    setLoading(true);
    try {
      const d = await api.post<{ access_token: string; user: User }>("/user/register", { email, password, name });
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
    <AuthCard title={t("createAccount")}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div>
          <Label>{t("name")}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
        <div>
          <Label>{t("email")}</Label>
          <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <Label>{t("password")}</Label>
          <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <Button type="submit" size="lg" className="mt-2 w-full" loading={loading}>
          {t("register")}
        </Button>
        <p className="mt-2 text-center text-[13px] text-muted">
          {t("hasAccount")}{" "}
          <Link href="/login" className="text-brand hover:underline">
            {t("login")}
          </Link>
        </p>
      </form>
    </AuthCard>
  );
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterInner />
    </Suspense>
  );
}
