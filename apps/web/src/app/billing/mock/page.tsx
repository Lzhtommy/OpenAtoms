"use client";
import { CreditCard } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const session = sp.get("session");
  const [info, setInfo] = useState<{ kind: string; item: string; amount_usd: number; status: string } | null>(null);
  useEffect(() => {
    if (session) api.get<typeof info>(`/billing/mock/${session}`).then(setInfo).catch(() => {});
  }, [session]);
  return (
    <AuthCard title="Mock checkout" subtitle="Stripe is not configured — this simulates a successful payment (dev only).">
      <div className="flex items-center gap-3 rounded-l border border-line bg-bg-2 p-3 text-[13px]">
        <CreditCard className="h-5 w-5 text-brand" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">
            {info ? `${info.kind === "plan" ? "Plan" : "Credits"}: ${info.item}` : "Order"}
            {info && <span className="ml-2 text-muted">${info.amount_usd}</span>}
          </div>
          <div className="truncate text-[11px] text-muted">session {session}</div>
        </div>
      </div>
      <div className="mt-5 flex gap-2">
        <Button variant="outline" className="flex-1" onClick={() => router.push("/pricing")}>
          Cancel
        </Button>
        <Button
          className="flex-1"
          loading={busy}
          disabled={!session || info?.status === "paid"}
          onClick={async () => {
            setBusy(true);
            try {
              await api.post("/billing/mock/complete", { session });
              await useAuth.getState().refreshBalance();
              await useAuth.getState().load();
              toast.success("Payment successful");
              router.push("/settings?tab=billing");
            } catch (e) {
              toast.error(errMsg(e));
              setBusy(false);
            }
          }}
        >
          Pay now
        </Button>
      </div>
    </AuthCard>
  );
}
export default function MockCheckout() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
