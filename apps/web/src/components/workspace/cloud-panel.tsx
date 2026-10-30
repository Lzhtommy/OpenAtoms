"use client";
import { Database, Download, RefreshCw, Table2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, download, errMsg } from "@/lib/api";
import type { Page } from "@/lib/types";
import { cn, safeJson } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelEmpty, useBlockSelection } from "./common";

interface TableInfo {
  name: string;
  columns: { name: string; type: string }[];
  row_count: number;
}

export function CloudPanel({ readOnly }: { readOnly?: boolean }) {
  const chatId = useChat((s) => s.chatId)!;
  const chat = useChat((s) => s.chat);
  const mergeChat = useChat((s) => s.mergeChat);
  const { selected: backendBlock } = useBlockSelection(["Backend"]);
  const activeIndex = useChat((s) => s.activeIndex);
  const [env, setEnv] = useState<"dev" | "prod">("dev");
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [table, setTable] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, any>[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const imp = useRef<HTMLInputElement>(null);
  const enabled = !!chat?.config.cloud_enabled;

  const loadTables = async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const t = await api.get<TableInfo[]>(`/chats/${chatId}/database/${env}/tables`);
      setTables(t);
      if (t.length && (!table || !t.find((x) => x.name === table))) setTable(t[0].name);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };
  const loadRows = async () => {
    if (!table) return setRows([]);
    try {
      const d = await api.get<Page<Record<string, any>>>(`/chats/${chatId}/database/${env}/tables/${table}/rows`, { cur_page: page, page_num: 50 });
      setRows(d.items);
      setHasMore(d.has_more);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  useEffect(() => {
    loadTables();
  }, [env, enabled]);
  useEffect(() => {
    loadRows();
  }, [table, env, page]);

  if (backendBlock && activeIndex === backendBlock.index) {
    const d = safeJson<any>(backendBlock.content, {});
    return (
      <div className="h-full overflow-y-auto p-4">
        <div className="mb-2 text-[13px] font-medium">Backend · {d.action ?? backendBlock.command_name}</div>
        <pre className="rounded-m border border-line bg-card p-3 font-mono text-[12px] whitespace-pre-wrap">{JSON.stringify(d, null, 2)}</pre>
      </div>
    );
  }

  if (!enabled)
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <Database className="h-10 w-10 text-brand" />
        <div className="text-[15px] font-semibold">OpenAtoms Cloud</div>
        <p className="max-w-sm text-[13px] text-muted">Database, auth and storage for your app — separate dev and prod environments, no setup required.</p>
        {!readOnly && (
          <Button
            loading={enabling}
            onClick={async () => {
              setEnabling(true);
              try {
                await api.post(`/chats/${chatId}/cloud/enable`);
                mergeChat({ config: { ...(chat!.config as any), cloud_enabled: true } });
                toast.success("Cloud enabled");
              } catch (e) {
                toast.error(errMsg(e));
              } finally {
                setEnabling(false);
              }
            }}
          >
            Enable Cloud
          </Button>
        )}
      </div>
    );

  const cur = tables.find((t) => t.name === table);
  const cols = cur?.columns.map((c) => c.name) ?? (rows[0] ? Object.keys(rows[0]) : []);

  return (
    <div className="flex h-full">
      <div className="flex w-[200px] shrink-0 flex-col border-r border-line bg-card">
        <div className="flex h-[34px] items-center gap-1 border-b border-line px-2">
          <div className="inline-flex rounded-full bg-hover p-0.5 text-[11px]">
            {(["dev", "prod"] as const).map((e) => (
              <button key={e} onClick={() => setEnv(e)} className={cn("cursor-pointer rounded-full px-2 py-0.5", env === e && "bg-card shadow-card")}>
                {e}
              </button>
            ))}
          </div>
          <div className="flex-1" />
          <Button size="icon-sm" variant="ghost" onClick={loadTables} loading={loading}>
            {!loading && <RefreshCw className="h-3.5 w-3.5" />}
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {tables.length === 0 && <div className="p-3 text-[12px] text-muted">No tables yet</div>}
          {tables.map((t) => (
            <button
              key={t.name}
              onClick={() => {
                setTable(t.name);
                setPage(1);
              }}
              className={cn("flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-hover", t.name === table && "bg-brand-soft text-brand")}
            >
              <Table2 className="h-3.5 w-3.5" />
              <span className="flex-1 truncate">{t.name}</span>
              <span className="text-[10px] text-muted">{t.row_count}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        {table ? (
          <>
            <div className="flex h-[34px] shrink-0 items-center gap-1.5 border-b border-line px-3 text-[12px]">
              <span className="font-medium">{table}</span>
              <span className="text-muted">{cur?.row_count ?? 0} rows</span>
              <div className="flex-1" />
              <Button size="xs" variant="ghost" onClick={() => download(`/chats/${chatId}/database/${env}/tables/${table}/export`, `${table}.csv`).catch((e) => toast.error(errMsg(e)))}>
                <Download className="h-3 w-3" /> CSV
              </Button>
              {!readOnly && (
                <>
                  <Button size="xs" variant="ghost" onClick={() => imp.current?.click()}>
                    <Upload className="h-3 w-3" /> Import
                  </Button>
                  <input
                    ref={imp}
                    type="file"
                    accept=".csv"
                    hidden
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      e.target.value = "";
                      if (!f) return;
                      const fd = new FormData();
                      fd.append("file", f);
                      try {
                        await api.upload(`/chats/${chatId}/database/${env}/tables/${table}/import`, fd);
                        toast.success("Imported");
                        loadTables();
                        loadRows();
                      } catch (err) {
                        toast.error(errMsg(err));
                      }
                    }}
                  />
                </>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-full border-collapse text-[12px]">
                <thead className="sticky top-0 bg-bg-2">
                  <tr>
                    {cols.map((c) => (
                      <th key={c} className="border-b border-line px-3 py-1.5 text-left font-medium whitespace-nowrap text-fg-2">
                        {c}
                        <span className="ml-1 text-[10px] font-normal text-subtle">{cur?.columns.find((x) => x.name === c)?.type}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="hover:bg-hover">
                      {cols.map((c) => (
                        <td key={c} className="max-w-[260px] truncate border-b border-line px-3 py-1.5 font-mono text-[11.5px]">
                          {typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 && <div className="p-6 text-center text-[12px] text-muted">No rows</div>}
            </div>
            <div className="flex h-8 shrink-0 items-center justify-end gap-2 border-t border-line px-3 text-[12px]">
              <Button size="xs" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                Prev
              </Button>
              <span className="text-muted">Page {page}</span>
              <Button size="xs" variant="ghost" disabled={!hasMore} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          </>
        ) : (
          <PanelEmpty icon={Database} text="Select a table" />
        )}
      </div>
    </div>
  );
}
