"use client";
import { NotebookPen } from "lucide-react";
import { safeJson } from "@/lib/utils";
import { PanelEmpty, useBlockSelection, useSnapshot } from "./common";
import { StorageImage } from "./storage-image";

interface Cell {
  source: string;
  outputs?: { type: "stream" | "error" | "image"; text?: string; data?: string; name?: string; ename?: string; evalue?: string }[];
}

export function NotebookPanel() {
  const { selected } = useBlockSelection(["Notebook"]);
  const snap = useSnapshot(selected && !selected.content ? selected : undefined);
  if (!selected) return <PanelEmpty icon={NotebookPen} text="No notebook cells yet" />;
  const d = safeJson<{ cells?: Cell[] }>(selected.content || snap || "{}", {});
  return (
    <div className="h-full overflow-y-auto p-4">
      {(d.cells ?? []).map((c, i) => (
        <div key={i} className="mb-4 overflow-hidden rounded-l border border-line bg-card">
          <div className="flex">
            <div className="w-10 shrink-0 bg-bg-2 pt-2 text-center font-mono text-[11px] text-subtle">[{i + 1}]</div>
            <pre className="min-w-0 flex-1 overflow-x-auto p-2 font-mono text-[12px] whitespace-pre text-fg">{c.source}</pre>
          </div>
          {(c.outputs ?? []).length > 0 && (
            <div className="border-t border-line p-2 pl-12">
              {c.outputs!.map((o, j) =>
                o.type === "image" && o.data ? (
                  <StorageImage key={j} src={imgSrc(o.data)} className="my-1 max-w-full rounded-m" />
                ) : (
                  <pre key={j} className={`font-mono text-[12px] whitespace-pre-wrap ${o.type === "error" ? "text-danger" : "text-fg-2"}`}>
                    {o.type === "error" ? `${o.ename ?? "Error"}: ${o.evalue ?? o.text ?? ""}` : o.text}
                  </pre>
                ),
              )}
            </div>
          )}
        </div>
      ))}
      {selected.is_running && <div className="text-[12px] text-muted">Executing…</div>}
    </div>
  );
}

function imgSrc(data: string) {
  if (data.startsWith("data:") || /\.(png|jpe?g|svg|webp)$/i.test(data)) return data;
  return `data:image/png;base64,${data}`;
}
