# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "python-socketio[asyncio_client]>=5.11"]
# ///
"""End-to-end smoke test against a running stack (`make dev`).

Covers the PLAN demos: register → create project with first prompt → live socket events →
agent run (lite or full) → files + build → version → preview via gateway → publish → public URL →
second round → v2 → restore v1 → queue while running → share replay → cloud entities via app API.

Usage: uv run scripts/e2e.py [--mode lite|full] [--api http://127.0.0.1:8100] [--gateway http://127.0.0.1:8080]
"""
from __future__ import annotations

import argparse
import json
import asyncio
import re
import sys
import time
import uuid

import httpx
import socketio

OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


class E2EError(Exception):
    pass


def check(cond: bool, label: str) -> None:
    print(f"  {OK if cond else FAIL} {label}")
    if not cond:
        raise E2EError(label)


class Api:
    def __init__(self, base: str) -> None:
        self.c = httpx.AsyncClient(base_url=base.rstrip("/") + "/api/v1", timeout=60, trust_env=False)
        self.token = ""

    async def req(self, method: str, path: str, **kw):  # noqa: ANN003, ANN201
        headers = kw.pop("headers", {})
        if self.token:
            headers["Authorization"] = self.token
        r = await self.c.request(method, path, headers=headers, **kw)
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise E2EError(f"{method} {path} → {r.status_code} non-json: {r.text[:200]}") from e
        if body.get("code") != 0:
            raise E2EError(f"{method} {path} → {r.status_code} {body}")
        return body["data"]


def delta_text(content) -> str:  # noqa: ANN001
    if isinstance(content, list):
        return "".join(op["insert"] for op in content if isinstance(op.get("insert"), str))
    return str(content)


async def wait_idle(api: Api, chat_id: str, timeout: float = 600) -> dict:
    t0 = time.time()
    seen_running = False
    while time.time() - t0 < timeout:
        chat = await api.req("GET", f"/chats/{chat_id}")
        running = chat["metadata"]["is_running"]
        seen_running |= running
        if not running and (seen_running or time.time() - t0 > 3):
            return chat
        await asyncio.sleep(1)
    raise E2EError("timed out waiting for run to finish")


async def wait_version(api: Api, chat_id: str, n: int, timeout: float = 300) -> list[dict]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        vs = await api.req("GET", f"/chats/{chat_id}/versions")
        ready = [v for v in vs if v["status"] in ("ready", "failed")]
        if len(ready) >= n:
            return vs
        await asyncio.sleep(1)
    raise E2EError(f"timed out waiting for version {n}")


def gw_get(gateway: str, url: str, **kw) -> httpx.Response:  # noqa: ANN003
    """Fetch an *.app.localhost URL through the gateway using an explicit Host header."""
    m = re.match(r"https?://([^/]+)(/.*)?$", url)
    assert m
    host, path = m.group(1), m.group(2) or "/"
    with httpx.Client(trust_env=False, timeout=60) as c:  # never route *.localhost through a system proxy
        return c.get(gateway.rstrip("/") + path, headers={"Host": host, **kw.pop("headers", {})}, **kw)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8100")
    ap.add_argument("--gateway", default="http://127.0.0.1:8080")
    ap.add_argument("--mode", default="lite", choices=["lite", "full"])
    ap.add_argument("--prompt", default="做一个番茄钟页面")
    ap.add_argument("--p9", action="store_true", help="also verify P9 features (boost, specialists, media, automations, Expo)")
    ap.add_argument("--only-p9", action="store_true", help="register, then run only the P9 section")
    args = ap.parse_args()

    api = Api(args.api)
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"

    print("P1 账号与项目")
    data = await api.req("POST", "/user/register", json={"email": email, "password": "pass12345", "name": "E2E"})
    api.token = data["access_token"]
    if args.only_p9:
        await p9(api, args)
        await api.c.aclose()
        print(f"\n{OK} E2E passed (P9)")
        return
    me = await api.req("GET", "/user/detail")
    check(me["email"] == email, "register + detail")
    cfg = await api.req("GET", "/configs/general")
    check("default_model" in cfg, "configs/general")
    bal = await api.req("GET", "/transaction/balance")
    check(bal["credits"] > 0, f"signup credits = {bal['credits']}")

    events: list[tuple[str, dict]] = []
    sio = socketio.AsyncClient(reconnection=False)

    @sio.on("*")
    async def _any(event, data):  # noqa: ANN001
        events.append((event, data))

    joined = asyncio.Event()

    @sio.on("join_chat_ack")
    async def _ack(data):  # noqa: ANN001
        if data.get("ok"):
            joined.set()

    await sio.connect(args.api, transports=["websocket"], socketio_path="/socket.io", auth={"token": api.token})
    check(sio.connected, "socket connected")

    chat = await api.req("POST", "/chats", json={"title": "E2E", "config": {"agent_mode": args.mode}})
    chat_id = chat["chat_id"]
    await sio.emit("join_chat", {"chat_id": chat_id})
    await asyncio.wait_for(joined.wait(), 5)
    check(True, f"join_chat ack ({chat_id})")

    print(f"P2 Agent 运行（{args.mode}）")
    res = await api.req("POST", f"/chats/{chat_id}/messages",
                        json={"content": [{"insert": args.prompt}], "metadata": {"agent_mode": args.mode}})
    check(not res["queued"], "message accepted, run started")
    chat = await wait_idle(api, chat_id)
    hist = await api.req("GET", f"/chats/{chat_id}/messages")
    msgs = hist["messages"]
    roles = {m["role"] for m in msgs}
    check(any(m["type"] == "think" for m in msgs), f"think messages streamed ({len(msgs)} msgs, roles={sorted(roles)})")
    cmds = [a["command_name"] for a in hist["action_datas"]]
    check("editor_write" in cmds and "terminal_run" in cmds, f"tool calls: {sorted(set(cmds))}")
    check(all(a["status"] != "running" for a in hist["action_datas"]), "all action_datas finished")
    if args.mode == "full":
        check({"Team Leader", "Engineer"} <= roles, "team roles participated")
        check(any(m["type"] == "emoji" for m in msgs) or any(m.get("reply_messages") for m in msgs), "emoji ok receipts")
    names = [e for e, _ in events]
    check("msg:create" in names and "msg:update" in names, "socket msg:create/msg:update received")
    check("timeline:start" in names and "timeline:complete" in names, "socket timeline:* received")
    seqs = [d["seq"] for _, d in events if isinstance(d, dict) and "seq" in d]
    check(seqs == sorted(seqs), "event seq monotonic")
    replay = await api.req("GET", f"/chats/{chat_id}/events", params={"after": seqs[len(seqs) // 2]})
    check(len(replay["events"]) > 0, "events replay after seq")

    timelines = await api.req("GET", f"/chats/{chat_id}/block-timelines")
    check(len(timelines) >= 2, f"{len(timelines)} block timelines")
    snap = next(t for t in timelines if t["file"])
    f = await api.req("GET", f"/chats/{chat_id}/files", params={"path": snap["file"]})
    check(f["type"] == "file", "block snapshot readable via .storage path")
    tree = await api.req("GET", f"/chats/{chat_id}/files/tree")
    check(any(n["name"] == "src" for n in tree), "workspace file tree")

    print("P3 预览")
    ticket = await api.req("POST", f"/chats/{chat_id}/preview-ticket")
    r = gw_get(args.gateway, ticket["url"], follow_redirects=False)
    check(r.status_code in (302, 303, 307) and "oa_preview=" in r.headers.get("set-cookie", ""),
          f"/__preview_auth exchanges ticket for cookie ({r.status_code})")
    sc = r.headers["set-cookie"].lower()
    check("partitioned" in sc and "samesite=none" in sc, "preview cookie is iframe-safe (SameSite=None; Partitioned)")
    preview_cookie = {"Cookie": r.headers["set-cookie"].split(";")[0]}
    t0 = time.time()
    while time.time() - t0 < 120:
        r = gw_get(args.gateway, chat["uris"]["dev"] + "/", headers=preview_cookie)
        if r.status_code == 200:
            break
        await asyncio.sleep(2)
    check(r.status_code == 200 and "__oa/bridge.js" in r.text,
          f"preview via gateway with bridge injected ({r.status_code} {r.text[:120]!r})")
    r = gw_get(args.gateway, chat["uris"]["dev"] + "/")
    check(r.status_code == 403, "private preview without cookie → 403")

    print("P4 版本与发布")
    vs = await wait_version(api, chat_id, 1)
    check(vs[0]["status"] == "ready", f"version {vs[0]['version']} ready")
    dep = await api.req("PUT", f"/chats/{chat_id}/deployment", json={"version": vs[0]["version"], "is_always_latest": False})
    for _ in range(60):
        dep = await api.req("GET", f"/chats/{chat_id}/deployment")
        if dep["status"] != "deploying":
            break
        await asyncio.sleep(1)
    check(dep["status"] == "success", f"deployed → {dep['url']}")
    r = gw_get(args.gateway, dep["url"] + "/")
    check(r.status_code == 200 and "<div id=\"root\"" in r.text, "published URL serves app")
    r = gw_get(args.gateway, dep["url"] + "/some/spa/route")
    check(r.status_code == 200, "SPA fallback")
    await api.req("PATCH", f"/chats/{chat_id}", json={"access_mode": "public"})

    print("P5 第二轮 + 队列 + 恢复")
    await api.req("POST", f"/chats/{chat_id}/messages", json={"content": [{"insert": "把标题改成 E2E Timer"}]})
    await asyncio.sleep(0.5)
    q = await api.req("POST", f"/chats/{chat_id}/messages", json={"content": [{"insert": "再加一个页脚"}]})
    check(q["queued"], "message while running → prompt queue")
    t0 = time.time()
    while time.time() - t0 < 900:
        vs = await api.req("GET", f"/chats/{chat_id}/versions")
        pq = await api.req("GET", f"/chats/{chat_id}/prompt-queue")
        c = await api.req("GET", f"/chats/{chat_id}")
        if len([v for v in vs if v["status"] == "ready"]) >= 3 and not pq["items"] and not c["metadata"]["is_running"]:
            break
        await asyncio.sleep(2)
    check(len(vs) >= 3, f"queued prompt auto-ran → versions {[v['version'] for v in vs]}")
    await api.req("POST", f"/chats/{chat_id}/versions/v1/restore")
    vs2 = await wait_version(api, chat_id, len(vs) + 1)
    check(len(vs2) == len(vs) + 1, "restore v1 creates new version")

    print("P6 Cloud 应用后端")
    await api.req("POST", f"/chats/{chat_id}/cloud/enable")
    dev_host = chat["uris"]["dev"]
    cookie_hdr = preview_cookie
    m = re.match(r"https?://([^/]+)", dev_host)
    host = m.group(1)
    with httpx.Client(base_url=args.gateway, headers={"Host": host, **cookie_hdr}, timeout=30, trust_env=False) as gc:
        r = gc.post("/api/v1/entities/Note", json={"text": "hello"})
        check(r.status_code in (200, 201), f"app entities create ({r.status_code})")
        r = gc.get("/api/v1/entities/Note")
        check(r.status_code == 200 and any(row.get("text") == "hello" for row in r.json()), "app entities list")
    tables = await api.req("GET", f"/chats/{chat_id}/database/dev/tables")
    check(any(t["name"].lower() == "note" for t in tables), "Cloud DB table browser")

    print("P7 分享与发现")
    share = httpx.Client(trust_env=False).get(args.api + f"/api/v1/share/{chat_id}", timeout=30).json()
    check(share["code"] == 0 and len(share["data"]["messages"]) > 0, "public share replay (no auth)")
    disc = await api.req("GET", "/discover")
    check(any(p["chat_id"] == chat_id for p in disc["items"]), "project listed in discover")
    fork = await api.req("POST", f"/chats/{chat_id}/fork", json={})
    check(fork["chat_id"] != chat_id, "remix/fork")

    await sio.disconnect()
    if args.p9:
        await p9(api, args)
    await api.c.aclose()
    print(f"\n{OK} E2E passed ({args.mode}{' + P9' if args.p9 else ''})")


async def read_file(api: Api, chat_id: str, path: str) -> str:
    f = await api.req("GET", f"/chats/{chat_id}/files", params={"path": path})
    return f.get("content") or ""


async def gw_preview(api: Api, args, chat_id: str) -> httpx.Response:  # noqa: ANN001
    """Authenticate a (private) preview through its ticket and fetch it via the gateway."""
    ticket = await api.req("POST", f"/chats/{chat_id}/preview-ticket")
    r = gw_get(args.gateway, ticket["url"], follow_redirects=False)
    cookie = {"Cookie": r.headers.get("set-cookie", "").split(";")[0]}
    host = re.match(r"https?://([^/]+)", ticket["url"]).group(1)
    t0 = time.time()
    while time.time() - t0 < 120:
        r = gw_get(args.gateway, f"http://{host}/", headers=cookie)
        if r.status_code == 200:
            return r
        await asyncio.sleep(2)
    return r


async def stop_sandbox(api: Api, chat_id: str) -> None:
    """Free the project's sandbox (the free plan runs 2 at once) before moving on to the next project."""
    await api.req("POST", f"/chats/{chat_id}/runtime/stop")


async def p9(api: Api, args) -> None:  # noqa: ANN001, PLR0915
    print("P9.1 竞赛模式 (Best-of-N)")
    for p in (await api.req("GET", "/chats"))["items"]:  # P1–P7 projects may still hold sandboxes
        await stop_sandbox(api, p["chat_id"])
    chat = await api.req("POST", "/chats", json={"title": "Boost", "config": {"agent_mode": "lite"}})
    cid = chat["chat_id"]
    res = await api.req("POST", f"/chats/{cid}/messages", json={
        "content": [{"insert": "Make a counter"}], "use_boost": True,
        "boost_models": ["oa-mock", "oa-mock-2"]})  # free plan: 2 concurrent sandboxes
    check(res["boost"]["status"] == "running" and len(res["boost"]["items"]) == 2, "boost started with 2 candidates")
    t0 = time.time()
    while time.time() - t0 < 600:
        b = await api.req("GET", f"/chats/{cid}/boost")
        if b["status"] in ("ready", "failed"):
            break
        await asyncio.sleep(2)
    check(b["status"] == "ready" and all(i["status"] == "success" for i in b["items"]),
          f"all candidates finished; recommended #{(b['recommended'] or 0) + 1}: {b['judge_reason']}")
    check(all(i["build_ok"] for i in b["items"]), "every candidate's pnpm build passed")
    child = b["items"][1]["chat_id"]
    r = await gw_preview(api, args, child)
    check(r.status_code == 200 and "__oa/bridge.js" in r.text, "candidate #2 live preview via its own ticket")
    sel = await api.req("POST", f"/chats/{cid}/boost/select", json={"index": 1})
    check(sel["status"] == "merged" and sel["version"], f"merged candidate #2 → {sel['version']}")
    comp = await read_file(api, cid, "src/components/Counter.tsx")
    check("emerald" in comp, "parent now has candidate #2's design (emerald accent)")
    projects = await api.req("GET", "/chats")
    check(all(p.get("parent_chat_id") is None for p in projects["items"]), "candidate projects hidden from lists")
    await stop_sandbox(api, cid)

    print("P9.2 深度研究 (Iris)")
    chat = await api.req("POST", "/chats", json={"title": "Research", "config": {"agent_mode": "full"}})
    rid = chat["chat_id"]
    await api.req("POST", f"/chats/{rid}/messages", json={
        "content": [{"insert": "Research the state of WebAssembly adoption"}], "metadata": {"enable_dr": True}})
    await wait_idle(api, rid)
    report = await read_file(api, rid, "docs/research/report.md")
    check(report.startswith("# Research report") and "## Sources" in report, f"cited report written ({len(report)} chars)")
    tls = await api.req("GET", f"/chats/{rid}/block-timelines")
    check(sum(t["block_type"] == "Research" for t in tls) >= 3, "Research blocks recorded")
    await stop_sandbox(api, rid)

    print("P9.3 SEO (Sarah) + 广告 (Adrian)")
    chat = await api.req("POST", "/chats", json={"title": "Growth", "config": {"agent_mode": "full"}})
    gid = chat["chat_id"]
    await api.req("POST", f"/chats/{gid}/messages", json={"content": [{"insert":
                  "Landing page for Bean There coffee with good SEO and a launch ads campaign"}]})
    await wait_idle(api, gid)
    hist = await api.req("GET", f"/chats/{gid}/messages")
    names = {m["name"] for m in hist["messages"]}
    check({"Sarah", "Adrian"} <= names, "Sarah and Adrian joined the team")
    seo = [json.loads(t["content"]) for t in await api.req("GET", f"/chats/{gid}/block-timelines")
           if t["command_name"] == "seo_audit"]
    check(len(seo) >= 2 and seo[-1]["score"] > seo[0]["score"], f"SEO score {seo[0]['score']} → {seo[-1]['score']}")
    html = await read_file(api, gid, "index.html")
    check('name="description"' in html and "og:title" in html, "index.html has meta description + Open Graph")
    camp = await read_file(api, gid, "docs/ads/campaign.md")
    check("## Ad groups" in camp, "ad campaign plan written")
    await stop_sandbox(api, gid)

    print("P9.4 多模态")
    chat = await api.req("POST", "/chats", json={"title": "Media", "config": {"agent_mode": "lite"}})
    mid = chat["chat_id"]
    import zlib
    import struct

    def _png(w: int, h: int) -> bytes:  # tiny valid RGB PNG without third-party deps
        raw = b"".join(b"\x00" + bytes([200, 120, 60]) * w for _ in range(h))
        def chunk(t: bytes, d: bytes) -> bytes:
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    up_img = await api.req("POST", f"/chats/{mid}/files/upload", files={"file": ("mood.png", _png(64, 40), "image/png")},
                           data={"dir": "uploads"})
    up_doc = await api.req("POST", f"/chats/{mid}/files/upload",
                           files={"file": ("brief.md", b"# Brief\n\nA calm landing page for Bean There.", "text/markdown")},
                           data={"dir": "uploads"})
    await api.req("POST", f"/chats/{mid}/messages", json={
        "content": [{"insert": "Landing page with a hero image for Bean There, and export a PDF summary"}],
        "metadata": {"attachments": [
            {"name": "mood.png", "path": up_img["path"], "url": up_img["url"], "mime": "image/png"},
            {"name": "brief.md", "path": up_doc["path"], "url": up_doc["url"], "mime": "text/markdown"}]}})
    await wait_idle(api, mid)
    ads = (await api.req("GET", f"/chats/{mid}/messages"))["action_datas"]
    cmds = {a["command_name"] for a in ads}
    for c in ("imageanalyzer_analyze_images", "documentextractor_extract_documents", "imagecreator_generate_image",
              "markdownpdfcreator_convert_file"):
        check(c in cmds, f"{c} ran")
    raw = httpx.Client(trust_env=False).get(args.api + f"/api/v1/chats/{mid}/files/raw",
                                            params={"path": "public/assets/hero.png"},
                                            headers={"Authorization": api.token}, timeout=30)
    check(raw.content[:4] == b"\x89PNG", "generated hero image in public/assets/hero.png")
    pdf = httpx.Client(trust_env=False).get(args.api + f"/api/v1/chats/{mid}/files/raw",
                                            params={"path": "docs/summary.pdf"},
                                            headers={"Authorization": api.token}, timeout=30)
    check(pdf.content[:5] == b"%PDF-", "docs/summary.pdf exported")

    print("P9.5 定时任务")
    auto = await api.req("POST", "/automations", json={"chat_id": mid, "name": "Every minute", "cron": "* * * * *",
                                                       "prompt": "把页脚年份更新为今年"})
    t0 = time.time()
    fired = None
    while time.time() - t0 < 150:
        lst = await api.req("GET", "/automations")
        fired = next(a for a in lst if a["id"] == auto["id"])
        if fired["run_count"] >= 1:
            break
        await asyncio.sleep(3)
    check(fired and fired["run_count"] >= 1 and fired["last_status"] in ("sent", "queued"),
          f"scheduler fired the automation ({fired['last_status'] if fired else None})")
    hist = await api.req("GET", f"/automations/{auto['id']}/history")
    check(bool(hist), "automation prompt posted into the project chat")
    await api.req("PATCH", f"/automations/{auto['id']}", json={"enabled": False})
    await wait_idle(api, mid)
    await stop_sandbox(api, mid)

    print("P9.6 移动应用 (Expo)")
    chat = await api.req("POST", "/chats", json={"title": "Mobile", "config": {"agent_mode": "lite",
                                                                            "template_name": "expo-app"}})
    eid = chat["chat_id"]
    await api.req("POST", f"/chats/{eid}/messages", json={"content": [{"insert": "A counter app"}]})
    await wait_idle(api, eid)
    app = await read_file(api, eid, "App.tsx")
    check('from "react-native"' in app, "Alex wrote React Native code")
    vs = await wait_version(api, eid, 1)
    check(vs[0]["status"] == "ready", f"expo export build → {vs[0]['version']} ready")
    r = await gw_preview(api, args, eid)
    check(r.status_code == 200 and ".bundle" in r.text, "Expo web dev server preview via gateway")
    dep = await api.req("PUT", f"/chats/{eid}/deployment", json={"version": vs[0]["version"]})
    r = gw_get(args.gateway, dep["url"] + "/")
    check(r.status_code == 200 and "_expo/static/js" in r.text, "published Expo web build served")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except E2EError as e:
        print(f"\n{FAIL} E2E failed: {e}")
        sys.exit(1)
