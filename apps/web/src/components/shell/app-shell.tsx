"use client";
import {
  CalendarClock,
  Check,
  ChevronsUpDown,
  Compass,
  CreditCard,
  FolderKanban,
  Home,
  LogOut,
  Menu as MenuIcon,
  PanelLeftClose,
  Plus,
  Settings,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSep, MenuTrigger } from "@/components/ui/menu";
import { UserAvatar } from "@/components/ui/avatar";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import type { Chat, Page, Workspace } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FullPageLoader, useRequireAuth } from "./auth-guard";
import { LogoMark } from "./logo";
import { LangToggle, ThemeToggle } from "./prefs";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready } = useRequireAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  if (!ready) return <FullPageLoader />;
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="hidden w-[248px] shrink-0 md:flex">
        <Sidebar />
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMobileOpen(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <aside className="absolute top-0 left-0 h-full w-[260px] bg-bg" onClick={(e) => e.stopPropagation()}>
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
      <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex h-12 items-center gap-2 px-3 md:hidden">
          <Button variant="ghost" size="icon-sm" onClick={() => setMobileOpen(true)}>
            <MenuIcon className="h-4 w-4" />
          </Button>
          <LogoMark />
        </div>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </main>
    </div>
  );
}

export function useRecentChats(limit = 12) {
  const workspaceId = useAuth((s) => s.workspaceId);
  const [chats, setChats] = useState<Chat[]>([]);
  const load = () =>
    api
      .get<Page<Chat>>("/chats", { cur_page: 1, page_num: limit })
      .then((p) => setChats(p.items))
      .catch(() => {});
  useEffect(() => {
    load();
  }, [workspaceId]);
  return { chats, reload: load };
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const t = useT();
  const path = usePathname();
  const router = useRouter();
  const { user, workspaces, workspaceId, switchWorkspace, balance, logout, refreshWorkspaces } = useAuth();
  const { chats } = useRecentChats();
  const [newWs, setNewWs] = useState(false);
  const [wsName, setWsName] = useState("");
  const ws = workspaces.find((w) => w.id === workspaceId);

  const nav = [
    { href: "/dashboard", label: t("home"), icon: Home },
    { href: "/my-projects", label: t("myProjects"), icon: FolderKanban },
    { href: "/discover", label: t("discover"), icon: Compass },
    { href: "/automations", label: t("automations"), icon: CalendarClock },
    { href: "/pricing", label: t("pricing"), icon: Sparkles },
    { href: "/settings", label: t("settings"), icon: Settings },
  ];

  const createWs = async () => {
    try {
      const w = await api.post<Workspace>("/workspaces", { name: wsName || "New workspace" });
      await refreshWorkspaces();
      switchWorkspace(w.id);
      setNewWs(false);
      setWsName("");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="flex h-full w-full flex-col gap-1 px-3 py-3">
      <div className="flex items-center justify-between px-1 pb-2">
        <Link href="/dashboard" className="flex items-center gap-2" onClick={onNavigate}>
          <LogoMark />
          <span className="text-[15px] font-semibold tracking-tight">OpenAtoms</span>
        </Link>
        <PanelLeftClose className="hidden h-4 w-4 text-subtle" />
      </div>

      <Menu>
        <MenuTrigger asChild>
          <button className="flex w-full items-center gap-2 rounded-l px-2 py-2 text-left hover:bg-hover">
            <span className="flex h-6 w-6 items-center justify-center rounded-m bg-inverse text-[11px] font-semibold text-inverse-fg">
              {(ws?.name || "W").charAt(0).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">{ws?.name ?? "Workspace"}</span>
              <span className="block text-[11px] text-muted capitalize">
                {ws?.plan ?? "free"} · {ws?.role}
              </span>
            </span>
            <ChevronsUpDown className="h-3.5 w-3.5 text-subtle" />
          </button>
        </MenuTrigger>
        <MenuContent className="w-[220px]">
          <MenuLabel>{t("workspace")}</MenuLabel>
          {workspaces.map((w) => (
            <MenuItem
              key={w.id}
              onSelect={() => {
                switchWorkspace(w.id);
                router.push("/dashboard");
              }}
            >
              <span className="flex-1 truncate">{w.name}</span>
              {w.id === workspaceId && <Check className="h-3.5 w-3.5 text-brand" />}
            </MenuItem>
          ))}
          <MenuSep />
          <MenuItem onSelect={() => setNewWs(true)}>
            <Plus className="h-3.5 w-3.5" /> New workspace
          </MenuItem>
        </MenuContent>
      </Menu>

      <Link
        href="/dashboard"
        onClick={onNavigate}
        className="mt-1 mb-1 flex items-center justify-center gap-1.5 rounded-full border border-line-strong bg-card py-1.5 text-[13px] font-medium hover:bg-hover"
      >
        <Plus className="h-3.5 w-3.5" /> New project
      </Link>

      <nav className="flex flex-col gap-0.5">
        {nav.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-2.5 rounded-m px-2.5 py-1.5 text-[13px] text-fg-2 hover:bg-hover",
              path.startsWith(n.href) && "bg-active font-medium text-fg",
            )}
          >
            <n.icon className="h-4 w-4" />
            {n.label}
          </Link>
        ))}
      </nav>

      <div className="mt-4 px-2.5 text-[11px] font-medium text-muted">{t("recent")}</div>
      <div className="-mx-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-1">
        {chats.map((c) => (
          <Link
            key={c.chat_id}
            href={`/chat/${c.chat_id}`}
            onClick={onNavigate}
            className={cn(
              "truncate rounded-m px-2.5 py-1.5 text-[13px] text-fg-2 hover:bg-hover",
              path === `/chat/${c.chat_id}` && "bg-active text-fg",
            )}
          >
            {c.title || "Untitled"}
          </Link>
        ))}
      </div>

      <Link
        href="/settings?tab=billing"
        onClick={onNavigate}
        className="flex items-center gap-2 rounded-l border border-line bg-card px-3 py-2 text-[12px] hover:bg-hover"
      >
        <CreditCard className="h-3.5 w-3.5 text-brand" />
        <span className="flex-1 text-fg-2">{t("credits")}</span>
        <span className="font-semibold">{balance?.credits ?? "—"}</span>
      </Link>

      <div className="flex items-center gap-1 pt-1">
        <Menu>
          <MenuTrigger asChild>
            <button className="flex min-w-0 flex-1 items-center gap-2 rounded-m px-1.5 py-1.5 hover:bg-hover">
              <UserAvatar name={user?.name || user?.email} src={user?.avatar} size={26} />
              <span className="truncate text-[13px]">{user?.name || user?.email}</span>
            </button>
          </MenuTrigger>
          <MenuContent side="top">
            <MenuLabel>{user?.email}</MenuLabel>
            <MenuItem onSelect={() => router.push("/settings")}>
              <Settings className="h-3.5 w-3.5" /> {t("settings")}
            </MenuItem>
            <MenuSep />
            <MenuItem
              danger
              onSelect={async () => {
                await logout();
                router.replace("/login");
              }}
            >
              <LogOut className="h-3.5 w-3.5" /> {t("logout")}
            </MenuItem>
          </MenuContent>
        </Menu>
        <LangToggle />
        <ThemeToggle />
      </div>

      <Dialog open={newWs} onOpenChange={setNewWs}>
        <DialogContent title="New workspace">
          <Input autoFocus value={wsName} onChange={(e) => setWsName(e.target.value)} placeholder="Team name" />
          <div className="mt-4 flex justify-end">
            <Button onClick={createWs}>Create</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
