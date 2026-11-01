from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from oa.config import settings
from oa.db import models as m
from oa.db.session import SessionLocal
from tests.conftest import INTERNAL, register_user


async def _project(client, h, html: str = "<html><body><h1>v1</h1></body></html>") -> str:
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "Site"})).json()["data"]["chat_id"]
    ws = settings.workspace_dir(cid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "index.html").write_text(html)
    (ws / "src").mkdir(exist_ok=True)
    (ws / "src" / "app.js").write_text("console.log('hi')\n")
    return cid


async def test_files_api(client, fake_runtime):
    u = await register_user(client)
    h = u["headers"]
    cid = await _project(client, h)
    d = (await client.get(f"/api/v1/chats/{cid}/files?path=", headers=h)).json()["data"]
    assert d["type"] == "dir" and {e["name"] for e in d["entries"]} >= {"index.html", "src"}
    f = (await client.get(f"/api/v1/chats/{cid}/files?path=src/app.js", headers=h)).json()["data"]
    assert f["content"].startswith("console.log") and f["binary"] is False
    assert (await client.get(f"/api/v1/chats/{cid}/files?path=../../etc/passwd", headers=h)).json()["code"] == 4400

    await client.put(f"/api/v1/chats/{cid}/files", headers=h, json={"path": "docs/prd.md", "content": "# PRD"})
    tree = (await client.get(f"/api/v1/chats/{cid}/files/tree", headers=h)).json()["data"]
    assert any(n["name"] == "docs" and n["children"][0]["name"] == "prd.md" for n in tree)
    raw = await client.get(f"/api/v1/chats/{cid}/files/raw?path=docs/prd.md", headers=h)
    assert raw.content == b"# PRD"
    up = (await client.post(f"/api/v1/chats/{cid}/files/upload", headers=h,
                            files={"file": ("logo.png", b"\x89PNG\x00\x01", "image/png")})).json()["data"]
    assert up["path"] == "uploads/logo.png"
    assert (await client.get(f"/api/v1/chats/{cid}/files?path=uploads/logo.png", headers=h)).json()["data"]["binary"]
    z = await client.get(f"/api/v1/chats/{cid}/files/export", headers=h)
    assert z.headers["content-type"] == "application/zip" and z.content[:2] == b"PK"
    # token via query works for download links
    z2 = await client.get(f"/api/v1/chats/{cid}/files/export?token={u['token']}")
    assert z2.status_code == 200
    # non-ASCII titles must not break the latin-1 Content-Disposition header
    await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"title": "番茄钟 app"})
    z3 = await client.get(f"/api/v1/chats/{cid}/files/export", headers=h)
    assert z3.status_code == 200 and f'filename="{cid}.zip"' in z3.headers["content-disposition"]
    assert "filename*=UTF-8''%E7%95%AA%E8%8C%84%E9%92%9F-app.zip" in z3.headers["content-disposition"]
    await client.delete(f"/api/v1/chats/{cid}/files?path=docs", headers=h)
    assert (await client.get(f"/api/v1/chats/{cid}/files?path=docs/prd.md", headers=h)).status_code == 404

    # .storage snapshots come from object storage
    from oa.core.storage import get_storage

    await get_storage().put_bytes(f"chats/{cid}/.storage/1/abcd1234/terminal.log", b"$ ls\nindex.html\n")
    s = (await client.get(f"/api/v1/chats/{cid}/files?path=.storage/1/abcd1234/terminal.log", headers=h)).json()["data"]
    assert s["content"].startswith("$ ls")


async def test_versions_deploy_resolve_restore(client, fake_runtime):
    _, manager = fake_runtime
    u = await register_user(client)
    h, uid = u["headers"], u["user"]["id"]
    cid = await _project(client, h)

    from oa.domain.versions import create_version

    v1 = await create_version(cid, "First version")
    assert v1.version == "v1" and v1.status == "ready" and v1.commit
    assert v1.link == f"http://{uid}-{cid}-0-v1.{settings.app_domain}"
    (settings.workspace_dir(cid) / "index.html").write_text("<html><body><h1>v2</h1></body></html>")
    (settings.workspace_dir(cid) / "extra.txt").write_text("added in v2")
    v2 = await create_version(cid, "Second")
    assert v2.version == "v2"

    vs = (await client.get(f"/api/v1/chats/{cid}/versions", headers=h)).json()["data"]
    assert [v["version"] for v in vs] == ["v2", "v1"]
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    assert [x["content"]["version"] for x in hist["messages"] if x["type"] == "version"] == ["v1", "v2"]

    # version preview link: private chat needs cookie
    host = f"{uid}-{cid}-0-v1.app.localhost:8080"
    r = (await client.get("/internal/resolve", params={"host": host}, headers=INTERNAL)).json()
    assert r["kind"] == "auth_required"
    assert (await client.get("/internal/resolve", params={"host": host})).status_code == 403  # internal token required

    # deploy v1, public
    dep = (await client.put(f"/api/v1/chats/{cid}/deployment", headers=h,
                            json={"version": "v1", "is_always_latest": False, "access_mode": "public"})).json()["data"]
    assert dep["url"] == f"http://{uid}-{cid}-1-v1.{settings.app_domain}" and dep["status"] == "success"
    r = (await client.get("/internal/resolve", params={"host": urlparse(dep["url"]).netloc}, headers=INTERNAL)).json()
    assert r["kind"] == "static" and r["base"].endswith(f"/internal/static/{cid}/v1")
    assert r["data"]["kind"] == "static"
    page = await client.get(f"/internal/static/{cid}/v1/index.html", headers=INTERNAL)
    assert b"<h1>v1</h1>" in page.content and page.headers["content-type"].startswith("text/html")
    assert (await client.get(f"/internal/static/{cid}/v1/missing.js", headers=INTERNAL)).status_code == 404
    # always latest
    dep = (await client.put(f"/api/v1/chats/{cid}/deployment", headers=h, json={"is_always_latest": True})).json()["data"]
    assert dep["url"].endswith(f"-{cid}-1-latest.{settings.app_domain}") and dep["version"] == "v2"
    r = (await client.get("/internal/resolve", params={"host": urlparse(dep["url"]).netloc}, headers=INTERNAL)).json()
    assert r["base"].endswith("/v2")
    # wrong seq / wrong user → notfound
    bad = f"{uid}-{cid}-9-latest.app.localhost"
    assert (await client.get("/internal/resolve", params={"host": bad}, headers=INTERNAL)).json()["kind"] == "notfound"

    # restore v1 → v3 identical to v1, extra file removed, latest deployment follows
    v3 = (await client.post(f"/api/v1/chats/{cid}/versions/v1/restore", headers=h)).json()["data"]
    assert v3["version"] == "v3"
    assert "<h1>v1</h1>" in (settings.workspace_dir(cid) / "index.html").read_text()
    assert not (settings.workspace_dir(cid) / "extra.txt").exists()
    dep = (await client.get(f"/api/v1/chats/{cid}/deployment", headers=h)).json()["data"]
    assert dep["version"] == "v3"
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    assert any(x["type"] == "restore" for x in hist["messages"])

    # undeploy then redeploy → seq increments
    await client.delete(f"/api/v1/chats/{cid}/deployment", headers=h)
    assert (await client.get(f"/api/v1/chats/{cid}/deployment", headers=h)).json()["data"] is None
    dep = (await client.put(f"/api/v1/chats/{cid}/deployment", headers=h, json={"version": "v2"})).json()["data"]
    assert dep["seq"] == 2

    # publishing doesn't change project visibility; private project's published site with private access → auth
    dep = (await client.put(f"/api/v1/chats/{cid}/deployment", headers=h, json={"access_mode": "private"})).json()["data"]
    r = (await client.get("/internal/resolve", params={"host": urlparse(dep["url"]).netloc}, headers=INTERNAL)).json()
    assert dep["access_mode"] == "private" and r["kind"] == "auth_required"
    # discover lists the project once it is made public
    await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"access_mode": "public"})
    items = (await client.get("/api/v1/discover?sort=new")).json()["data"]["items"]
    assert any(i["chat_id"] == cid for i in items)


async def test_preview_ticket_flow(client, fake_runtime):
    _, manager = fake_runtime
    u = await register_user(client)
    h = u["headers"]
    cid = await _project(client, h)
    host = f"5173-{cid}-preview.app.localhost:8080"
    assert (await client.get("/internal/resolve", params={"host": host}, headers=INTERNAL)).json()["kind"] == "auth_required"

    t = (await client.post(f"/api/v1/chats/{cid}/preview-ticket", headers=h)).json()["data"]
    assert t["url"].startswith(f"http://5173-{cid}-preview.") and "__preview_auth" in t["url"]
    ticket = parse_qs(urlparse(t["url"]).query)["ticket"][0]
    # wrong host rejected
    other = f"5173-{'0' * 32}-preview.app.localhost"
    assert (await client.post("/internal/preview-auth", headers=INTERNAL, json={"ticket": ticket, "host": other})).status_code == 403
    pa = (await client.post("/internal/preview-auth", headers=INTERNAL, json={"ticket": ticket, "host": host})).json()
    cookie = pa["cookie"]
    # single use
    assert (await client.post("/internal/preview-auth", headers=INTERNAL, json={"ticket": ticket, "host": host})).status_code == 403

    r = (await client.get("/internal/resolve", params={"host": host, "cookie": cookie}, headers=INTERNAL)).json()
    assert r["kind"] == "waking" and manager.woken == [cid]
    manager.upstreams[cid] = "http://127.0.0.1:5999"
    r = (await client.get("/internal/resolve", params={"host": host, "cookie": cookie}, headers=INTERNAL)).json()
    assert r == {**r, "kind": "sandbox", "upstream": "http://127.0.0.1:5999"}
    # forged cookie
    forged = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    r = (await client.get("/internal/resolve", params={"host": host, "cookie": forged}, headers=INTERNAL)).json()
    assert r["kind"] == "auth_required"


async def test_share_redaction_and_fork(client, fake_runtime):
    u = await register_user(client)
    h = u["headers"]
    cid = await _project(client, h)
    await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"access_mode": "secret"})
    await client.post(f"/api/v1/chats/{cid}/secrets", headers=h, json={"key": "API_KEY", "value": "sk-supersecret-123", "env": "dev"})
    sec = (await client.get(f"/api/v1/chats/{cid}/secrets", headers=h)).json()["data"]
    assert sec[0]["value"] == "••••-123"
    assert "sk-supersecret-123" in (settings.workspace_dir(cid) / ".env.local").read_text()
    async with SessionLocal() as s:
        s.add(m.Message(chat_id=cid, role="Engineer", name="Alex", type="message",
                        content=[{"insert": "set key sk-supersecret-123"}], round_id=0))
        s.add(m.BlockTimeline(chat_id=cid, index=1, block_type="Terminal", command_name="terminal_run",
                              content="$ cat .env\nsk-supersecret-123", is_running=False, has_secret=True))
        await s.commit()
    share = (await client.get(f"/api/v1/share/{cid}")).json()["data"]  # anonymous
    assert "sk-supersecret-123" not in str(share)
    assert share["timelines"][0]["content"] == "[redacted]"
    assert share["chat"]["stats"]["views"] >= 1

    from oa.domain.versions import create_version

    await create_version(cid, "v1")
    other = await register_user(client)
    fork = (await client.post(f"/api/v1/chats/{cid}/fork", headers=other["headers"], json={"version": "v1"})).json()["data"]
    assert fork["status"] == "ready" and fork["fork_from"] == {"chat_id": cid, "version": "v1"}
    assert (settings.workspace_dir(fork["chat_id"]) / "index.html").exists()
    hist = (await client.get(f"/api/v1/chats/{fork['chat_id']}/messages", headers=other["headers"])).json()["data"]
    assert hist["messages"][0]["type"] == "remix"
    # like
    r = (await client.post(f"/api/v1/chats/{cid}/like", headers=other["headers"])).json()["data"]
    assert r == {"likes": 1, "is_liked": True}
