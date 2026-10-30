"use client";
import { Check } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Logo } from "@/components/shell/logo";
import { LangToggle, ThemeToggle } from "@/components/shell/prefs";
import { Button } from "@/components/ui/button";
import { api, errMsg, tokenStore } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface Plan {
  id: "free" | "pro" | "max";
  name: string;
  price_usd: number;
  monthly_credits: number;
  features: string[];
}

const PACKS = [
  { id: "pack_500", credits: 500, price: 5 },
  { id: "pack_2000", credits: 2000, price: 18 },
  { id: "pack_10000", credits: 10000, price: 80 },
];

export default function Pricing() {
  const router = useRouter();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const { user, balance, load } = useAuth();
  useEffect(() => {
    api.get<Plan[]>("/billing/plans").then(setPlans).catch(() => {});
    if (tokenStore.get() && !user) load();
  }, []);

  const checkout = async (body: { plan?: string; pack?: string }) => {
    if (!tokenStore.get()) return router.push("/login?next=/pricing");
    setBusy(body.plan ?? body.pack ?? "");
    try {
      const d = await api.post<{ url: string }>("/billing/checkout", body);
      location.href = d.url;
    } catch (e) {
      toast.error(errMsg(e));
      setBusy(null);
    }
  };

  const current = balance?.plan ?? user?.plan ?? "free";

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Logo href={user ? "/dashboard" : "/"} />
        <div className="flex items-center gap-1">
          <LangToggle />
          <ThemeToggle />
          {user ? (
            <Link href="/dashboard">
              <Button size="sm">Dashboard</Button>
            </Link>
          ) : (
            <Link href="/login">
              <Button size="sm">Log in</Button>
            </Link>
          )}
        </div>
      </header>
      <div className="mx-auto max-w-5xl px-6 pt-12 pb-24">
        <h1 className="text-center font-display text-[40px]">Simple, credit-based pricing</h1>
        <p className="mt-2 text-center text-[14px] text-muted">Credits are consumed by LLM tokens and sandbox time. Upgrade or top up anytime.</p>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {plans.map((p) => (
            <div key={p.id} className={cn("flex flex-col rounded-xl border bg-card p-6 shadow-card", p.id === "pro" ? "border-brand" : "border-line")}>
              <div className="flex items-center gap-2">
                <h2 className="text-[18px] font-semibold">{p.name}</h2>
                {p.id === "pro" && <span className="rounded-full bg-brand px-2 py-0.5 text-[10px] text-white">Popular</span>}
              </div>
              <div className="mt-3">
                <span className="font-display text-[36px]">${p.price_usd}</span>
                <span className="text-[13px] text-muted"> / month</span>
              </div>
              <div className="mt-1 text-[13px] text-fg-2">{p.monthly_credits.toLocaleString()} credits / month</div>
              <ul className="mt-5 flex flex-1 flex-col gap-2 text-[13px]">
                {p.features.map((f) => (
                  <li key={f} className="flex items-start gap-2">
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-positive" />
                    {f}
                  </li>
                ))}
              </ul>
              <Button
                className="mt-6 w-full"
                variant={p.id === "pro" ? "brand" : "primary"}
                disabled={current === p.id || p.id === "free"}
                loading={busy === p.id}
                onClick={() => checkout({ plan: p.id })}
              >
                {current === p.id ? "Current plan" : p.id === "free" ? "Free" : `Upgrade to ${p.name}`}
              </Button>
            </div>
          ))}
        </div>
        <h2 className="mt-16 text-center font-display text-[26px]">Top up credits</h2>
        <div className="mx-auto mt-6 grid max-w-3xl gap-4 sm:grid-cols-3">
          {PACKS.map((p) => (
            <div key={p.id} className="rounded-xl border border-line bg-card p-5 text-center shadow-card">
              <div className="text-[22px] font-semibold">{p.credits.toLocaleString()}</div>
              <div className="text-[12px] text-muted">credits</div>
              <Button className="mt-4 w-full" variant="outline" loading={busy === p.id} onClick={() => checkout({ pack: p.id })}>
                ${p.price}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
