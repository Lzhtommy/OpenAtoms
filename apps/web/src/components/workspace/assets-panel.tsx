"use client";
import { Download, FileText, ImageIcon } from "lucide-react";
import { toast } from "sonner";
import { Markdown } from "@/components/chat/markdown";
import { api, errMsg } from "@/lib/api";
import type { AssetItem, BlockTimeline } from "@/lib/types";
import { basename, cn, safeJson } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelBar, PanelEmpty, useBlockSelection } from "./common";
import { StorageImage } from "./storage-image";

interface AssetsDoc {
  items?: AssetItem[];
  summary?: string;
}

async function openWorkspaceFile(chatId: string, path: string) {
  try {
    const r = await api.get<Response>(`/chats/${chatId}/files/raw`, { path }, { raw: true });
    const url = URL.createObjectURL(await r.blob());
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    toast.error(errMsg(e));
  }
}

function itemsOf(t: BlockTimeline): AssetItem[] {
  return safeJson<AssetsDoc>(t.content || "{}", {}).items ?? [];
}

/** Generated images, analyzed attachments and documents (Assets + File blocks). */
export function AssetsPanel() {
  const chatId = useChat((s) => s.chatId)!;
  const { list, selected } = useBlockSelection(["Assets", "File"]);
  const showBlock = useChat((s) => s.showBlock);
  if (!list.length) return <PanelEmpty icon={ImageIcon} text="No images or documents yet" />;
  const doc = safeJson<AssetsDoc>(selected?.content || "{}", {});
  const images = list.flatMap((t) => itemsOf(t).filter((i) => i.kind === "image").map((i) => ({ ...i, t })));
  const docs = list.flatMap((t) => itemsOf(t).filter((i) => i.kind !== "image").map((i) => ({ ...i, t })));

  return (
    <div className="flex h-full flex-col">
      <PanelBar>
        <ImageIcon className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium">Assets</span>
        <span className="text-muted">
          {images.length} image(s) · {docs.length} document(s)
        </span>
      </PanelBar>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {selected && doc.summary && (
          <div className="mb-4 rounded-l border border-line bg-card p-3">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-subtle">{selected.command_name}</div>
            <Markdown text={doc.summary} />
          </div>
        )}
        {images.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            {images.map((it, i) => (
              <button
                key={`${it.t.index}-${i}`}
                onClick={() => showBlock(it.t.index)}
                className={cn(
                  "group cursor-pointer overflow-hidden rounded-l border border-line bg-card text-left hover:border-line-strong",
                  selected?.index === it.t.index && "border-brand",
                )}
              >
                {it.preview ? (
                  <StorageImage src={it.preview} alt={it.prompt ?? it.path} className="aspect-[16/10] w-full object-cover" />
                ) : (
                  <div className="flex aspect-[16/10] items-center justify-center bg-bg-2">
                    <ImageIcon className="h-6 w-6 text-subtle" />
                  </div>
                )}
                <div className="p-2">
                  <div className="truncate font-mono text-[11px]">{it.path}</div>
                  <div className="text-[11px] text-muted">
                    {it.width && it.height ? `${it.width}×${it.height}` : ""}
                    {it.source ? ` · ${it.source}` : ""}
                    {it.tone ? ` · ${it.tone}` : ""}
                  </div>
                  {it.dominant_colors && (
                    <div className="mt-1 flex gap-1">
                      {it.dominant_colors.map((c) => (
                        <span key={c.hex} title={c.hex} className="h-3 w-3 rounded-full border border-line" style={{ background: c.hex }} />
                      ))}
                    </div>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
        {docs.length > 0 && (
          <div className="mt-4 flex flex-col gap-2">
            {docs.map((it, i) => (
              <div
                key={`${it.t.index}-${i}`}
                className={cn("rounded-l border border-line bg-card p-3", selected?.index === it.t.index && "border-brand")}
              >
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-muted" />
                  <button className="min-w-0 flex-1 cursor-pointer truncate text-left font-mono text-[12px]" onClick={() => showBlock(it.t.index)}>
                    {basename(it.path)}
                  </button>
                  <span className="text-[11px] text-muted">
                    {it.kind === "pdf" && it.pages ? `${it.pages} page(s)` : it.chars ? `${it.chars} chars` : it.kind}
                  </span>
                  <button
                    className="cursor-pointer rounded-m p-1 text-muted hover:bg-hover hover:text-fg"
                    onClick={() => openWorkspaceFile(chatId, it.path)}
                    aria-label="open file"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                </div>
                {it.excerpt && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-m bg-bg-2 p-2 text-[11px] text-fg-2">{it.excerpt}</pre>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
