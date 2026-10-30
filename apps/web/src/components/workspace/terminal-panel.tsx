"use client";
import "@xterm/xterm/css/xterm.css";
import { SquareTerminal } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import { cn } from "@/lib/utils";
import { useChat } from "@/store/chat";
import { PanelEmpty, useBlockSelection, useSnapshot } from "./common";

export function TerminalPanel() {
  const { list, selected } = useBlockSelection(["Terminal"]);
  const showBlock = useChat((s) => s.showBlock);
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const written = useRef<{ index: number; len: number }>({ index: -1, len: 0 });
  const snap = useSnapshot(selected && !selected.content ? selected : undefined);
  const text = selected?.content || snap || "";

  useEffect(() => {
    let disposed = false;
    let ro: ResizeObserver | null = null;
    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      const t = new Terminal({
        convertEol: true,
        disableStdin: true,
        fontFamily: "IBM Plex Mono, ui-monospace, monospace",
        fontSize: 12,
        scrollback: 5000,
        theme: { background: "#0f0f0f", foreground: "#e6e6e6" },
      });
      const f = new FitAddon();
      t.loadAddon(f);
      t.open(host.current);
      f.fit();
      term.current = t;
      fit.current = f;
      written.current = { index: -1, len: 0 };
      ro = new ResizeObserver(() => {
        try {
          f.fit();
        } catch {
          /* ignore */
        }
      });
      ro.observe(host.current);
      writer.current();
    })();
    return () => {
      disposed = true;
      ro?.disconnect();
      term.current?.dispose();
      term.current = null;
    };
  }, []);

  const writeText = () => {
    const t = term.current;
    if (!t) return;
    const cur = selected;
    const content = cur?.content || snap || "";
    if (!cur) return;
    if (written.current.index !== cur.index || content.length < written.current.len) {
      t.reset();
      t.write(colorize(content));
      written.current = { index: cur.index, len: content.length };
    } else if (content.length > written.current.len) {
      t.write(content.slice(written.current.len));
      written.current.len = content.length;
    }
  };
  const writer = useRef(writeText);
  writer.current = writeText;

  useEffect(writeText, [selected?.index, text]);

  return (
    <div className="flex h-full">
      <div className="w-[200px] shrink-0 overflow-y-auto border-r border-line bg-card">
        {list.length === 0 && <div className="p-3 text-[12px] text-muted">No commands yet</div>}
        {list.map((t) => (
          <button
            key={t.index}
            onClick={() => showBlock(t.index)}
            className={cn(
              "flex w-full cursor-pointer items-center gap-1.5 border-b border-line px-2.5 py-1.5 text-left font-mono text-[11px] text-fg-2 hover:bg-hover",
              selected?.index === t.index && "bg-brand-soft text-brand",
            )}
          >
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", t.is_running ? "animate-pulse bg-warning" : t.extra_data?.exit_code ? "bg-danger" : "bg-positive")} />
            <span className="truncate">$ {t.extra_data?.cmd ?? (t.content.split("\n")[0] || "").replace(/^\$ /, "")}</span>
          </button>
        ))}
      </div>
      <div className="relative min-w-0 flex-1 bg-[#0f0f0f] p-2">
        <div ref={host} className="h-full w-full" />
        {!selected && (
          <div className="absolute inset-0">
            <PanelEmpty icon={SquareTerminal} text="No terminal output yet" />
          </div>
        )}
      </div>
    </div>
  );
}

function colorize(s: string) {
  // highlight "$ cmd" header lines
  return s.replace(/^\$ (.*)$/gm, "\x1b[36m$ $1\x1b[0m");
}
