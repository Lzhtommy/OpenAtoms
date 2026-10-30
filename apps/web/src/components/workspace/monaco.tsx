"use client";
import { DiffEditor, Editor } from "@monaco-editor/react";
import { useTheme } from "@/lib/theme";

const opts = {
  readOnly: true,
  minimap: { enabled: false },
  fontSize: 12.5,
  fontFamily: "IBM Plex Mono, ui-monospace, monospace",
  scrollBeyondLastLine: false,
  renderLineHighlight: "none" as const,
  automaticLayout: true,
  wordWrap: "on" as const,
  padding: { top: 8 },
};

export function CodeView({ value, language, path }: { value: string; language: string; path?: string }) {
  const dark = useTheme((s) => s.dark);
  return (
    <Editor
      height="100%"
      path={path}
      value={value}
      language={language}
      theme={dark ? "vs-dark" : "light"}
      options={opts}
      loading={<div className="p-4 text-[12px] text-muted">Loading editor…</div>}
    />
  );
}

export function DiffView({ before, after, language }: { before: string; after: string; language: string }) {
  const dark = useTheme((s) => s.dark);
  return (
    <DiffEditor
      height="100%"
      original={before}
      modified={after}
      language={language}
      theme={dark ? "vs-dark" : "light"}
      options={{ ...opts, renderSideBySide: false }}
    />
  );
}
