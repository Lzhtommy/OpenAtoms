"use client";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { AuthCard } from "@/components/auth/auth-card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, errMsg } from "@/lib/api";

export default function ForgotPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [devToken, setDevToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  return (
    <AuthCard title="Reset password" subtitle="We'll email you a link to reset your password.">
      {sent ? (
        <div className="text-[13px] text-fg-2">
          If an account exists for <b>{email}</b>, a reset email has been sent.
          {devToken && (
            <div className="mt-3 rounded-m bg-brand-soft p-3 text-[12px]">
              Dev mode:{" "}
              <Link className="text-brand underline" href={`/password/reset?token=${devToken}`}>
                open reset link
              </Link>
            </div>
          )}
        </div>
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setLoading(true);
            try {
              const d = await api.post<{ dev_token?: string } | null>("/user/pwd/forgot", { email });
              setDevToken(d?.dev_token ?? null);
              setSent(true);
            } catch (err) {
              toast.error(errMsg(err));
            } finally {
              setLoading(false);
            }
          }}
        >
          <div>
            <Label>Email</Label>
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <Button type="submit" size="lg" loading={loading}>
            Send reset link
          </Button>
          <Link href="/login" className="text-center text-[13px] text-brand hover:underline">
            Back to login
          </Link>
        </form>
      )}
    </AuthCard>
  );
}
