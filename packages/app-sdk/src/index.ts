/**
 * OpenAtoms app SDK — talks to the OpenAtoms Cloud backend of a generated app.
 *
 * The app's own origin proxies `/api/*` to the platform (see gateway), so the
 * default base URL is same-origin. Zero dependencies; works in any modern browser.
 *
 *   import { createClient } from "@/lib/oa";
 *   const oa = createClient();
 *   const todos = await oa.entities.Todo.list();
 *   await oa.entities.Todo.create({ title: "Ship it", done: false });
 */

export interface ClientOptions {
  /** Origin of the app backend. Defaults to same-origin (""). */
  baseUrl?: string;
  /** localStorage key for the app user token. */
  tokenKey?: string;
  /** Custom fetch implementation (tests / SSR). */
  fetch?: typeof fetch;
}

export interface AppUser {
  id: number;
  email: string;
  [k: string]: unknown;
}

export interface EntityRecord {
  id: number | string;
  created_at?: string;
  updated_at?: string;
  owner_id?: number | null;
  [k: string]: unknown;
}

export interface ListOptions {
  /** Field to sort by; prefix with "-" for descending, e.g. "-created_at". */
  sort?: string;
  limit?: number;
  skip?: number;
}

export interface EntityClient<T extends EntityRecord = EntityRecord> {
  list(opts?: ListOptions): Promise<T[]>;
  filter(query: Record<string, unknown>, opts?: ListOptions): Promise<T[]>;
  get(id: T["id"]): Promise<T>;
  create(data: Partial<T>): Promise<T>;
  update(id: T["id"], data: Partial<T>): Promise<T>;
  delete(id: T["id"]): Promise<void>;
  bulkCreate(rows: Partial<T>[]): Promise<T[]>;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface GenTextOptions {
  messages: ChatMessage[];
  model?: string;
  /** Called with each streamed text chunk. */
  onChunk?: (chunk: string) => void;
  signal?: AbortSignal;
}

export class OAError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "OAError";
    this.status = status;
    this.body = body;
  }
}

const memoryStore = new Map<string, string>();
const store = {
  get(k: string): string | null {
    try {
      return globalThis.localStorage ? globalThis.localStorage.getItem(k) : memoryStore.get(k) ?? null;
    } catch {
      return memoryStore.get(k) ?? null;
    }
  },
  set(k: string, v: string | null): void {
    try {
      if (globalThis.localStorage) {
        if (v === null) globalThis.localStorage.removeItem(k);
        else globalThis.localStorage.setItem(k, v);
        return;
      }
    } catch {
      /* fall through to memory */
    }
    if (v === null) memoryStore.delete(k);
    else memoryStore.set(k, v);
  },
};

export function createClient(options: ClientOptions = {}) {
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
  const tokenKey = options.tokenKey ?? "oa_app_token";
  const doFetch: typeof fetch = options.fetch ?? ((...a) => globalThis.fetch(...a));
  const api = (path: string) => `${baseUrl}/api/v1${path}`;

  function headers(json = true): Record<string, string> {
    const h: Record<string, string> = {};
    if (json) h["Content-Type"] = "application/json";
    const t = store.get(tokenKey);
    if (t) h["Authorization"] = `Bearer ${t}`;
    return h;
  }

  async function request<T>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
    const isForm = typeof FormData !== "undefined" && body instanceof FormData;
    const res = await doFetch(api(path), {
      method,
      headers: headers(!isForm && body !== undefined),
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      credentials: "include",
      ...init,
    });
    const text = await res.text();
    let data: unknown = undefined;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!res.ok) {
      const msg =
        (data && typeof data === "object" && ("detail" in data || "message" in data)
          ? String((data as { detail?: unknown; message?: unknown }).detail ?? (data as { message?: unknown }).message)
          : undefined) || `Request failed with status ${res.status}`;
      throw new OAError(res.status, msg, data);
    }
    return data as T;
  }

  function qs(params: Record<string, unknown>): string {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      q.set(k, typeof v === "object" ? JSON.stringify(v) : String(v));
    }
    const s = q.toString();
    return s ? `?${s}` : "";
  }

  function entity<T extends EntityRecord>(name: string): EntityClient<T> {
    const base = `/entities/${encodeURIComponent(name)}`;
    return {
      list: (opts = {}) => request<T[]>("GET", base + qs({ ...opts })),
      filter: (query, opts = {}) => request<T[]>("GET", base + qs({ query, ...opts })),
      get: (id) => request<T>("GET", `${base}/${encodeURIComponent(String(id))}`),
      create: (data) => request<T>("POST", base, data),
      update: (id, data) => request<T>("PUT", `${base}/${encodeURIComponent(String(id))}`, data),
      delete: async (id) => {
        await request<unknown>("DELETE", `${base}/${encodeURIComponent(String(id))}`);
      },
      bulkCreate: (rows) => request<T[]>("POST", `${base}/batch`, rows),
    };
  }

  const entityCache = new Map<string, EntityClient>();
  const entities = new Proxy({} as Record<string, EntityClient>, {
    get(_t, prop) {
      if (typeof prop !== "string" || prop === "then") return undefined;
      let c = entityCache.get(prop);
      if (!c) {
        c = entity(prop);
        entityCache.set(prop, c);
      }
      return c;
    },
  });

  const auth = {
    getToken: (): string | null => store.get(tokenKey),
    setToken: (token: string | null): void => store.set(tokenKey, token),
    isAuthenticated: (): boolean => !!store.get(tokenKey),
    async register(email: string, password: string): Promise<{ token: string; user: AppUser }> {
      const r = await request<{ token: string; user: AppUser }>("POST", "/auth/register", { email, password });
      store.set(tokenKey, r.token);
      return r;
    },
    async login(email: string, password: string): Promise<{ token: string; user: AppUser }> {
      const r = await request<{ token: string; user: AppUser }>("POST", "/auth/login", { email, password });
      store.set(tokenKey, r.token);
      return r;
    },
    async me(): Promise<AppUser | null> {
      if (!store.get(tokenKey)) return null;
      try {
        return await request<AppUser>("GET", "/auth/me");
      } catch (e) {
        if (e instanceof OAError && e.status === 401) {
          store.set(tokenKey, null);
          return null;
        }
        throw e;
      }
    },
    async logout(): Promise<void> {
      try {
        await request<unknown>("POST", "/auth/logout");
      } finally {
        store.set(tokenKey, null);
      }
    },
  };

  const storage = {
    async upload(file: Blob, filename?: string): Promise<{ url: string; key?: string }> {
      const fd = new FormData();
      fd.append("file", file, filename ?? (file as File).name ?? "upload");
      return request<{ url: string; key?: string }>("POST", "/storage/upload", fd);
    },
    fileUrl: (key: string): string => api(`/storage/files/${key.split("/").map(encodeURIComponent).join("/")}`),
  };

  const ai = {
    /** Generate text. Streams when onChunk is provided; always resolves with the full text. */
    async gentxt(opts: GenTextOptions): Promise<string> {
      const res = await doFetch(api("/aihub/gentxt"), {
        method: "POST",
        headers: { ...headers(true), Accept: "text/event-stream" },
        body: JSON.stringify({ messages: opts.messages, model: opts.model, stream: true }),
        credentials: "include",
        signal: opts.signal,
      });
      if (!res.ok || !res.body) {
        throw new OAError(res.status, `gentxt failed with status ${res.status}`, await res.text().catch(() => ""));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let full = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const event = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of event.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const payload = line.slice(5).trim();
            if (payload === "[DONE]") return full;
            try {
              const msg = JSON.parse(payload) as { content?: string; error?: string };
              if (msg.error) throw new OAError(500, msg.error);
              if (msg.content) {
                full += msg.content;
                opts.onChunk?.(msg.content);
              }
            } catch (e) {
              if (e instanceof OAError) throw e;
            }
          }
        }
      }
      return full;
    },
  };

  return {
    auth,
    entities: entities as Record<string, EntityClient> & { [name: string]: EntityClient },
    storage,
    ai,
    config: () => request<{ chat_id: string; env: string }>("GET", "/config"),
  };
}

export type OAClient = ReturnType<typeof createClient>;

/** Default client bound to the current origin. */
export const oa = createClient();
export default oa;
