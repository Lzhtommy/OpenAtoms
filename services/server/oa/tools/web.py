"""Browser, Browser-RT (checkui_run) and Search blocks.

Uses Playwright (headless Chromium) when the `playwright` package and a browser are installed; otherwise a
pure-HTTP browser (fetch + HTML→text, link following) keeps every tool functional.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as htmllib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from oa.core.storage import get_storage
from oa.tools.base import ToolContext, ToolResult, tool, truncate_for_llm

log = logging.getLogger("oa.tools.web")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) " \
     "Chrome/130.0 Safari/537.36 OpenAtoms"
VIEW_CHARS = 4000


# ---------------------------------------------------------------- html helpers
_DROP = re.compile(r"<(script|style|noscript|svg|template)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK = re.compile(r"</?(p|div|br|li|h[1-6]|tr|section|article|header|footer|ul|ol|table|nav)[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_LINK = re.compile(r"<a\s[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)


def html_to_text(raw: str) -> str:
    s = _DROP.sub(" ", raw)
    s = _BLOCK.sub("\n", s)
    s = htmllib.unescape(_TAG.sub(" ", s))
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def extract_links(raw: str, base: str) -> list[tuple[str, str]]:
    out, seen = [], set()
    for href, inner in _LINK.findall(raw):
        text = re.sub(r"\s+", " ", htmllib.unescape(_TAG.sub(" ", inner))).strip()
        url = urljoin(base, href)
        if not text or url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        out.append((text[:80], url))
    return out


def extract_title(raw: str) -> str:
    m = _TITLE.search(raw)
    return re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip() if m else ""


# ---------------------------------------------------------------- sessions
@dataclass
class PageState:
    url: str = ""
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    offset: int = 0
    screenshot: bytes | None = None
    status: int = 0


def playwright_available() -> bool:
    if os.environ.get("OA_DISABLE_PLAYWRIGHT"):
        return False
    try:
        import playwright.async_api  # noqa: F401

        return True
    except Exception:
        return False


class _PW:
    """Lazily-launched shared Chromium; one page per chat."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._pages: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def browser(self):
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True, args=["--no-sandbox"])
            return self._browser

    async def page(self, chat_id: str):
        p = self._pages.get(chat_id)
        if p is None or p.is_closed():
            b = await self.browser()
            ctx = await b.new_context(viewport={"width": 1280, "height": 800}, user_agent=UA)
            p = await ctx.new_page()
            self._pages[chat_id] = p
        return p

    async def close(self, chat_id: str) -> None:
        p = self._pages.pop(chat_id, None)
        if p is not None:
            try:
                await p.context.close()
            except Exception:
                pass


_pw = _PW()
_http_pages: dict[str, PageState] = {}


async def close_session(chat_id: str) -> None:
    _http_pages.pop(chat_id, None)
    if playwright_available():
        await _pw.close(chat_id)


async def _pw_state(page: Any) -> PageState:
    raw = await page.content()
    st = PageState(url=page.url, title=await page.title(), text=html_to_text(raw), links=extract_links(raw, page.url))
    try:
        st.screenshot = await page.screenshot(type="png")
    except Exception:
        st.screenshot = None
    return st


async def _http_fetch(url: str) -> PageState:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers={"User-Agent": UA}) as c:
        r = await c.get(url)
    ctype = r.headers.get("content-type", "")
    raw = r.text if ("html" in ctype or "text" in ctype or not ctype) else ""
    return PageState(url=str(r.url), title=extract_title(raw), text=html_to_text(raw) if raw else f"[{ctype}]",
                     links=extract_links(raw, str(r.url)) if raw else [], status=r.status_code)


# ---------------------------------------------------------------- browser tools
async def _emit_browser(ctx: ToolContext, command: str, st: PageState) -> None:
    async with ctx.record(command, "Browser", extra={"page_url": st.url}) as blk:
        shot_path = None
        if st.screenshot:
            h = hashlib.sha1(st.screenshot).hexdigest()[:8]
            shot_path = f".storage/{blk.index}/{h}/screenshot.png"
            await get_storage().put_bytes(f"chats/{ctx.chat_id}/{shot_path}", st.screenshot, "image/png")
        view = st.text[st.offset: st.offset + VIEW_CHARS]
        doc = json.dumps({"url": st.url, "title": st.title, "screenshot": shot_path, "text": view},
                         ensure_ascii=False)
        await blk.replace(doc)
        await blk.complete(snapshots={"page.json": doc}, extra_data={"page_url": st.url})


def _observation(st: PageState) -> str:
    view = st.text[st.offset: st.offset + VIEW_CHARS]
    more = len(st.text) - (st.offset + VIEW_CHARS)
    links = "\n".join(f"[{i}] {t} → {u}" for i, (t, u) in enumerate(st.links[:30]))
    return (f"URL: {st.url}\nTitle: {st.title}\n\n{view}"
            + (f"\n…({more} more chars, use browser_scroll)" if more > 0 else "")
            + (f"\n\nLinks:\n{links}" if links else ""))


async def _goto(ctx: ToolContext, url: str) -> PageState:
    if not urlparse(url).scheme:
        url = "https://" + url
    if playwright_available():
        page = await _pw.page(ctx.chat_id)
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(0.8)
        st = await _pw_state(page)
        st.status = resp.status if resp else 0
        return st
    st = await _http_fetch(url)
    _http_pages[ctx.chat_id] = st
    return st


@tool("browser_goto", "Open a URL in the browser and return the visible page text and links.",
      {"url": {"type": "string"}}, block_type="Browser")
async def browser_goto(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        st = await _goto(ctx, str(args["url"]))
    except Exception as e:
        return ToolResult(f"Error: could not open {args['url']}: {e}", ok=False)
    await _emit_browser(ctx, "browser_goto", st)
    return ToolResult(truncate_for_llm(_observation(st)))


def _current(ctx: ToolContext) -> PageState | None:
    return _http_pages.get(ctx.chat_id)


@tool("browser_click", "Click a link or button by its visible text (or a link index from the last observation).",
      {"target": {"type": "string", "description": "Visible text, CSS selector, or link index"}}, block_type="Browser")
async def browser_click(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    target = str(args["target"]).strip()
    if playwright_available():
        page = await _pw.page(ctx.chat_id)
        try:
            if target.isdigit():
                links = extract_links(await page.content(), page.url)
                await page.goto(links[int(target)][1], wait_until="domcontentloaded", timeout=30000)
            else:
                loc = page.get_by_text(target, exact=False).first
                if await loc.count() == 0:
                    loc = page.locator(target).first
                await loc.click(timeout=8000)
                await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(0.6)
            st = await _pw_state(page)
        except Exception as e:
            return ToolResult(f"Error: could not click {target!r}: {e}", ok=False)
    else:
        cur = _current(ctx)
        if cur is None:
            return ToolResult("Error: no page open; call browser_goto first.", ok=False)
        url = None
        if target.isdigit() and int(target) < len(cur.links):
            url = cur.links[int(target)][1]
        else:
            t = target.lower()
            url = next((u for txt, u in cur.links if t in txt.lower()), None) or \
                next((u for _, u in cur.links if t in u.lower()), None)
        if not url:
            return ToolResult(f"Error: no link matching {target!r}. Links:\n" +
                              "\n".join(f"[{i}] {t}" for i, (t, _) in enumerate(cur.links[:30])), ok=False)
        try:
            st = await _http_fetch(url)
        except Exception as e:
            return ToolResult(f"Error: could not open {url}: {e}", ok=False)
        _http_pages[ctx.chat_id] = st
    await _emit_browser(ctx, "browser_click", st)
    return ToolResult(truncate_for_llm(_observation(st)))


@tool("browser_scroll", "Scroll the current page up or down.",
      {"direction": {"type": "string", "enum": ["down", "up"], "optional": True}}, block_type="Browser")
async def browser_scroll(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    down = str(args.get("direction") or "down") != "up"
    if playwright_available():
        page = await _pw.page(ctx.chat_id)
        await page.mouse.wheel(0, 800 if down else -800)
        await asyncio.sleep(0.4)
        st = await _pw_state(page)
        y = await page.evaluate("window.scrollY")
        st.offset = min(len(st.text), int(y / 800 * VIEW_CHARS))
    else:
        st = _current(ctx)
        if st is None:
            return ToolResult("Error: no page open; call browser_goto first.", ok=False)
        st.offset = max(0, min(len(st.text) - 1, st.offset + (VIEW_CHARS if down else -VIEW_CHARS)))
    await _emit_browser(ctx, "browser_scroll", st)
    return ToolResult(truncate_for_llm(_observation(st)))


@tool("browser_type", "Type text into an input (by CSS selector or placeholder text), optionally pressing Enter.",
      {"target": {"type": "string"}, "text": {"type": "string"},
       "submit": {"type": "boolean", "optional": True}}, block_type="Browser")
async def browser_type(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if not playwright_available():
        cur = _current(ctx)
        # HTTP fallback: support GET search forms by appending ?q=
        if cur is None:
            return ToolResult("Error: no page open; call browser_goto first.", ok=False)
        if args.get("submit"):
            u = urlparse(cur.url)
            url = f"{u.scheme}://{u.netloc}/search?q={httpx.QueryParams({'q': args['text']})['q']}"
            try:
                st = await _http_fetch(url)
            except Exception as e:
                return ToolResult(f"Error: {e}", ok=False)
            _http_pages[ctx.chat_id] = st
            await _emit_browser(ctx, "browser_type", st)
            return ToolResult(truncate_for_llm(_observation(st)))
        return ToolResult("Typing without submitting has no visible effect in the text browser.", ok=False)
    page = await _pw.page(ctx.chat_id)
    target = str(args["target"])
    try:
        loc = page.get_by_placeholder(target).first
        if await loc.count() == 0:
            loc = page.locator(target).first
        await loc.fill(str(args["text"]), timeout=8000)
        if args.get("submit"):
            await loc.press("Enter")
            await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(0.5)
        st = await _pw_state(page)
    except Exception as e:
        return ToolResult(f"Error: could not type into {target!r}: {e}", ok=False)
    await _emit_browser(ctx, "browser_type", st)
    return ToolResult(truncate_for_llm(_observation(st)))


# ---------------------------------------------------------------- checkui (Browser-RT)
async def _http_check(upstream: str, path: str) -> dict[str, Any]:
    errors: list[str] = []
    status = 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        try:
            r = await c.get(upstream.rstrip("/") + path)
            status = r.status_code
            if status >= 400:
                errors.append(f"GET {path} returned HTTP {status}")
            elif 'id="root"' not in r.text and "<script" not in r.text:
                errors.append("Page HTML has no app mount point / scripts")
            # Vite compiles modules on request: a transform error surfaces as HTTP 500 with the message
            for mod in re.findall(r'<script[^>]+type="module"[^>]+src="([^"]+)"', r.text):
                if mod.startswith("/@"):
                    continue
                mr = await c.get(upstream.rstrip("/") + mod)
                if mr.status_code >= 400:
                    errors.append(f"Module {mod} failed to compile (HTTP {mr.status_code}): {mr.text[:1500]}")
                else:
                    for imp in re.findall(r'from\s+"(/src/[^"]+)"', mr.text)[:20]:
                        ir = await c.get(upstream.rstrip("/") + imp)
                        if ir.status_code >= 400:
                            errors.append(f"Module {imp} failed to compile: {ir.text[:1500]}")
        except httpx.HTTPError as e:
            errors.append(f"Preview unreachable: {e}")
    return {"status": status, "errors": errors, "console": [], "screenshot": None}


async def _pw_check(upstream: str, path: str) -> dict[str, Any]:
    browser = await _pw.browser()
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    errors: list[str] = []
    console: list[str] = []
    origin = upstream.rstrip("/")
    # Vite's HMR client pings/sockets are noise, not app failures.
    ignore = ("/@vite/client", "/__vite_ping", "/@react-refresh", "hot-update", "favicon.ico")

    def hmr(req: Any) -> bool:  # Vite dev-server plumbing (HMR socket + reconnect ping), not the app
        return req.resource_type == "websocket" or "x-vite-ping" in (req.headers.get("accept") or "") \
            or any(x in req.url for x in ignore)

    def on_failed(req: Any) -> None:
        if not hmr(req) and "ERR_ABORTED" not in str(req.failure):
            errors.append(f"Request failed: {req.method} {req.url} ({req.failure})")

    def on_console(msg: Any) -> None:
        text = msg.text
        if msg.type == "error" and (text.startswith("Failed to load resource")  # reported via network events
                                    or ("WebSocket connection to" in text and "token=" in text)):  # HMR socket
            return
        (errors if msg.type == "error" else console).append(f"[{msg.type}] {text}")

    def on_response(resp: Any) -> None:
        if resp.status >= 400 and resp.url.startswith(origin) and not hmr(resp.request):
            errors.append(f"HTTP {resp.status} for {resp.request.method} {resp.url}")

    page.on("pageerror", lambda e: errors.append(f"Uncaught: {e}"))
    page.on("console", on_console)
    page.on("requestfailed", on_failed)
    page.on("response", on_response)
    try:
        resp = await page.goto(upstream.rstrip("/") + path, wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(1.0)
        empty = await page.evaluate("() => (document.getElementById('root')?.innerHTML || 'x').trim().length === 0")
        if empty:
            errors.append("The app rendered an empty #root (blank page).")
        shot = await page.screenshot(type="png")
        return {"status": resp.status if resp else 0, "errors": errors, "console": console[-50:], "screenshot": shot}
    finally:
        await ctx.close()


@tool("checkui_run", "Load the running app preview in a headless browser and report runtime/console errors.",
      {"path": {"type": "string", "description": "Route to check, default /", "optional": True}},
      block_type="Browser-RT")
async def checkui_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = "/" + str(args.get("path") or "/").lstrip("/")
    await ctx.rc.sandbox()  # make sure the sandbox (and dev server) is up
    upstream = await ctx.rc.preview_upstream()
    async with ctx.record("checkui_run", "Browser-RT") as blk:
        if not upstream:
            logs = await _dev_logs(ctx)
            res = {"url": path, "status": 0, "errors": ["Dev server is not reachable."] + logs, "console": []}
        else:
            try:
                res = await (_pw_check(upstream, path) if playwright_available() else _http_check(upstream, path))
            except Exception as e:
                log.warning("checkui failed: %s", e)
                res = await _http_check(upstream, path)
            res["url"] = path
        shot = res.pop("screenshot", None)
        shot_path = None
        if shot:
            h = hashlib.sha1(shot).hexdigest()[:8]
            shot_path = f".storage/{blk.index}/{h}/screenshot.png"
            await get_storage().put_bytes(f"chats/{ctx.chat_id}/{shot_path}", shot, "image/png")
        res["screenshot"] = shot_path
        doc = json.dumps(res, ensure_ascii=False)
        await blk.replace(doc)
        await blk.complete(status="success" if not res["errors"] else "failed", snapshots={"check.json": doc})
    if res["errors"]:
        return ToolResult("Preview has errors:\n" + truncate_for_llm("\n".join(res["errors"]), 6000), ok=False)
    return ToolResult(f"Preview {path} loaded without errors (HTTP {res.get('status')}).")


async def _dev_logs(ctx: ToolContext) -> list[str]:
    try:
        sb = await ctx.rc.sandbox()
        lines = [ln async for ln in sb.process_logs("dev", follow=False, tail=30)]
        return [ln for ln in lines if ln.strip()][-15:]
    except Exception:
        return []


# ---------------------------------------------------------------- search
async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    from oa.config import settings

    key = settings.tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
    if key:
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post("https://api.tavily.com/search", json={
                    "api_key": key, "query": query, "max_results": max_results, "include_answer": True,
                })
                r.raise_for_status()
                d = r.json()
            return {"query": query, "answer": d.get("answer") or "", "results": [
                {"title": x.get("title", ""), "url": x.get("url", ""), "content": (x.get("content") or "")[:800]}
                for x in d.get("results", [])]}
        except Exception as e:
            log.warning("tavily search failed: %s", e)
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        results = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
                             r.text, re.DOTALL):
            href = htmllib.unescape(m.group(1))
            if "uddg=" in href:
                from urllib.parse import parse_qs, unquote

                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            results.append({"title": html_to_text(m.group(2)), "url": href, "content": html_to_text(m.group(3))})
            if len(results) >= max_results:
                break
        return {"query": query, "answer": "", "results": results}
    except Exception as e:
        log.warning("ddg search failed: %s", e)
        return {"query": query, "answer": "", "results": [], "error": str(e)}


@tool("searchenhancedqa_run", "Search the web and return the top results with snippets.",
      {"query": {"type": "string"}}, block_type="Search")
async def searchenhancedqa_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args["query"])
    async with ctx.record("searchenhancedqa_run", "Search", extra={"query": query}) as blk:
        res = await web_search(query)
        doc = json.dumps(res, ensure_ascii=False)
        await blk.replace(doc)
        await blk.complete(snapshots={"search.json": doc})
    if not res["results"]:
        return ToolResult(f"No results for {query!r}" + (f" ({res['error']})" if res.get("error") else "") +
                          ". Continue with your own knowledge.")
    body = "\n\n".join(f"[{i + 1}] {r['title']}\n{r['url']}\n{r['content']}" for i, r in enumerate(res["results"]))
    return ToolResult(truncate_for_llm((f"Answer: {res['answer']}\n\n" if res.get("answer") else "") + body))
