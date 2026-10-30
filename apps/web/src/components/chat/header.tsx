"use client";
import {
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  GitFork,
  History,
  Link2,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Rocket,
  Settings2,
  Share2,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { LogoMark } from "@/components/shell/logo";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuSep, MenuTrigger } from "@/components/ui/menu";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tip } from "@/components/ui/tooltip";
import { AGENTS } from "@/lib/agents";
import { api, errMsg } from "@/lib/api";
import { chatActions } from "@/lib/chat-actions";
import { useT } from "@/lib/i18n";
import type { Chat, Deployment } from "@/lib/types";
import { cn, copyText, timeAgo } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PANELS, type PanelPresence, pinned } from "@/components/workspace/workspace";
import { SecretsEditor } from "@/components/settings/secrets-editor";

export function ChatHeader({ collapsed, onToggleCollapse, readOnly }: { collapsed: boolean; onToggleCollapse: () => void; readOnly?: boolean }) {
  const t = useT();
  const router = useRouter();
  const chat = useChat((s) => s.chat);
  const panel = useChat((s) => s.panel);
  const setPanel = useChat((s) => s.setPanel);
  const timelines = useChat((s) => s.timelines);
  const hasBoost = useChat((s) => !!s.boost);
  const presence = useMemo<PanelPresence>(
    () => ({ blockTypes: new Set(Object.values(timelines).map((t) => t.block_type)), hasBoost }),
    [timelines, hasBoost],
  );
  const mergeChat = useChat((s) => s.mergeChat);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  if (!chat) return <div className="h-11" />;
  const team = chat.config.agent_mode === "lite" ? AGENTS.filter((a) => a.name === "Alex") : AGENTS.slice(0, 5);

  const patch = async (body: Record<string, unknown>) => {
    try {
      const c = await api.patch<Chat>(`/chats/${chat.chat_id}`, body);
      mergeChat(c);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <header className="flex h-11 shrink-0 items-center gap-1 px-2">
      <Link href="/dashboard" className="mr-1 rounded-full p-1 hover:bg-hover">
        <LogoMark size={22} />
      </Link>
      <Menu>
        <MenuTrigger asChild disabled={readOnly}>
          <button className="flex max-w-[220px] min-w-0 cursor-pointer items-center gap-1 rounded-m px-2 py-1 text-[13px] font-medium hover:bg-hover">
            <span className="truncate">{chat.title}</span>
            {!readOnly && <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />}
          </button>
        </MenuTrigger>
        <MenuContent>
          <MenuItem
            onSelect={() => {
              setTitle(chat.title);
              setRenaming(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" /> {t("rename")}
          </MenuItem>
          <MenuItem onSelect={() => patch({ is_starred: !chat.is_starred })}>
            <Star className={cn("h-3.5 w-3.5", chat.is_starred && "fill-warning text-warning")} /> {chat.is_starred ? "Unstar" : "Star"}
          </MenuItem>
          <MenuItem onSelect={() => setSettingsOpen(true)}>
            <Settings2 className="h-3.5 w-3.5" /> {t("settings")}
          </MenuItem>
          <MenuItem
            onSelect={async () => {
              try {
                const c = await api.post<Chat>(`/chats/${chat.chat_id}/fork`, {});
                router.push(`/chat/${c.chat_id}`);
              } catch (e) {
                toast.error(errMsg(e));
              }
            }}
          >
            <GitFork className="h-3.5 w-3.5" /> Duplicate
          </MenuItem>
          <MenuSep />
          <MenuItem
            danger
            onSelect={async () => {
              if (!confirm("Delete this project?")) return;
              await api.del(`/chats/${chat.chat_id}`).catch((e) => toast.error(errMsg(e)));
              router.replace("/my-projects");
            }}
          >
            <Trash2 className="h-3.5 w-3.5" /> {t("delete")}
          </MenuItem>
        </MenuContent>
      </Menu>
      <Tip label={t("versions")}>
        <Button variant="ghost" size="icon-sm" onClick={() => setVersionsOpen(true)}>
          <History className="h-4 w-4" />
        </Button>
      </Tip>
      <Tip label={collapsed ? "Expand chat" : "Collapse chat"}>
        <Button variant="ghost" size="icon-sm" onClick={onToggleCollapse} className="hidden md:inline-flex">
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
      </Tip>
      <div className="mx-1 hidden h-5 w-px bg-line md:block" />
      <div className="hidden min-w-0 items-center gap-0.5 overflow-x-auto md:flex">
        {PANELS.filter((p) => !(readOnly && p.key === "files") && (!p.when || p.key === panel || p.when(presence))).map((p) => (
          <Tip key={p.key} label={t(p.label)}>
            <button
              onClick={() => {
                pinned.at = Date.now();
                setPanel(p.key);
              }}
              className={cn(
                "flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-2 text-[12px] text-muted hover:bg-hover hover:text-fg",
                panel === p.key && "bg-card text-fg shadow-card",
              )}
            >
              <p.icon className="h-3.5 w-3.5" />
              {panel === p.key && <span className="hidden lg:inline">{t(p.label)}</span>}
            </button>
          </Tip>
        ))}
      </div>
      <div className="flex-1" />
      <div className="mr-2 hidden -space-x-2 sm:flex">
        {team.map((a) => (
          <img key={a.name} src={a.avatar} alt={a.name} title={`${a.name} · ${a.role}`} className="h-7 w-7 rounded-full bg-bg-2 ring-2 ring-bg" />
        ))}
      </div>
      {!readOnly && (
        <>
          <Button variant="outline" size="sm" onClick={() => setShareOpen(true)}>
            <Share2 className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{t("share")}</span>
          </Button>
          <Button size="sm" onClick={() => setPublishOpen(true)}>
            <Rocket className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{t("publish")}</span>
          </Button>
        </>
      )}

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent title={t("rename")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              patch({ title });
              setRenaming(false);
            }}
          >
            <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} />
            <div className="mt-4 flex justify-end">
              <Button type="submit">{t("save")}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <VersionsDrawer open={versionsOpen} onOpenChange={setVersionsOpen} readOnly={readOnly} />
      {!readOnly && <PublishDialog open={publishOpen} onOpenChange={setPublishOpen} />}
      {!readOnly && <ShareDialog open={shareOpen} onOpenChange={setShareOpen} patch={patch} />}
      {!readOnly && <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} patch={patch} />}
    </header>
  );
}

function VersionsDrawer({ open, onOpenChange, readOnly }: { open: boolean; onOpenChange: (o: boolean) => void; readOnly?: boolean }) {
  const t = useT();
  const versions = useChat((s) => s.versions);
  const chat = useChat((s) => s.chat)!;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title={t("versions")}>
        <div className="flex flex-col gap-2">
          {versions.length === 0 && <div className="py-8 text-center text-[13px] text-muted">No versions yet</div>}
          {versions.map((v) => (
            <div key={v.version} className={cn("rounded-l border border-line p-3", chat.version?.version === v.version && "border-brand/40 bg-brand-soft")}>
              <div className="flex items-center gap-2">
                <span className="text-[14px] font-semibold">{v.version}</span>
                <Badge tone={v.status === "ready" ? "positive" : v.status === "failed" ? "danger" : "warning"}>{v.status}</Badge>
                <span className="flex-1" />
                <span className="text-[11px] text-muted">{timeAgo(v.created_at)}</span>
              </div>
              {v.cover && <img src={v.cover} alt="" className="mt-2 aspect-video w-full rounded-m border border-line object-cover" />}
              <p className="mt-1.5 line-clamp-3 text-[12px] text-fg-2">{v.summary}</p>
              <div className="mt-2 flex gap-2">
                {v.link && (
                  <a href={v.link} target="_blank" rel="noreferrer">
                    <Button size="xs" variant="outline">
                      <ExternalLink className="h-3 w-3" /> {t("open")}
                    </Button>
                  </a>
                )}
                {!readOnly && (
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={async () => {
                      if (await chatActions.restore(chat.chat_id, v.version)) onOpenChange(false);
                    }}
                  >
                    <History className="h-3 w-3" /> {t("restore")}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function PublishDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useT();
  const chat = useChat((s) => s.chat)!;
  const versions = useChat((s) => s.versions);
  const deployment = useChat((s) => s.deployment);
  const set = useChat((s) => s.set);
  const ready = versions.filter((v) => v.status === "ready");
  const [version, setVersion] = useState("");
  const [latest, setLatest] = useState(true);
  const [access, setAccess] = useState<"public" | "private">("public");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      setVersion(deployment?.version ?? ready[0]?.version ?? "");
      setLatest(deployment?.is_always_latest ?? true);
      setAccess(deployment?.access_mode === "private" ? "private" : "public");
    }
  }, [open]);
  const publish = async () => {
    setBusy(true);
    try {
      const d = await api.put<Deployment>(`/chats/${chat.chat_id}/deployment`, {
        version: version || ready[0]?.version,
        is_always_latest: latest,
        access_mode: access,
      });
      set({ deployment: d });
      toast.success("Published");
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t("publish")} description="Deploy a version of your app to a public URL.">
        {ready.length === 0 ? (
          <div className="rounded-m bg-bg-2 p-4 text-[13px] text-muted">No built version yet. Ask the agents to build something first.</div>
        ) : (
          <div className="flex flex-col gap-4">
            <div>
              <Label>Version</Label>
              <Select
                value={version || ready[0].version}
                onValueChange={setVersion}
                options={ready.map((v) => ({ value: v.version, label: v.version, desc: v.summary.slice(0, 60) }))}
                className="w-full justify-between"
              />
            </div>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span>
                {t("alwaysLatest")}
                <span className="block text-[11px] text-muted">New versions are deployed automatically</span>
              </span>
              <Switch checked={latest} onCheckedChange={setLatest} />
            </label>
            <div>
              <Label>Who can visit</Label>
              <Select
                value={access}
                onValueChange={(v) => setAccess(v as "public" | "private")}
                options={[
                  { value: "public", label: "Anyone with the link", desc: "The published site is public" },
                  { value: "private", label: "Only workspace members", desc: "Visitors must open it from OpenAtoms" },
                ]}
                className="w-full justify-between"
              />
            </div>
            {deployment?.url && (
              <div className="rounded-l border border-line bg-bg-2 p-3">
                <div className="flex items-center gap-2 text-[12px]">
                  <Badge tone={deployment.status === "success" ? "positive" : deployment.status === "failed" ? "danger" : "warning"}>{deployment.status}</Badge>
                  <span className="text-muted">{deployment.is_always_latest ? "always latest" : deployment.version}</span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <a href={deployment.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-[13px] text-brand hover:underline">
                    {deployment.url}
                  </a>
                  <Button size="icon-sm" variant="ghost" onClick={() => copyText(deployment.url!)?.then(() => toast.success(t("copied")))}>
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            )}
            <div className="flex justify-end gap-2">
              {deployment && (
                <Button
                  variant="outline"
                  onClick={async () => {
                    await api.del(`/chats/${chat.chat_id}/deployment`).catch((e) => toast.error(errMsg(e)));
                    set({ deployment: null });
                  }}
                >
                  {t("unpublish")}
                </Button>
              )}
              <Button onClick={publish} loading={busy}>
                <Rocket className="h-3.5 w-3.5" /> {deployment ? "Update" : t("publish")}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ShareDialog({ open, onOpenChange, patch }: { open: boolean; onOpenChange: (o: boolean) => void; patch: (b: Record<string, unknown>) => Promise<void> }) {
  const t = useT();
  const chat = useChat((s) => s.chat)!;
  const shareUrl = typeof window !== "undefined" ? `${location.origin}/share/${chat.chat_id}` : "";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t("share")} description="Share a read-only replay of this project.">
        <div className="flex flex-col gap-2">
          {(["private", "secret", "public"] as const).map((m) => (
            <button
              key={m}
              onClick={() => patch({ access_mode: m })}
              className={cn("flex cursor-pointer items-center gap-3 rounded-l border p-3 text-left", chat.access_mode === m ? "border-brand bg-brand-soft" : "border-line hover:bg-hover")}
            >
              <div className="flex-1">
                <div className="text-[13px] font-medium">{t(m)}</div>
                <div className="text-[11px] text-muted">
                  {m === "private" ? "Only workspace members" : m === "secret" ? "Anyone with the link can view" : "Listed on Discover; anyone can view and remix"}
                </div>
              </div>
              {chat.access_mode === m && <Check className="h-4 w-4 text-brand" />}
            </button>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-2 rounded-l border border-line bg-bg-2 p-2">
          <Link2 className="h-3.5 w-3.5 shrink-0 text-muted" />
          <span className="min-w-0 flex-1 truncate text-[12px]">{shareUrl}</span>
          <Button size="xs" disabled={chat.access_mode === "private"} onClick={() => copyText(shareUrl)?.then(() => toast.success(t("copied")))}>
            {t("copy")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SettingsDialog({ open, onOpenChange, patch }: { open: boolean; onOpenChange: (o: boolean) => void; patch: (b: Record<string, unknown>) => Promise<void> }) {
  const t = useT();
  const chat = useChat((s) => s.chat)!;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t("settings")} className="w-[640px]">
        <div className="flex flex-col gap-4 text-[13px]">
          <div className="flex items-center justify-between">
            <span>Agent mode</span>
            <Select
              value={chat.config.agent_mode}
              onValueChange={(v) => patch({ config: { agent_mode: v } })}
              options={[
                { value: "lite", label: t("lite"), desc: t("liteDesc") },
                { value: "full", label: t("full"), desc: t("fullDesc") },
              ]}
            />
          </div>
          <div className="flex items-center justify-between">
            <span>
              {t("planMode")}
              <span className="block text-[11px] text-muted">{t("planModeDesc")}</span>
            </span>
            <Switch checked={!!chat.config.plan_mode} onCheckedChange={(v) => patch({ config: { plan_mode: v } })} />
          </div>
          <div className="flex items-center justify-between">
            <span>{t("accessMode")}</span>
            <Select
              value={chat.access_mode}
              onValueChange={(v) => patch({ access_mode: v })}
              options={[
                { value: "private", label: t("private") },
                { value: "secret", label: t("secret") },
                { value: "public", label: t("public") },
              ]}
            />
          </div>
          <IntegrationsSection />
          <div>
            <div className="mb-2 font-medium">{t("secrets")}</div>
            <SecretsEditor basePath={`/chats/${chat.chat_id}/secrets`} />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function IntegrationsSection() {
  const chat = useChat((s) => s.chat)!;
  const mergeChat = useChat((s) => s.mergeChat);
  const [repo, setRepo] = useState(chat.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "openatoms-app");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      toast.success(ok);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  };
  const gh = chat.config.github;
  return (
    <div className="rounded-l border border-line p-3">
      <div className="mb-2 flex items-center gap-2 font-medium">
        GitHub
        {gh?.repo && <Badge tone="positive">{gh.repo}</Badge>}
      </div>
      {gh?.repo ? (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" loading={busy === "push"} onClick={() => run("push", () => api.post(`/chats/${chat.chat_id}/github/push`, { message: "Update from OpenAtoms" }), "Pushed")}>
            Push
          </Button>
          <Button size="sm" variant="outline" loading={busy === "pull"} onClick={() => run("pull", () => api.post(`/chats/${chat.chat_id}/github/pull`), "Pulled")}>
            Pull
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="repo name" />
          <Button
            size="md"
            loading={busy === "repo"}
            onClick={() =>
              run(
                "repo",
                async () => {
                  await api.post(`/chats/${chat.chat_id}/github/repo`, { name: repo, private: true });
                  mergeChat(await api.get<Chat>(`/chats/${chat.chat_id}`));
                },
                "Repository created",
              )
            }
          >
            Create repo
          </Button>
        </div>
      )}
      <div className="mt-3 mb-2 flex items-center gap-2 font-medium">
        Supabase
        {chat.config.supabase?.project_ref && <Badge tone="positive">{chat.config.supabase.project_ref}</Badge>}
      </div>
      <div className="flex gap-2">
        <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="project ref" />
        <Button
          variant="outline"
          disabled={!ref}
          loading={busy === "sb"}
          onClick={() =>
            run(
              "sb",
              async () => {
                await api.post(`/chats/${chat.chat_id}/supabase/link`, { project_ref: ref });
                mergeChat(await api.get<Chat>(`/chats/${chat.chat_id}`));
              },
              "Supabase linked",
            )
          }
        >
          Link
        </Button>
      </div>
      <p className="mt-2 text-[11px] text-muted">Connect your GitHub/Supabase account in Settings → Integrations first.</p>
    </div>
  );
}
