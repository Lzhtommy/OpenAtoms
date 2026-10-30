"use client";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

export const Markdown = memo(function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("md text-[14px]", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={{ a: (p) => <a {...p} target="_blank" rel="noreferrer" /> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
