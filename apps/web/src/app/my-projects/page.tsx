"use client";
import { Folder, FolderPlus, MoreHorizontal, Pencil, Search, Star, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuSep, MenuSub, MenuSubContent, MenuSubTrigger, MenuTrigger } from "@/components/ui/menu";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLang, useT } from "@/lib/i18n";
import type { Chat, Page } from "@/lib/types";
import { cn, timeAgo } from "@/lib/utils";

interface FolderT {
  id: number;
  name: string;
}

function Inner() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const workspaceId = useAuth((s) => s.workspaceId);
  const [chats, setChats] = useState<Chat[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState<{ starred?: boolean; folder?: number }>({});
  const [folders, setFolders] = useState<FolderT[]>([]);
  const [dialog, setDialog] = useState<{ kind: "rename" | "folder" | "renameFolder"; id?: string | number; value: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async (p = 1) => {
    setLoading(true);
    try {
      const d = await api.get<Page<Chat>>("/chats", {
        cur_page: p,
        page_num: 24,
        keyword: keyword || undefined,
        is_starred: filter.starred ? true : undefined,
        folder_id: filter.folder,
      });
      setChats(p === 1 ? d.items : (c) => [...c, ...d.items]);
      setTotal(d.total);
      setHasMore(d.has_more);
      setPage(p);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };
  const loadFolders = () => api.get<FolderT[]>("/folders").then(setFolders).catch(() => {});
  useEffect(() => {
    load(1);
  }, [filter, workspaceId]);
  useEffect(() => {
    loadFolders();
  }, [workspaceId]);
  useEffect(() => {
    const id = setTimeout(() => load(1), 300);
    return () => clearTimeout(id);
  }, [keyword]);

  const patch = async (id: string, body: Record<string, unknown>) => {
    try {
      const c = await api.patch<Chat>(`/chats/${id}`, body);
      setChats((cs) => cs.map((x) => (x.chat_id === id ? { ...x, ...c } : x)));
      if ("folder_id" in body && filter.folder !== undefined) load(1);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const submitDialog = async () => {
    if (!dialog) return;
    try {
      if (dialog.kind === "rename") await patch(dialog.id as string, { title: dialog.value });
      if (dialog.kind === "folder") await api.post("/folders", { name: dialog.value });
      if (dialog.kind === "renameFolder") await api.patch(`/folders/${dialog.id}`, { name: dialog.value });
      loadFolders();
      setDialog(null);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-serif text-[28px]">{t("myProjects")}</h1>
        <span className="text-[13px] text-muted">{total}</span>
        <div className="flex-1" />
        <div className="relative w-64">
          <Search className="absolute top-2.5 left-3 h-4 w-4 text-subtle" />
          <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder={t("search")} className="rounded-full pl-9" />
        </div>
        <Button variant="outline" onClick={() => setDialog({ kind: "folder", value: "" })}>
          <FolderPlus className="h-4 w-4" /> {t("newFolder")}
        </Button>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Chip active={!filter.starred && filter.folder === undefined} onClick={() => setFilter({})}>
          {t("allProjects")}
        </Chip>
        <Chip active={!!filter.starred} onClick={() => setFilter({ starred: true })}>
          <Star className="h-3 w-3" /> {t("starred")}
        </Chip>
        {folders.map((f) => (
          <div key={f.id} className="group relative">
            <Chip active={filter.folder === f.id} onClick={() => setFilter({ folder: f.id })}>
              <Folder className="h-3 w-3" /> {f.name}
            </Chip>
            <Menu>
              <MenuTrigger asChild>
                <button className="absolute -top-1 -right-1 hidden rounded-full bg-card p-0.5 shadow-card group-hover:block">
                  <MoreHorizontal className="h-3 w-3" />
                </button>
              </MenuTrigger>
              <MenuContent>
                <MenuItem onSelect={() => setDialog({ kind: "renameFolder", id: f.id, value: f.name })}>
                  <Pencil className="h-3.5 w-3.5" /> {t("rename")}
                </MenuItem>
                <MenuItem
                  danger
                  onSelect={async () => {
                    await api.del(`/folders/${f.id}`).catch((e) => toast.error(errMsg(e)));
                    if (filter.folder === f.id) setFilter({});
                    loadFolders();
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" /> {t("delete")}
                </MenuItem>
              </MenuContent>
            </Menu>
          </div>
        ))}
      </div>

      {!loading && chats.length === 0 ? (
        <Empty className="mt-10">
          {t("empty")}
          <Link href="/dashboard">
            <Button size="sm">+ New project</Button>
          </Link>
        </Empty>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {chats.map((c) => (
            <div key={c.chat_id} className="group overflow-hidden rounded-xl border border-line bg-card shadow-card transition-shadow hover:shadow-pop">
              <Link href={`/chat/${c.chat_id}`} className="block">
                <div className="relative aspect-[16/9] bg-bg-2">
                  {c.version?.cover ? (
                    <img src={c.version.cover} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center font-serif text-[40px] text-subtle">{c.title.charAt(0)}</div>
                  )}
                  {c.metadata.is_running && <span className="absolute top-2 left-2 rounded-full bg-brand px-2 py-0.5 text-[10px] text-white">{t("running")}</span>}
                </div>
              </Link>
              <div className="flex items-center gap-2 p-3">
                <div className="min-w-0 flex-1">
                  <Link href={`/chat/${c.chat_id}`} className="block truncate text-[14px] font-medium hover:underline">
                    {c.title}
                  </Link>
                  <div className="mt-0.5 text-[11px] text-muted">
                    {c.version?.version ?? "draft"} · {timeAgo(c.updated_at, lang)} · {t(c.access_mode)}
                  </div>
                </div>
                <button onClick={() => patch(c.chat_id, { is_starred: !c.is_starred })} className="cursor-pointer p-1">
                  <Star className={cn("h-4 w-4 text-subtle", c.is_starred && "fill-warning text-warning")} />
                </button>
                <Menu>
                  <MenuTrigger asChild>
                    <button className="cursor-pointer rounded p-1 hover:bg-hover">
                      <MoreHorizontal className="h-4 w-4" />
                    </button>
                  </MenuTrigger>
                  <MenuContent align="end">
                    <MenuItem onSelect={() => setDialog({ kind: "rename", id: c.chat_id, value: c.title })}>
                      <Pencil className="h-3.5 w-3.5" /> {t("rename")}
                    </MenuItem>
                    <MenuSub>
                      <MenuSubTrigger>
                        <Folder className="h-3.5 w-3.5" /> {t("moveTo")}
                      </MenuSubTrigger>
                      <MenuSubContent>
                        <MenuItem onSelect={() => patch(c.chat_id, { folder_id: null })}>— None</MenuItem>
                        {folders.map((f) => (
                          <MenuItem key={f.id} onSelect={() => patch(c.chat_id, { folder_id: f.id })}>
                            {f.name}
                          </MenuItem>
                        ))}
                      </MenuSubContent>
                    </MenuSub>
                    <MenuSep />
                    <MenuItem
                      danger
                      onSelect={async () => {
                        if (!confirm(`Delete "${c.title}"?`)) return;
                        try {
                          await api.del(`/chats/${c.chat_id}`);
                          setChats((cs) => cs.filter((x) => x.chat_id !== c.chat_id));
                        } catch (e) {
                          toast.error(errMsg(e));
                        }
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" /> {t("delete")}
                    </MenuItem>
                  </MenuContent>
                </Menu>
              </div>
            </div>
          ))}
        </div>
      )}
      {hasMore && (
        <div className="mt-6 flex justify-center">
          <Button variant="outline" onClick={() => load(page + 1)} loading={loading}>
            Load more
          </Button>
        </div>
      )}

      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent title={dialog?.kind === "folder" ? t("newFolder") : t("rename")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitDialog();
            }}
          >
            <Input autoFocus value={dialog?.value ?? ""} onChange={(e) => dialog && setDialog({ ...dialog, value: e.target.value })} />
            <div className="mt-4 flex justify-end">
              <Button type="submit">{t("save")}</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Chip({ active, onClick, children }: { active?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 text-[12px]",
        active ? "border-inverse bg-inverse text-inverse-fg" : "border-line-strong bg-card text-fg-2 hover:bg-hover",
      )}
    >
      {children}
    </button>
  );
}

export default function MyProjects() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}
