"use client";
import { Paperclip } from "lucide-react";
import { openAuthed } from "@/lib/api";
import { segments } from "@/lib/delta";
import { Markdown } from "./markdown";
import { ToolCard } from "./tool-card";

export function DeltaView({ content, streaming }: { content: unknown; streaming?: boolean }) {
  const segs = segments(content);
  return (
    <div className="min-w-0">
      {segs.map((s, i) => {
        if (s.kind === "text") return s.text.trim() ? <Markdown key={i} text={s.text} /> : null;
        if (s.kind === "action") return <ToolCard key={i} uuid={s.uuid} />;
        if (s.kind === "attachment")
          return (
            <button
              key={i}
              onClick={() => openAuthed(s.url)}
              className="mr-1 mb-1 inline-flex cursor-pointer items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-[12px] hover:bg-active"
            >
              <Paperclip className="h-3 w-3" />
              {s.name}
            </button>
          );
        return null;
      })}
      {streaming && (
        <span className="ml-0.5 inline-flex gap-0.5 align-middle">
          <span className="typing-dot h-1 w-1 rounded-full bg-muted" />
          <span className="typing-dot h-1 w-1 rounded-full bg-muted" />
          <span className="typing-dot h-1 w-1 rounded-full bg-muted" />
        </span>
      )}
    </div>
  );
}
