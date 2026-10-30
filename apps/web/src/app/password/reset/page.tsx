"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, errMsg } from "@/lib/api";

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);
  return (
    <AuthCard title="Choose a new password">
      <form
        className="flex flex-col gap-3"
        onSubmit={async (e) => {
          e.preventDefault();
          setLoading(true);
          try {
            await api.post("/user/pwd/reset", { token: sp.get("token"), password: pw });
            toast.success("Password updated");
            router.replace("/login");
          } catch (err) {
            toast.error(errMsg(err));
          } finally {
            setLoading(false);
          }
        }}
      >
        <div>
          <Label>New password</Label>
          <Input type="password" minLength={6} required value={pw} onChange={(e) => setPw(e.target.value)} />
        </div>
        <Button type="submit" size="lg" loading={loading}>
          Update password
        </Button>
      </form>
    </AuthCard>
  );
}
export default function ResetPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
