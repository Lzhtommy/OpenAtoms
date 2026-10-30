"use client";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { AuthCard } from "@/components/auth/auth-card";
import { FullPageLoader, useRequireAuth } from "@/components/shell/auth-guard";
import { Button } from "@/components/ui/button";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Workspace } from "@/lib/types";

export default function Invite() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { ready } = useRequireAuth();
  const [busy, setBusy] = useState(false);
  if (!ready) return <FullPageLoader />;
  return (
    <AuthCard title="Join workspace" subtitle="You've been invited to collaborate on a workspace.">
      <Button
        className="w-full"
        size="lg"
        loading={busy}
        onClick={async () => {
          setBusy(true);
          try {
            const w = await api.post<Workspace>(`/workspaces/invitations/${token}/accept`);
            await useAuth.getState().refreshWorkspaces();
            useAuth.getState().switchWorkspace(w.id);
            toast.success(`Joined ${w.name}`);
            router.replace("/dashboard");
          } catch (e) {
            toast.error(errMsg(e));
            setBusy(false);
          }
        }}
      >
        Accept invitation
      </Button>
    </AuthCard>
  );
}
