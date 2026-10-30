"use client";
import { BookOpenText, ExternalLink, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Markdown } from "@/components/chat/markdown";
import { fetchFileText } from "@/lib/api";
import { safeJson } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelBar, PanelEmpty, useBlockSelection } from "./common";

interface ResearchDoc {
  queries?: string[];
  findings?: { query: string; summary: string; sources: string[] }[];
  report?: string;
  report_markdown?: string;
}

/** Deep research (Iris): navigation steps with sources, then the final cited report. */
export function ResearchPanel() {
  const chatId = useChat((s) => s.chatId)!;
  const mode = useChat((s) => s.mode);
  const { list, selected } = useBlockSelection(["Research"]);
  const d = safeJson<ResearchDoc>(selected?.content || "{}", {});
  const [report, setReport] = useState<string | null>(null);
  useEffect(() => {
    setReport(d.report_markdown ?? null);
    if (!d.report_markdown && d.report && mode !== "replay") {
      fetchFileText(chatId, d.report).then(setReport).catch(() => setReport(null));
    }
  }, [selected?.index, d.report, d.report_markdown, chatId, mode]);
  if (!list.length || !selected) return <PanelEmpty icon={BookOpenText} text="No research yet" />;

  return (
    <div className="flex h-full flex-col">
      <PanelBar>
        <BookOpenText className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium">Deep research</span>
        <span className="text-muted">
          {d.findings?.length ?? 0} finding(s){d.report ? ` · ${d.report}` : ""}
        </span>
      </PanelBar>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <ol className="flex flex-col gap-2">
          {(d.findings ?? []).map((f, i) => (
            <li key={i} className="rounded-l border border-line bg-card p-3">
              <div className="flex items-center gap-1.5 text-[11px] text-subtle">
                <Search className="h-3 w-3" /> {f.query || `step ${i + 1}`}
              </div>
              <div className="mt-1 text-[13px]">{f.summary}</div>
              {f.sources?.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {(f.sources ?? []).map((u) => (
                    <a key={u} href={u} target="_blank" rel="noreferrer" className="inline-flex max-w-[260px] items-center gap-1 truncate rounded-full bg-hover px-2 py-0.5 text-[11px] text-brand">
                      <ExternalLink className="h-3 w-3 shrink-0" />
                      <span className="truncate">{u.replace(/^https?:\/\//, "")}</span>
                    </a>
                  ))}
                </div>
              )}
            </li>
          ))}
          {d.queries && d.queries.length > (d.findings?.length ?? 0) && (
            <li className="rounded-l border border-dashed border-line p-3 text-[12px] text-muted">
              Next: {d.queries[d.queries.length - 1]}
            </li>
          )}
        </ol>
        {report && (
          <article className="mt-5 rounded-xl border border-line bg-card p-5">
            <Markdown text={report} />
          </article>
        )}
      </div>
    </div>
  );
}
