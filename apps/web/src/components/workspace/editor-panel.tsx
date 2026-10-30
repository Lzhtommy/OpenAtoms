"use client";
import { Code2, GitCompare, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AgentAvatar } from "@/components/ui/avatar";
import type { BlockTimeline } from "@/lib/types";
import { basename, cn, langFromPath, safeJson } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelEmpty, useBlockSelection, useSnapshot } from "./common";
import { CodeView, DiffView } from "./monaco";

function pathOf(t: BlockTimeline) {
  return (t.extra_data?.path as string) || t.src_path?.replace(/^\/workspace\//, "") || basename(t.file) || `block-${t.index}`;
}

export function EditorPanel() {
  const { list, selected } = useBlockSelection(["Editor"]);
  const showBlock = useChat((s) => s.showBlock);
  const [tabs, setTabs] = useState<string[]>([]);
  const [diff, setDiff] = useState(false);
  const snapRaw = useSnapshot(selected);
  const snap = safeJson<{ path?: string; before?: string; after?: string } | null>(snapRaw, null);

  // latest block per file path
  const latestByPath = useMemo(() => {
    const m = new Map<string, BlockTimeline>();
    for (const t of list) m.set(pathOf(t), t);
    return m;
  }, [list]);

  const curPath = selected ? pathOf(selected) : null;
  useEffect(() => {
    if (curPath && !tabs.includes(curPath)) setTabs((t) => [...t.slice(-7), curPath]);
  }, [curPath]);

  if (!selected) return <PanelEmpty icon={Code2} text="No code written yet" />;
  const after = selected.is_running ? selected.content : snap?.after ?? selected.content;
  const before = snap?.before ?? "";
  const lang = (selected.extra_data?.language as string) || langFromPath(curPath || "");

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[34px] shrink-0 items-center border-b border-line">
        <div className="flex min-w-0 flex-1 overflow-x-auto">
          {tabs.map((p) => {
            const t = latestByPath.get(p);
            return (
              <div
                key={p}
                onClick={() => t && showBlock(t.index)}
                className={cn(
                  "group flex h-[34px] shrink-0 cursor-pointer items-center gap-1.5 border-r border-line px-3 text-[12px] text-muted",
                  p === curPath && "bg-card text-fg",
                )}
              >
                {t?.is_running && <Loader2 className="h-3 w-3 animate-spin text-brand" />}
                <span title={p}>{basename(p)}</span>
                <button
                  className="rounded opacity-0 group-hover:opacity-100 hover:bg-hover"
                  onClick={(e) => {
                    e.stopPropagation();
                    setTabs(tabs.filter((x) => x !== p));
                  }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
        <button
          disabled={!snap}
          onClick={() => setDiff(!diff)}
          className={cn(
            "mx-2 flex cursor-pointer items-center gap-1 rounded-full px-2 py-0.5 text-[11px] text-muted hover:bg-hover disabled:opacity-40",
            diff && "bg-brand-soft text-brand",
          )}
        >
          <GitCompare className="h-3 w-3" /> Diff
        </button>
      </div>
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-line bg-bg-2 px-3 text-[11px] text-muted">
        <AgentAvatar role={selected.block_owner} size={14} />
        <span className="font-mono">{curPath}</span>
        <span>· {selected.command_name}</span>
      </div>
      <div className="min-h-0 flex-1">
        {diff && snap ? <DiffView before={before} after={after} language={lang} /> : <CodeView value={after} language={lang} path={`blk-${selected.index}-${curPath}`} />}
      </div>
    </div>
  );
}
