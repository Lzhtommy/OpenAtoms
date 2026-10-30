import { uid } from "./utils";

export class ApiError extends Error {
  code: number;
  status: number;
  data: unknown;
  constructor(code: number, message: string, status: number, data?: unknown) {
    super(message);
    this.code = code;
    this.status = status;
    this.data = data;
  }
}

const TOKEN_KEY = "oa_token";
const WS_KEY = "oa_workspace";

export const tokenStore = {
  get(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(TOKEN_KEY);
  },
  set(t: string | null) {
    if (typeof window === "undefined") return;
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  },
  workspace(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(WS_KEY);
  },
  setWorkspace(id: number | null) {
    if (typeof window === "undefined") return;
    if (id) localStorage.setItem(WS_KEY, String(id));
    else localStorage.removeItem(WS_KEY);
  },
};

let refreshing: Promise<boolean> | null = null;

async function refreshToken(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const r = await fetch("/api/v1/user/refresh", { method: "POST", credentials: "include" });
        if (!r.ok) return false;
        const j = await r.json();
        if (j?.code === 0 && j.data?.access_token) {
          tokenStore.set(j.data.access_token);
          return true;
        }
        return false;
      } catch {
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

export interface ReqOpts {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  form?: FormData;
  raw?: boolean; // return Response
  noAuthRedirect?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: ReqOpts["query"]) {
  const base = path.startsWith("/api/") ? path : `/api/v1${path.startsWith("/") ? path : "/" + path}`;
  if (!query) return base;
  const qs = Object.entries(query)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return qs ? `${base}?${qs}` : base;
}

export function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "X-Request-ID": uid() };
  const t = tokenStore.get();
  if (t) h["Authorization"] = `Bearer ${t}`;
  const ws = tokenStore.workspace();
  if (ws) h["X-Workspace-Id"] = ws;
  return h;
}

export async function request<T = any>(path: string, opts: ReqOpts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = authHeaders();
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(buildUrl(path, opts.query), {
    method: opts.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    credentials: "include",
    signal: opts.signal,
  });
  if (res.status === 401 && !retried && !path.includes("/user/refresh") && !path.includes("/user/login")) {
    if (await refreshToken()) return request<T>(path, opts, true);
    tokenStore.set(null);
    if (!opts.noAuthRedirect && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    }
    throw new ApiError(4401, "please relogin", 401);
  }
  if (opts.raw) {
    if (!res.ok) throw new ApiError(res.status, res.statusText, res.status);
    return res as unknown as T;
  }
  let json: any = null;
  try {
    json = await res.json();
  } catch {
    throw new ApiError(5000, `HTTP ${res.status}`, res.status);
  }
  if (!json || typeof json !== "object" || !("code" in json)) {
    if (!res.ok) throw new ApiError(5000, json?.detail ?? `HTTP ${res.status}`, res.status);
    return json as T;
  }
  if (json.code !== 0) throw new ApiError(json.code, json.message || "error", res.status, json.data);
  return json.data as T;
}

export const api = {
  get: <T = any>(p: string, query?: ReqOpts["query"], o: ReqOpts = {}) => request<T>(p, { ...o, query }),
  post: <T = any>(p: string, body?: unknown, o: ReqOpts = {}) => request<T>(p, { ...o, method: "POST", body: body ?? {} }),
  put: <T = any>(p: string, body?: unknown) => request<T>(p, { method: "PUT", body: body ?? {} }),
  patch: <T = any>(p: string, body?: unknown) => request<T>(p, { method: "PATCH", body: body ?? {} }),
  del: <T = any>(p: string, query?: ReqOpts["query"]) => request<T>(p, { method: "DELETE", query }),
  upload: <T = any>(p: string, form: FormData) => request<T>(p, { method: "POST", form }),
};

export function errMsg(e: unknown) {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/** Download a binary endpoint with auth (zip/csv). */
export async function download(path: string, filename: string) {
  const res = await request<Response>(path, { raw: true });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/** Fetch a .storage snapshot / file content as text. */
export async function fetchFileText(chatId: string, path: string, share = false): Promise<string> {
  if (share) {
    const r = await fetch(buildUrl(`/share/${chatId}/files`, { path }));
    const j = await r.json().catch(() => null);
    if (j?.code === 0) return typeof j.data?.content === "string" ? j.data.content : JSON.stringify(j.data);
    return "";
  }
  const d = await request<{ type: string; content?: string }>(`/chats/${chatId}/files`, { query: { path } });
  return d?.content ?? "";
}

export function rawFileUrl(chatId: string, path: string) {
  return buildUrl(`/chats/${chatId}/files/raw`, { path });
}

/** Open an auth-protected /api/ URL (attachment) in a new tab via blob. */
export async function openAuthed(url: string) {
  if (!url.startsWith("/api/")) {
    window.open(url, "_blank");
    return;
  }
  const w = window.open("", "_blank");
  try {
    const res = await request<Response>(url, { raw: true });
    const blob = await res.blob();
    const obj = URL.createObjectURL(blob);
    if (w) w.location.href = obj;
    else window.open(obj, "_blank");
  } catch {
    w?.close();
  }
}
