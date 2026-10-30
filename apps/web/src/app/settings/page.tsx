"use client";
import { Copy, Github, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { SecretsEditor } from "@/components/settings/secrets-editor";
import { AppShell } from "@/components/shell/app-shell";
import { UserAvatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLang, useT } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import type { Member, Page, User } from "@/lib/types";
import { cn, copyText, fmtDate } from "@/lib/utils";

const TABS = ["profile", "workspace", "secrets", "integrations", "billing"] as const;
type Tab = (typeof TABS)[number];

function Inner() {
  const t = useT();
  const sp = useSearchParams();
  const router = useRouter();
  const tab = (TABS.includes(sp.get("tab") as Tab) ? sp.get("tab") : "profile") as Tab;
  const labels: Record<Tab, string> = {
    profile: t("profile"),
    workspace: `${t("workspace")} & ${t("members")}`,
    secrets: t("secrets"),
    integrations: t("integrations"),
    billing: t("billing"),
  };
  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <h1 className="font-display text-[28px]">{t("settings")}</h1>
      <div className="mt-5 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((k) => (
          <button
            key={k}
            onClick={() => router.replace(`/settings?tab=${k}`)}
            className={cn("-mb-px cursor-pointer border-b-2 px-3 py-2 text-[13px]", tab === k ? "border-inverse font-medium text-fg" : "border-transparent text-muted hover:text-fg")}
          >
            {labels[k]}
          </button>
        ))}
      </div>
      <div className="mt-6">
        {tab === "profile" && <Profile />}
        {tab === "workspace" && <WorkspaceTab />}
        {tab === "secrets" && <SecretsTab />}
        {tab === "integrations" && <Integrations />}
        {tab === "billing" && <Billing />}
      </div>
    </div>
  );
}

function Profile() {
  const t = useT();
  const { user, setUser } = useAuth();
  const { lang, setLang } = useLang();
  const { dark, toggle } = useTheme();
  const [name, setName] = useState(user?.name ?? "");
  const [avatar, setAvatar] = useState(user?.avatar ?? "");
  const [busy, setBusy] = useState(false);
  return (
    <Card className="p-5">
      <div className="flex items-center gap-4">
        <UserAvatar name={user?.name || user?.email} src={avatar || null} size={56} />
        <div>
          <div className="text-[15px] font-medium">{user?.email}</div>
          <div className="mt-1 flex items-center gap-2 text-[12px] text-muted">
            {user?.email_verified ? <Badge tone="positive">verified</Badge> : <Badge tone="warning">unverified</Badge>}
            <span>plan: {user?.plan}</span>
          </div>
        </div>
      </div>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <div>
          <Label>{t("name")}</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Avatar URL</Label>
          <Input value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="https://…" />
        </div>
        <div>
          <Label>{t("language")}</Label>
          <Select value={lang} onValueChange={(v) => setLang(v as "zh" | "en")} options={[{ value: "zh", label: "中文" }, { value: "en", label: "English" }]} />
        </div>
        <div>
          <Label>{t("darkMode")}</Label>
          <Button variant="outline" size="sm" onClick={toggle}>
            {dark ? "Dark" : "Light"}
          </Button>
        </div>
      </div>
      <div className="mt-5 flex justify-end">
        <Button
          loading={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const u = await api.patch<User>("/user/detail", { name, avatar: avatar || null });
              setUser(u);
              toast.success("Saved");
            } catch (e) {
              toast.error(errMsg(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {t("save")}
        </Button>
      </div>
    </Card>
  );
}

function WorkspaceTab() {
  const t = useT();
  const { workspaces, workspaceId, refreshWorkspaces, user } = useAuth();
  const ws = workspaces.find((w) => w.id === workspaceId);
  const [name, setName] = useState(ws?.name ?? "");
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const canAdmin = ws?.role === "owner" || ws?.role === "admin";
  const load = () => workspaceId && api.get<Member[]>(`/workspaces/${workspaceId}/members`).then(setMembers).catch((e) => toast.error(errMsg(e)));
  useEffect(() => {
    setName(ws?.name ?? "");
    load();
  }, [workspaceId]);
  if (!ws) return null;
  return (
    <div className="flex flex-col gap-5">
      <Card className="p-5">
        <Label>Workspace name</Label>
        <div className="flex gap-2">
          <Input value={name} disabled={!canAdmin} onChange={(e) => setName(e.target.value)} />
          <Button
            disabled={!canAdmin}
            onClick={async () => {
              try {
                await api.patch(`/workspaces/${ws.id}`, { name });
                await refreshWorkspaces();
                toast.success("Saved");
              } catch (e) {
                toast.error(errMsg(e));
              }
            }}
          >
            {t("save")}
          </Button>
        </div>
      </Card>
      <Card className="p-5">
        <div className="mb-3 text-[14px] font-medium">{t("members")}</div>
        {canAdmin && (
          <form
            className="mb-4 flex flex-wrap gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                const d = await api.post<{ token: string; url: string }>(`/workspaces/${ws.id}/invitations`, { email, role });
                setInviteUrl(d.url || `${location.origin}/workspace/invite/${d.token}`);
                setEmail("");
              } catch (err) {
                toast.error(errMsg(err));
              }
            }}
          >
            <Input type="email" required placeholder="teammate@company.com" className="min-w-52 flex-1" value={email} onChange={(e) => setEmail(e.target.value)} />
            <Select value={role} onValueChange={setRole} options={["admin", "member", "viewer"].map((r) => ({ value: r, label: r }))} />
            <Button type="submit">{t("invite")}</Button>
          </form>
        )}
        {inviteUrl && (
          <div className="mb-4 flex items-center gap-2 rounded-l bg-brand-soft p-2 text-[12px]">
            <span className="min-w-0 flex-1 truncate">{inviteUrl}</span>
            <Button size="xs" onClick={() => copyText(inviteUrl)?.then(() => toast.success(t("copied")))}>
              <Copy className="h-3 w-3" /> {t("copy")}
            </Button>
          </div>
        )}
        <div className="flex flex-col divide-y divide-line">
          {members.map((m) => (
            <div key={m.user_id} className="flex items-center gap-3 py-2.5">
              <UserAvatar name={m.name || m.email} src={m.avatar} size={30} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{m.name || m.email}</div>
                <div className="truncate text-[11px] text-muted">{m.email}</div>
              </div>
              {canAdmin && m.role !== "owner" && m.user_id !== user?.id ? (
                <>
                  <Select
                    value={m.role}
                    onValueChange={async (r) => {
                      try {
                        await api.patch(`/workspaces/${ws.id}/members/${m.user_id}`, { role: r });
                        load();
                      } catch (e) {
                        toast.error(errMsg(e));
                      }
                    }}
                    options={["admin", "member", "viewer"].map((r) => ({ value: r, label: r }))}
                  />
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    onClick={async () => {
                      await api.del(`/workspaces/${ws.id}/members/${m.user_id}`).catch((e) => toast.error(errMsg(e)));
                      load();
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </>
              ) : (
                <Badge>{m.role}</Badge>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function SecretsTab() {
  const workspaceId = useAuth((s) => s.workspaceId);
  return (
    <Card className="p-5">
      <div className="mb-1 text-[14px] font-medium">Workspace secrets</div>
      <p className="mb-4 text-[12px] text-muted">Injected as environment variables into every project sandbox in this workspace. Values are encrypted and masked in logs.</p>
      {workspaceId && <SecretsEditor basePath={`/workspaces/${workspaceId}/secrets`} />}
    </Card>
  );
}

interface IntegrationRow {
  provider: string;
  connected: boolean;
  account: Record<string, any> | null;
}

function Integrations() {
  const t = useT();
  const [rows, setRows] = useState<IntegrationRow[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const load = () => api.get<IntegrationRow[]>("/integrations").then(setRows).catch((e) => toast.error(errMsg(e)));
  useEffect(() => {
    load();
  }, []);
  const providers = [
    { id: "github", name: "GitHub", desc: "Push generated code to repositories.", icon: <Github className="h-5 w-5" />, tokenHint: "Personal access token (repo scope)" },
    {
      id: "supabase",
      name: "Supabase",
      desc: "Use your Supabase project as the app backend.",
      icon: <span className="text-[18px]">⚡</span>,
      tokenHint: "Supabase access token",
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      {providers.map((p) => {
        const row = rows.find((r) => r.provider === p.id);
        return (
          <Card key={p.id} className="p-5">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-l bg-bg-2">{p.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[14px] font-medium">
                  {p.name}
                  {row?.connected && <Badge tone="positive">{t("connected")}</Badge>}
                </div>
                <div className="text-[12px] text-muted">
                  {row?.connected ? row.account?.login ?? row.account?.name ?? row.account?.email ?? "" : p.desc}
                </div>
              </div>
              {row?.connected ? (
                <Button
                  variant="outline"
                  onClick={async () => {
                    await api.del(`/integrations/${p.id}`).catch((e) => toast.error(errMsg(e)));
                    load();
                  }}
                >
                  {t("disconnect")}
                </Button>
              ) : (
                <Button
                  onClick={async () => {
                    try {
                      const d = await api.get<{ url: string }>(`/integrations/${p.id}/connect`);
                      location.href = d.url;
                    } catch (e) {
                      toast.error(`${errMsg(e)} — paste a token below instead`);
                    }
                  }}
                >
                  {t("connect")}
                </Button>
              )}
            </div>
            {!row?.connected && (
              <form
                className="mt-4 flex gap-2"
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await api.post(`/integrations/${p.id}/token`, { token: tokens[p.id] });
                    toast.success(`${p.name} connected`);
                    setTokens({ ...tokens, [p.id]: "" });
                    load();
                  } catch (err) {
                    toast.error(errMsg(err));
                  }
                }}
              >
                <Input type="password" placeholder={p.tokenHint} value={tokens[p.id] ?? ""} onChange={(e) => setTokens({ ...tokens, [p.id]: e.target.value })} />
                <Button type="submit" variant="outline" disabled={!tokens[p.id]}>
                  Save token
                </Button>
              </form>
            )}
          </Card>
        );
      })}
    </div>
  );
}

interface LedgerRow {
  id: number;
  delta: number;
  reason: string;
  ref: string | null;
  created_at: string;
}

function Billing() {
  const t = useT();
  const { balance, refreshBalance } = useAuth();
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  useEffect(() => {
    refreshBalance();
  }, []);
  useEffect(() => {
    api
      .get<Page<LedgerRow>>("/transaction/ledger", { cur_page: page, page_num: 20 })
      .then((d) => {
        setRows(d.items);
        setHasMore(d.has_more);
      })
      .catch((e) => toast.error(errMsg(e)));
  }, [page]);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="p-5">
          <div className="text-[12px] text-muted">{t("balance")}</div>
          <div className="mt-1 text-[28px] font-semibold">{balance?.credits ?? "—"}</div>
        </Card>
        <Card className="p-5">
          <div className="text-[12px] text-muted">{t("currentPlan")}</div>
          <div className="mt-1 text-[22px] font-semibold capitalize">{balance?.plan ?? "free"}</div>
        </Card>
        <Card className="p-5">
          <div className="text-[12px] text-muted">Used this month</div>
          <div className="mt-1 text-[22px] font-semibold">
            {balance?.used_this_month ?? 0}
            <span className="text-[13px] font-normal text-muted"> / {balance?.monthly_quota ?? 0}</span>
          </div>
        </Card>
      </div>
      <div className="flex justify-end">
        <a href="/pricing">
          <Button variant="brand">{t("upgrade")}</Button>
        </a>
      </div>
      <Card className="overflow-hidden">
        <div className="border-b border-line px-5 py-3 text-[14px] font-medium">{t("ledger")}</div>
        <table className="w-full text-[13px]">
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td className="p-5 text-center text-muted">No transactions</td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-line last:border-0">
                <td className="px-5 py-2 text-muted">{fmtDate(r.created_at)}</td>
                <td className="px-2 py-2 capitalize">{r.reason}</td>
                <td className="max-w-[240px] truncate px-2 py-2 text-[12px] text-muted">{r.ref}</td>
                <td className={cn("px-5 py-2 text-right font-mono", r.delta >= 0 ? "text-positive" : "text-danger")}>
                  {r.delta >= 0 ? "+" : ""}
                  {r.delta}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-2">
          <Button size="xs" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Prev
          </Button>
          <Button size="xs" variant="ghost" disabled={!hasMore} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      </Card>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <AppShell>
      <Suspense>
        <Inner />
      </Suspense>
    </AppShell>
  );
}
