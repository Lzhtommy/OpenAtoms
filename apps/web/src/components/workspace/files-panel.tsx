"use client";
import { ChevronRight, Download, File, FileCode2, Folder, FolderOpen, RefreshCw, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { api, download, errMsg, rawFileUrl } from "@/lib/api";
import type { TreeNode } from "@/lib/types";
import { cn, langFromPath } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelEmpty } from "./common";
import { CodeView } from "./monaco";

export function FilesPanel() {
  const chatId = useChat((s) => s.chatId)!;
  const isRunning = useChat((s) => s.isRunning);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [file, setFile] = useState<{ path: string; content: string; binary: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  const up = useRef<HTMLInputElement>(null);

  const load = async () => {
    setLoading(true);
    try {
      setTree(await api.get<TreeNode[]>(`/chats/${chatId}/files/tree`));
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [chatId, isRunning]);

  const openFile = async (path: string) => {
    try {
      const d = await api.get<{ type: string; path: string; content: string; binary: boolean }>(`/chats/${chatId}/files`, { path });
      setFile({ path, content: d.content ?? "", binary: !!d.binary });
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const onUpload = async (list: FileList | null) => {
    if (!list) return;
    for (const f of Array.from(list)) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        await api.upload(`/chats/${chatId}/files/upload`, fd);
      } catch (e) {
        toast.error(errMsg(e));
      }
    }
    toast.success("Uploaded");
    load();
  };

  const render = (nodes: TreeNode[], depth: number): React.ReactNode =>
    nodes
      .slice()
      .sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1))
      .map((n) => (
        <div key={n.path}>
          <button
            onClick={() => (n.type === "dir" ? setOpen({ ...open, [n.path]: !open[n.path] }) : openFile(n.path))}
            className={cn(
              "flex w-full cursor-pointer items-center gap-1 py-[3px] pr-2 text-left text-[12px] text-fg-2 hover:bg-hover",
              file?.path === n.path && "bg-brand-soft text-brand",
            )}
            style={{ paddingLeft: 8 + depth * 12 }}
          >
            {n.type === "dir" ? (
              <>
                <ChevronRight className={cn("h-3 w-3 shrink-0 transition-transform", open[n.path] && "rotate-90")} />
                {open[n.path] ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-brand" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-brand" />}
              </>
            ) : (
              <>
                <span className="w-3" />
                <File className="h-3.5 w-3.5 shrink-0 text-muted" />
              </>
            )}
            <span className="truncate">{n.name}</span>
          </button>
          {n.type === "dir" && open[n.path] && n.children && render(n.children, depth + 1)}
        </div>
      ));

  return (
    <div className="flex h-full">
      <div className="flex w-[230px] shrink-0 flex-col border-r border-line bg-card">
        <div className="flex h-[34px] shrink-0 items-center gap-1 border-b border-line px-2 text-[12px] font-medium">
          <span className="flex-1">Files</span>
          <Tip label="Refresh">
            <Button size="icon-sm" variant="ghost" onClick={load} loading={loading}>
              {!loading && <RefreshCw className="h-3.5 w-3.5" />}
            </Button>
          </Tip>
          <Tip label="Upload">
            <Button size="icon-sm" variant="ghost" onClick={() => up.current?.click()}>
              <Upload className="h-3.5 w-3.5" />
            </Button>
          </Tip>
          <Tip label="Download zip">
            <Button size="icon-sm" variant="ghost" onClick={() => download(`/chats/${chatId}/files/export`, `project-${chatId.slice(0, 8)}.zip`).catch((e) => toast.error(errMsg(e)))}>
              <Download className="h-3.5 w-3.5" />
            </Button>
          </Tip>
          <input ref={up} type="file" multiple hidden onChange={(e) => (onUpload(e.target.files), (e.target.value = ""))} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">{tree.length ? render(tree, 0) : <div className="p-3 text-[12px] text-muted">Empty workspace</div>}</div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        {file ? (
          <>
            <div className="flex h-[34px] shrink-0 items-center gap-2 border-b border-line px-3 font-mono text-[12px] text-fg-2">
              <FileCode2 className="h-3.5 w-3.5" />
              <span className="flex-1 truncate">{file.path}</span>
              <a href={rawFileUrl(chatId, file.path)} target="_blank" rel="noreferrer" className="text-[11px] text-brand hover:underline">
                raw
              </a>
            </div>
            <div className="min-h-0 flex-1">
              {file.binary ? (
                /\.(png|jpe?g|gif|webp|svg)$/i.test(file.path) ? (
                  <div className="flex h-full items-center justify-center p-4">
                    <BinaryImage chatId={chatId} path={file.path} />
                  </div>
                ) : (
                  <PanelEmpty text="Binary file" />
                )
              ) : (
                <CodeView value={file.content} language={langFromPath(file.path)} path={`files-${file.path}`} />
              )}
            </div>
          </>
        ) : (
          <PanelEmpty icon={FileCode2} text="Select a file to view" />
        )}
      </div>
    </div>
  );
}

function BinaryImage({ chatId, path }: { chatId: string; path: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    api
      .get<Response>(`/chats/${chatId}/files/raw`, { path }, { raw: true })
      .then((r) => r.blob())
      .then((b) => setUrl((u = URL.createObjectURL(b))))
      .catch(() => {});
    return () => {
      if (u) URL.revokeObjectURL(u);
    };
  }, [path]);
  return url ? <img src={url} alt={path} className="max-h-full max-w-full rounded-m border border-line" /> : null;
}
