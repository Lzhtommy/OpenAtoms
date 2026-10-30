import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function uid() {
  return (globalThis.crypto?.randomUUID?.() ?? Math.random().toString(16).slice(2) + Date.now().toString(16)).replace(
    /-/g,
    "",
  );
}

/** Parse server ISO time; naive timestamps (no offset) are UTC. */
export function parseTs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const hasTz = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasTz ? iso : iso + "Z").getTime();
}

export function timeAgo(iso: string | null | undefined, lang: "zh" | "en" = "zh") {
  if (!iso) return "";
  const d = parseTs(iso);
  const s = Math.max(0, (Date.now() - d) / 1000);
  const zh = lang === "zh";
  if (s < 60) return zh ? "刚刚" : "just now";
  if (s < 3600) return zh ? `${Math.floor(s / 60)} 分钟前` : `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return zh ? `${Math.floor(s / 3600)} 小时前` : `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return zh ? `${Math.floor(s / 86400)} 天前` : `${Math.floor(s / 86400)}d ago`;
  return new Date(d).toLocaleDateString();
}

export function fmtTime(iso: string) {
  const d = new Date(parseTs(iso));
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function fmtDate(iso: string) {
  return new Date(parseTs(iso)).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

export function basename(p: string | null | undefined) {
  if (!p) return "";
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}

export function langFromPath(p: string) {
  const ext = p.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    json: "json",
    css: "css",
    scss: "scss",
    html: "html",
    md: "markdown",
    py: "python",
    sh: "shell",
    yml: "yaml",
    yaml: "yaml",
    sql: "sql",
    go: "go",
    toml: "ini",
  };
  return map[ext] ?? "plaintext";
}

export function safeJson<T = any>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

export function copyText(t: string) {
  return navigator.clipboard?.writeText(t);
}
