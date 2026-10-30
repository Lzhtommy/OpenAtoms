"use client";
import { ExternalLink, Globe, Search } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { safeJson } from "@/lib/utils";
import { PanelEmpty, useBlockSelection, useSnapshot } from "./common";
import { StorageImage } from "./storage-image";

interface PageData {
  url?: string;
  title?: string;
  screenshot?: string | null;
  text?: string;
}
interface SearchData {
  query?: string;
  results?: { title: string; url: string; content?: string }[];
  answer?: string;
}

export function BrowserPanel() {
  const { selected } = useBlockSelection(["Browser", "Search"]);
  const snap = useSnapshot(selected && !selected.content ? selected : undefined);
  if (!selected) return <PanelEmpty icon={Globe} text="No browsing activity yet" />;
  const raw = selected.content || snap || "{}";
  if (selected.block_type === "Search") {
    const d = safeJson<SearchData>(raw, {});
    return (
      <div className="h-full overflow-y-auto p-5">
        <div className="flex items-center gap-2 rounded-full border border-line-strong bg-card px-4 py-2 text-[13px]">
          <Search className="h-4 w-4 text-muted" />
          {d.query}
        </div>
        {d.answer && (
          <div className="mt-4 rounded-l border border-line bg-card p-4">
            <Markdown text={d.answer} />
          </div>
        )}
        <div className="mt-4 flex flex-col gap-3">
          {(d.results ?? []).map((r, i) => (
            <a key={i} href={r.url} target="_blank" rel="noreferrer" className="rounded-l border border-line bg-card p-3 hover:border-line-strong">
              <div className="text-[13px] font-medium text-brand">{r.title}</div>
              <div className="truncate text-[11px] text-positive">{r.url}</div>
              {r.content && <div className="mt-1 line-clamp-3 text-[12px] text-fg-2">{r.content}</div>}
            </a>
          ))}
        </div>
      </div>
    );
  }
  const d = safeJson<PageData>(raw, {});
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[34px] shrink-0 items-center gap-2 border-b border-line px-3">
        <Globe className="h-3.5 w-3.5 text-muted" />
        <div className="min-w-0 flex-1 truncate rounded-full bg-bg-2 px-3 py-0.5 text-[12px] text-fg-2">{d.url || selected.extra_data?.page_url}</div>
        {d.url && (
          <a href={d.url} target="_blank" rel="noreferrer" className="text-muted hover:text-fg">
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto bg-bg-2 p-4">
        {d.title && <div className="mb-2 text-[13px] font-medium">{d.title}</div>}
        {d.screenshot ? (
          <StorageImage src={d.screenshot} className="w-full rounded-m border border-line shadow-card" alt={d.title} />
        ) : (
          <pre className="rounded-m border border-line bg-card p-3 text-[12px] whitespace-pre-wrap text-fg-2">{d.text || (selected.is_running ? "Loading…" : "No content")}</pre>
        )}
        {d.screenshot && d.text && (
          <details className="mt-3">
            <summary className="cursor-pointer text-[12px] text-muted">Page text</summary>
            <pre className="mt-2 rounded-m border border-line bg-card p-3 text-[12px] whitespace-pre-wrap text-fg-2">{d.text}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
