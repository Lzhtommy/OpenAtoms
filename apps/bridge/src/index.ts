/**
 * OpenAtoms bridge — injected by the gateway into every HTML page of a preview or
 * published app (docs/CONTRACT.md §7). Reports errors / console / route changes to
 * the parent workbench, supports visual element selection and shows a badge when
 * the app is opened directly (not inside the workbench iframe).
 */

declare global {
  interface Window {
    __oaBridge?: { version: string };
    __OA_HOME?: string;
  }
}

const VERSION = "1";
const MAX_ARG_LEN = 2000;

(() => {
  if (window.__oaBridge) return;
  window.__oaBridge = { version: VERSION };

  const inIframe = (() => {
    try {
      return window.self !== window.top;
    } catch {
      return true; // cross-origin parent
    }
  })();
  const sessionId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  function post(type: string, payload: unknown): void {
    if (!inIframe) return;
    try {
      window.parent.postMessage({ type, payload, protocolVersion: 1, sessionId }, "*");
    } catch {
      /* payload not cloneable — ignore */
    }
  }

  // ---------------------------------------------------------------- serialization
  function stringify(v: unknown): string {
    if (typeof v === "string") return v;
    if (v instanceof Error) return v.stack || `${v.name}: ${v.message}`;
    if (v === undefined) return "undefined";
    if (typeof v === "function") return `[Function ${(v as { name?: string }).name || "anonymous"}]`;
    if (typeof v === "symbol" || typeof v === "bigint") return v.toString();
    if (typeof Element !== "undefined" && v instanceof Element) return `<${v.tagName.toLowerCase()}>`;
    const seen = new WeakSet<object>();
    try {
      return JSON.stringify(v, (_k, val) => {
        if (typeof val === "object" && val !== null) {
          if (seen.has(val)) return "[Circular]";
          seen.add(val);
        }
        if (typeof val === "bigint") return val.toString();
        return val;
      });
    } catch {
      return String(v);
    }
  }
  const clip = (s: string) => (s.length > MAX_ARG_LEN ? s.slice(0, MAX_ARG_LEN) + "…" : s);

  // ---------------------------------------------------------------- errors
  window.addEventListener("error", (e: ErrorEvent) => {
    if (!(e instanceof ErrorEvent)) return; // resource load errors are not ErrorEvents
    post("mgx-appview-error", {
      message: e.message || String(e.error),
      stack: e.error && e.error.stack ? String(e.error.stack) : "",
      source: e.filename || "",
      line: e.lineno || 0,
      col: e.colno || 0,
    });
  });
  window.addEventListener("unhandledrejection", (e: PromiseRejectionEvent) => {
    const r = e.reason;
    post("mgx-appview-error", {
      message: "Unhandled promise rejection: " + (r instanceof Error ? r.message : clip(stringify(r))),
      stack: r instanceof Error && r.stack ? r.stack : "",
      source: "",
      line: 0,
      col: 0,
    });
  });

  // ---------------------------------------------------------------- console
  const levels = ["log", "info", "warn", "error", "debug"] as const;
  for (const level of levels) {
    const orig = console[level];
    if (typeof orig !== "function") continue;
    console[level] = function (...args: unknown[]) {
      try {
        post("mgx-appview-console", { level, args: args.map((a) => clip(stringify(a))) });
      } catch {
        /* never break the app's console */
      }
      return orig.apply(this, args);
    };
  }

  // ---------------------------------------------------------------- routes
  const currentPath = () => location.pathname + location.search + location.hash;
  let lastPath = "";
  function reportRoute(): void {
    const path = currentPath();
    if (path === lastPath) return;
    lastPath = path;
    post("mgx-appview-route", { path, title: document.title });
  }
  for (const fn of ["pushState", "replaceState"] as const) {
    const orig = history[fn];
    history[fn] = function (this: History, ...args: Parameters<History["pushState"]>) {
      const r = orig.apply(this, args);
      setTimeout(reportRoute, 0);
      return r;
    } as History["pushState"];
  }
  window.addEventListener("popstate", reportRoute);
  window.addEventListener("hashchange", reportRoute);

  function onReady(): void {
    lastPath = currentPath();
    post("mgx-appview-ready", { path: lastPath, title: document.title });
    if (!inIframe) mountBadge();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", onReady, { once: true });
  else onReady();

  // ---------------------------------------------------------------- design mode
  let designMode = false;
  let overlay: HTMLDivElement | null = null;
  let label: HTMLDivElement | null = null;

  function ensureOverlay(): void {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.setAttribute("data-oa-bridge", "overlay");
    Object.assign(overlay.style, {
      position: "fixed",
      pointerEvents: "none",
      zIndex: "2147483646",
      border: "2px solid #4267ff",
      background: "rgba(66,103,255,0.08)",
      borderRadius: "2px",
      transition: "all 60ms ease-out",
      display: "none",
    });
    label = document.createElement("div");
    label.setAttribute("data-oa-bridge", "label");
    Object.assign(label.style, {
      position: "fixed",
      pointerEvents: "none",
      zIndex: "2147483647",
      background: "#4267ff",
      color: "#fff",
      font: "500 11px/1.6 ui-monospace, monospace",
      padding: "0 6px",
      borderRadius: "3px",
      display: "none",
    });
    document.documentElement.append(overlay, label);
  }

  function isBridgeNode(el: Element | null): boolean {
    return !!el && !!el.closest("[data-oa-bridge]");
  }

  function cssEscape(s: string): string {
    return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function selectorFor(el: Element): string {
    const parts: string[] = [];
    let cur: Element | null = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement && parts.length < 6) {
      if (cur.id) {
        parts.unshift("#" + cssEscape(cur.id));
        break;
      }
      let part = cur.tagName.toLowerCase();
      const parent: Element | null = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur!.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(" > ");
  }

  function highlight(el: Element | null): void {
    if (!overlay || !label) return;
    if (!el || isBridgeNode(el)) {
      overlay.style.display = label.style.display = "none";
      return;
    }
    const r = el.getBoundingClientRect();
    Object.assign(overlay.style, {
      display: "block",
      top: r.top + "px",
      left: r.left + "px",
      width: r.width + "px",
      height: r.height + "px",
    });
    label.textContent = el.tagName.toLowerCase() + (el.classList.length ? "." + Array.from(el.classList).slice(0, 2).join(".") : "");
    Object.assign(label.style, {
      display: "block",
      top: Math.max(0, r.top - 18) + "px",
      left: r.left + "px",
    });
  }

  // Selected element stays outlined (and follows scroll/resize/HMR re-layout) until another pick.
  let selected: Element | null = null;
  let selBox: HTMLDivElement | null = null;

  function ensureSelBox(): HTMLDivElement {
    if (!selBox) {
      selBox = document.createElement("div");
      selBox.setAttribute("data-oa-bridge", "selected");
      Object.assign(selBox.style, {
        position: "fixed",
        pointerEvents: "none",
        zIndex: "2147483645",
        outline: "2px solid #4267ff",
        outlineOffset: "1px",
        borderRadius: "2px",
        display: "none",
      });
      document.documentElement.append(selBox);
    }
    return selBox;
  }

  function placeSelBox(): void {
    if (!selBox) return;
    if (!designMode || !selected || !selected.isConnected) {
      selBox.style.display = "none";
      return;
    }
    const r = selected.getBoundingClientRect();
    Object.assign(selBox.style, { display: "block", top: r.top + "px", left: r.left + "px", width: r.width + "px", height: r.height + "px" });
  }

  /** The editable unit is the nearest element stamped with a source location by the template's dev plugin. */
  function target(el: Element | null): Element | null {
    if (!el || isBridgeNode(el)) return null;
    return el.closest("[data-oa-loc]") || el;
  }

  /** Static text only: the element has no element children, so replacing its text is a safe source edit. */
  function ownText(el: Element): { text: string; editable: boolean } {
    const editable = el.children.length === 0 && !["img", "input", "svg", "video", "br", "hr"].includes(el.tagName.toLowerCase());
    return { text: ((el as HTMLElement).innerText || el.textContent || "").trim().slice(0, 500), editable };
  }

  const STYLE_KEYS = [
    "fontSize", "fontWeight", "color", "backgroundColor", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "marginTop", "marginRight", "marginBottom", "marginLeft", "borderRadius", "display", "textAlign",
  ] as const;

  function describe(el: Element): Record<string, unknown> {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const styles: Record<string, string> = {};
    for (const k of STYLE_KEYS) styles[k] = cs[k];
    const { text, editable } = ownText(el);
    return {
      selector: selectorFor(el),
      loc: el.getAttribute("data-oa-loc"),
      tag: el.tagName.toLowerCase(),
      text,
      text_editable: editable,
      classes: Array.from(el.classList),
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      styles,
      path: currentPath(),
    };
  }

  const onMove = (e: MouseEvent) => highlight(target(e.target as Element));
  const onClick = (e: MouseEvent) => {
    const el = target(e.target as Element);
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    selected = el;
    ensureSelBox();
    placeSelBox();
    post("appviewer:sync-design-selection", describe(el));
  };
  const swallow = (e: Event) => {
    if (!isBridgeNode(e.target as Element)) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      selected = null;
      placeSelBox();
      post("appviewer:sync-design-selection", null);
    }
  };
  const onLayout = () => {
    placeSelBox();
    highlight(null);
  };

  function setDesignMode(enabled: boolean): void {
    if (enabled === designMode) return;
    designMode = enabled;
    if (enabled) {
      ensureOverlay();
      document.addEventListener("mousemove", onMove, true);
      document.addEventListener("click", onClick, true);
      document.addEventListener("mousedown", swallow, true);
      document.addEventListener("submit", swallow, true);
      document.addEventListener("keydown", onKey, true);
      window.addEventListener("scroll", onLayout, true);
      window.addEventListener("resize", onLayout);
      document.documentElement.style.cursor = "crosshair";
    } else {
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("mousedown", swallow, true);
      document.removeEventListener("submit", swallow, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onLayout, true);
      window.removeEventListener("resize", onLayout);
      document.documentElement.style.cursor = "";
      selected = null;
      highlight(null);
      placeSelBox();
    }
  }

  /**
   * Optimistic preview of a staged edit. New Tailwind classes usually have no CSS yet (the dev server's JIT only
   * sees classes once they are in the source), so the workbench also sends the equivalent inline `style`.
   */
  type PreviewMsg = { loc?: string; add?: string[]; remove?: string[]; text?: string | null; style?: Record<string, string> };
  function applyPreview(m: PreviewMsg): void {
    if (!m.loc) return;
    const els = document.querySelectorAll(`[data-oa-loc="${cssEscape(m.loc)}"]`);
    els.forEach((el) => {
      (m.remove || []).forEach((c) => el.classList.remove(c));
      (m.add || []).forEach((c) => el.classList.add(c));
      if (m.style) Object.assign((el as HTMLElement).style, m.style);
      if (typeof m.text === "string" && ownText(el).editable) el.textContent = m.text;
    });
    placeSelBox();
    if (selected && els.length) post("appviewer:sync-design-selection", describe(selected));
  }

  // ---------------------------------------------------------------- parent → app
  window.addEventListener("message", (e: MessageEvent) => {
    if (e.source !== window.parent || !e.data || typeof e.data !== "object") return;
    const msg = e.data as { type?: string; path?: string; enabled?: boolean } & PreviewMsg;
    switch (msg.type) {
      case "appviewer:navigate":
        if (typeof msg.path === "string") {
          history.pushState(null, "", msg.path);
          window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
          reportRoute();
        }
        break;
      case "appviewer:reload":
        location.reload();
        break;
      case "appviewer:design-mode":
        setDesignMode(!!msg.enabled);
        break;
      case "appviewer:apply-preview":
        applyPreview(msg);
        break;
    }
  });

  // ---------------------------------------------------------------- badge
  function mountBadge(): void {
    const meta = document.querySelector('meta[name="oa-badge"]');
    if (meta && meta.getAttribute("content") === "off") return;
    if (document.querySelector('[data-oa-bridge="badge"]')) return;
    const home = window.__OA_HOME || "";
    const badge = document.createElement(home ? "a" : "div") as HTMLAnchorElement;
    badge.setAttribute("data-oa-bridge", "badge");
    if (home) {
      badge.href = home;
      badge.target = "_blank";
      badge.rel = "noopener";
    }
    Object.assign(badge.style, {
      position: "fixed",
      right: "12px",
      bottom: "12px",
      zIndex: "2147483645",
      display: "flex",
      alignItems: "center",
      gap: "6px",
      padding: "6px 10px 6px 8px",
      borderRadius: "999px",
      background: "rgba(12,12,12,0.86)",
      color: "#fff",
      font: "500 12px/1 'IBM Plex Sans', system-ui, sans-serif",
      textDecoration: "none",
      boxShadow: "0 2px 10px rgba(0,0,0,.18)",
    });
    const dot = document.createElement("span");
    Object.assign(dot.style, { width: "10px", height: "10px", borderRadius: "50%", background: "#4267ff", display: "inline-block" });
    const text = document.createElement("span");
    text.textContent = "Made with OpenAtoms";
    const close = document.createElement("span");
    close.textContent = "×";
    close.setAttribute("aria-label", "Hide badge");
    Object.assign(close.style, { marginLeft: "4px", opacity: "0.6", cursor: "pointer", fontSize: "14px" });
    close.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      badge.remove();
    });
    badge.append(dot, text, close);
    document.body.appendChild(badge);
  }
})();

export {};
