"""P9 features: automations (cron + scheduler + API), deep research (Iris), SEO (Sarah), ads (Adrian),
multimodal tools, Expo template selection and Best-of-N boost. Agents run for real with `oa-mock` against an
in-memory sandbox; boost uses the real runner with one fake sandbox per candidate chat."""
from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

os.environ.setdefault("OA_MOCK_DELAY", "0")

from oa.agents import runner
from oa.config import settings
from oa.db import models as m
from oa.db.session import session_scope
from oa.domain import automations as auto

INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>App</title>
  </head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
"""


class BytesSandbox:
    """In-memory SandboxClient stand-in that keeps binary files intact."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {
            "package.json": json.dumps({"name": "app", "scripts": {"dev": "vite", "build": "vite build"}}).encode(),
            "index.html": INDEX_HTML.encode(),
            "src/App.tsx": b"export default function App() { return <div>template</div>; }\n",
            "src/main.tsx": b"import App from './App';\n",
        }
        self.commands: list[str] = []

    async def read(self, path: str) -> bytes:
        path = path.removeprefix("/workspace/").lstrip("/")
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write(self, path: str, content: str | bytes) -> None:
        self.files[path.removeprefix("/workspace/").lstrip("/")] = content.encode() if isinstance(content, str) else content

    async def list(self, path: str = ".", depth: int = 1) -> list[dict[str, Any]]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        return [{"name": p.rsplit("/", 1)[-1], "path": p, "type": "file", "size": len(b)}
                for p, b in sorted(self.files.items()) if p.startswith(prefix)]

    async def exec(self, cmd: str, cwd: str | None = None, env: dict | None = None, timeout_ms: int | None = None):
        self.commands.append(cmd)
        if "build" in cmd:
            yield {"type": "stdout", "data": "✓ built in 0.3s\n"}
        else:
            yield {"type": "stdout", "data": "\n".join(sorted(self.files)) + "\n"}
        yield {"type": "exit", "code": 0, "timed_out": False}

    async def kernel_execute(self, code: str, timeout_ms: int = 120000) -> dict[str, Any]:
        return {"ok": True, "outputs": [{"type": "stream", "name": "stdout", "text": "ok\n"}]}

    async def process_logs(self, name: str, follow: bool = True, tail: int = 200):
        yield "ready"


@pytest.fixture
def sandboxes(monkeypatch):
    boxes: dict[str, BytesSandbox] = {}

    async def factory(chat_id: str) -> BytesSandbox:
        return boxes.setdefault(chat_id, BytesSandbox())

    async def preview(chat_id: str) -> str | None:
        return None

    async def offline_search(query: str, max_results: int = 5) -> dict[str, Any]:
        return {"query": query, "answer": "", "results": [
            {"title": f"{query} — guide", "url": f"https://example.org/{abs(hash(query)) % 1000}",
             "content": f"An in-depth explanation of {query} with data from 2025 studies and expert commentary."}]}

    from oa.tools import web

    monkeypatch.setattr(runner, "SANDBOX_FACTORY", factory)
    monkeypatch.setattr(runner, "PREVIEW_RESOLVER", preview)
    monkeypatch.setattr(web, "web_search", offline_search)
    return boxes


async def make_chat(client, register, **config: Any) -> tuple[str, dict[str, str], int]:
    u = await register()
    r = await client.post("/api/v1/chats", headers=u["headers"], json={"title": "p9", "config": config})
    assert r.status_code == 200, r.text
    return r.json()["data"]["chat_id"], u["headers"], u["user"]["id"]


async def send(client, h: dict[str, str], chat_id: str, text: str, **body: Any) -> dict[str, Any]:
    r = await client.post(f"/api/v1/chats/{chat_id}/messages", headers=h, json={"content": [{"insert": text}], **body})
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def wait_idle(chat_id: str, timeout: float = 60) -> m.Run:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        async with session_scope() as s:
            run = (await s.execute(select(m.Run).where(m.Run.chat_id == chat_id).order_by(m.Run.id.desc()).limit(1))
                   ).scalar_one_or_none()
            chat = await s.get(m.Chat, chat_id)
        if run and run.status in ("success", "failed", "cancelled", "suspended") and not chat.is_running \
                and not runner.is_running(chat_id):
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("run did not finish")


async def action_datas(chat_id: str) -> list[m.ActionData]:
    async with session_scope() as s:
        return list((await s.execute(select(m.ActionData).where(m.ActionData.chat_id == chat_id)
                                     .order_by(m.ActionData.id))).scalars())


async def messages(chat_id: str) -> list[m.Message]:
    async with session_scope() as s:
        return list((await s.execute(select(m.Message).where(m.Message.chat_id == chat_id).order_by(m.Message.id)))
                    .scalars())


# ---------------------------------------------------------------- cron
@pytest.mark.parametrize("expr,tz,expected", [
    ("*/15 * * * *", "UTC", "2026-10-30T10:15:00+00:00"),
    ("0 9 * * 1-5", "UTC", "2026-11-02T09:00:00+00:00"),        # Friday 10:07 → Monday
    ("0 9 * * MON", "Asia/Shanghai", "2026-11-02T01:00:00+00:00"),
    ("30 8 * * 7", "UTC", "2026-11-01T08:30:00+00:00"),         # 7 == Sunday
    ("0 9 13 * 5", "UTC", "2026-11-06T09:00:00+00:00"),         # dom OR dow (Vixie)
    ("@daily", "UTC", "2026-10-31T00:00:00+00:00"),
])
def test_next_fire(expr, tz, expected):
    base = datetime(2026, 10, 30, 10, 7, tzinfo=UTC)
    assert auto.next_fire(expr, tz, base).isoformat() == expected


@pytest.mark.parametrize("bad", ["* * *", "61 * * * *", "0 25 * * *", "0 9 * * 8", "*/0 * * * *", "0 9 * * FOO"])
def test_cron_rejects(bad):
    with pytest.raises(auto.CronError):
        auto.parse_cron(bad)


# ---------------------------------------------------------------- automations API + scheduler
async def test_automations_crud_run_and_tick(client, register, fake_runtime):
    fake_runner, _ = fake_runtime
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    bad = await client.post("/api/v1/automations", headers=h, json={"chat_id": chat_id, "name": "x", "prompt": "p",
                                                                    "cron": "every day"})
    assert bad.status_code == 400
    r = await client.post("/api/v1/automations", headers=h, json={
        "chat_id": chat_id, "name": "Daily report", "prompt": "Summarise yesterday's signups", "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai"})
    a = r.json()["data"]
    assert a["next_run_at"] and a["enabled"] and a["schedule"] == "Every day at 09:00"
    lst = (await client.get("/api/v1/automations", headers=h)).json()["data"]
    assert [x["id"] for x in lst] == [a["id"]] and lst[0]["chat_title"] == "p9"

    # run now → posts a user message and starts a round
    run = (await client.post(f"/api/v1/automations/{a['id']}/run", headers=h)).json()["data"]
    assert run["last_status"] == "sent" and run["run_count"] == 1 and fake_runner.started
    hist = (await client.get(f"/api/v1/automations/{a['id']}/history", headers=h)).json()["data"]
    assert hist and hist[0]["content"][0]["insert"] == "Summarise yesterday's signups"
    assert hist[0]["metadata"]["automation"]["manual"] is True

    # scheduler: due → fired exactly once even if two ticks race
    async with session_scope() as s:
        row = await s.get(m.Automation, a["id"])
        row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        async with session_scope() as s2:
            c = await s2.get(m.Chat, chat_id)
            c.is_running = False
    fired = await asyncio.gather(auto.tick(), auto.tick())
    assert sorted(sum(fired, [])) == [a["id"]]
    async with session_scope() as s:
        row = await s.get(m.Automation, a["id"])
        assert row.run_count == 2 and row.next_run_at.replace(tzinfo=UTC) > datetime.now(UTC)

    # disable → no schedule; delete
    p = (await client.patch(f"/api/v1/automations/{a['id']}", headers=h, json={"enabled": False})).json()["data"]
    assert p["enabled"] is False and p["next_run_at"] is None
    assert (await client.delete(f"/api/v1/automations/{a['id']}", headers=h)).json()["code"] == 0
    assert (await client.get("/api/v1/automations", headers=h)).json()["data"] == []


async def test_automation_queues_when_busy(client, register, fake_runtime):
    chat_id, h, _ = await make_chat(client, register)
    a = (await client.post("/api/v1/automations", headers=h, json={
        "chat_id": chat_id, "name": "n", "prompt": "hello", "cron": "@hourly"})).json()["data"]
    async with session_scope() as s:
        (await s.get(m.Chat, chat_id)).is_running = True
    out = await auto.fire(a["id"])
    assert out["last_status"] == "queued"
    q = (await client.get(f"/api/v1/chats/{chat_id}/prompt-queue", headers=h)).json()["data"]
    assert q["items"] and q["items"][0]["metadata"]["automation"]["id"] == a["id"]


# ---------------------------------------------------------------- deep research (Iris)
async def test_deep_research_report(client, register, sandboxes):
    chat_id, h, _ = await make_chat(client, register, agent_mode="full")
    await send(client, h, chat_id, "Research the market for AI coding assistants", metadata={"enable_dr": True})
    run = await wait_idle(chat_id)
    assert run.status == "success", run.error
    report = sandboxes[chat_id].files["docs/research/report.md"].decode()
    assert report.startswith("# Research report") and "## Sources" in report and "[1] https://example.org/" in report
    cmds = [a.command_name for a in await action_datas(chat_id)]
    assert cmds.count("searchenhancedqa_run") == 3 and cmds.count("searchnavigator_next_query") == 3
    assert cmds[-1] == "researchexecutor_finalize_article"
    roles = {x.role for x in await messages(chat_id)}
    assert "Deep Researcher" in roles and "Team Leader" not in roles


# ---------------------------------------------------------------- team with SEO + ads specialists
async def test_team_with_seo_and_ads(client, register, sandboxes):
    chat_id, h, _ = await make_chat(client, register, agent_mode="full")
    await send(client, h, chat_id, "Landing page for Bean There coffee with good SEO and a launch ads campaign")
    run = await wait_idle(chat_id)
    assert run.status == "success", run.error
    tasks = run.state["tasks"]
    assert [t["assignee"] for t in tasks][-2:] == ["SEO Specialist", "Ads Specialist"]
    assert all(t["status"] == "done" for t in tasks)
    box = sandboxes[chat_id]
    html = box.files["index.html"].decode()
    assert 'name="description"' in html and 'property="og:title"' in html and "<title>App</title>" not in html
    assert run.state["seo_scores"][-1] > run.state["seo_scores"][0]
    camp = box.files["docs/ads/campaign.md"].decode()
    assert "## Ad groups" in camp and "### Brand" in camp
    names = {(x.role, x.name) for x in await messages(chat_id)}
    assert ("SEO Specialist", "Sarah") in names and ("Ads Specialist", "Adrian") in names
    growth = [a for a in await action_datas(chat_id) if a.block_type == "Growth"]
    assert {a.command_name for a in growth} == {"seo_audit", "ads_generate_campaign"}


# ---------------------------------------------------------------- multimodal
def test_media_helpers_roundtrip():
    from oa.tools import media

    png = media.procedural_png("hello", 320, 200)
    info = media.describe_image(png)
    assert (info["format"], info["width"], info["height"]) == ("PNG", 320, 200) and info["dominant_colors"]
    pdf = media.markdown_to_pdf("# Title 标题\n\nBody **bold** text.\n\n- one\n- two\n")
    text, meta = media.extract_text("x.pdf", pdf)
    assert meta == {"kind": "pdf", "pages": 1} and "Body bold text." in text

    import docx

    d = docx.Document()
    d.add_paragraph("Requirement: a guestbook")
    buf = io.BytesIO()
    d.save(buf)
    text, meta = media.extract_text("spec.docx", buf.getvalue())
    assert "guestbook" in text and meta["kind"] == "document"


async def test_attachments_images_and_pdf_export(client, register, sandboxes):
    from oa.tools import media

    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    box = sandboxes.setdefault(chat_id, BytesSandbox())
    box.files["uploads/brief.pdf"] = media.markdown_to_pdf("# Brief\n\nBuild a calm landing page for Bean There.")
    box.files["uploads/mood.png"] = media.procedural_png("mood", 200, 120)
    await send(client, h, chat_id, "Landing page with a hero image for Bean There, and export a PDF summary",
               metadata={"attachments": [
                   {"name": "brief.pdf", "path": "uploads/brief.pdf", "mime": "application/pdf"},
                   {"name": "mood.png", "path": "uploads/mood.png", "mime": "image/png"}]})
    run = await wait_idle(chat_id)
    assert run.status == "success", run.error
    cmds = [a.command_name for a in await action_datas(chat_id)]
    for c in ("imageanalyzer_analyze_images", "pdfanalyzer_analyze_pdfs", "imagecreator_generate_image",
              "markdownpdfcreator_convert_file"):
        assert c in cmds, c
    assert box.files["public/assets/hero.png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert box.files["docs/summary.pdf"][:5] == b"%PDF-"
    hero = next(a for a in await action_datas(chat_id) if a.command_name == "imagecreator_generate_image")
    assert hero.block_type == "Assets" and hero.file.endswith("assets.json")
    async with session_scope() as s:
        tl = (await s.execute(select(m.BlockTimeline).where(m.BlockTimeline.chat_id == chat_id,
                                                            m.BlockTimeline.index == hero.timeline_index))).scalar_one()
    item = json.loads(tl.content)["items"][0]
    assert item["preview"].endswith("/hero.png") and item["url"] == "/assets/hero.png"
    from oa.core.storage import get_storage

    assert (await get_storage().get_bytes(f"chats/{chat_id}/{item['preview']}"))[:4] == b"\x89PNG"


def test_attachment_ops_do_not_leak_into_the_requirement():
    """The composer sends attachments as Delta ops before the text; the marker must not glue onto the prompt."""
    from oa.agents.emit import delta_text
    from oa.llm.mock import _Conv

    text = delta_text([{"insert": {"attachment": {"name": "mood.png", "url": "/x"}}},
                       {"insert": "Use this moodboard: add a hero image"}])
    assert text == "[attachment: mood.png]\nUse this moodboard: add a hero image"
    assert _Conv([{"role": "user", "content": text}]).requirement == "Use this moodboard: add a hero image"


# ---------------------------------------------------------------- Expo template
async def test_expo_template_selection(client, register):
    from oa.sandbox import manager

    chat_id, _, _ = await make_chat(client, register, template_name="expo-app")
    await manager.init_workspace(chat_id)
    ws = settings.workspace_dir(chat_id)
    pkg = json.loads((ws / "package.json").read_text())
    assert "expo" in pkg["dependencies"] and "expo export" in pkg["scripts"]["build"]
    assert "Expo (React Native)" in (ws / ".atoms" / "memory.md").read_text()
    assert not (ws / "node_modules").exists()
    # unknown template names fall back to the default web template
    assert manager.template_path("../etc") == settings.template_dir


async def test_mock_writes_react_native_for_expo(client, register, sandboxes):
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite", template_name="expo-app")
    from oa.sandbox import manager

    await manager.init_workspace(chat_id)
    await send(client, h, chat_id, "A counter app")
    run = await wait_idle(chat_id)
    assert run.status == "success", run.error
    app = sandboxes[chat_id].files["App.tsx"].decode()
    assert 'from "react-native"' in app and "StyleSheet.create" in app and "className" not in app


async def test_first_run_of_new_expo_project_knows_it_is_react_native(client, register, sandboxes):
    """Dashboard flow: the first prompt runs before the workspace exists, so the agent must get the template's
    memory (otherwise it writes web code to src/App.tsx, which the Expo entry never mounts)."""
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite", template_name="expo-app")
    assert not (settings.workspace_dir(chat_id) / ".atoms" / "memory.md").exists()
    await send(client, h, chat_id, "A counter app")
    run = await wait_idle(chat_id)
    assert run.status == "success", run.error
    files = sandboxes[chat_id].files
    assert 'from "react-native"' in files["App.tsx"].decode()
    assert files["src/App.tsx"].decode().strip().endswith("<div>template</div>; }"), "web entry must be untouched"


# ---------------------------------------------------------------- boost / BoN
async def test_boost_best_of_n_select_merges(client, register, sandboxes):
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    # the free plan runs 2 sandboxes at once: 3 candidates are rejected up front with a clear reason
    r = await client.post(f"/api/v1/chats/{chat_id}/messages", headers=h, json={
        "content": [{"insert": "x"}], "use_boost": True, "boost_models": ["oa-mock", "oa-mock-2", "oa-mock-3"]})
    assert r.status_code == 400 and "at most 2 candidates" in r.json()["message"]
    chat = (await client.get(f"/api/v1/chats/{chat_id}", headers=h)).json()["data"]
    assert chat["metadata"]["is_running"] is False  # rejection releases the project
    async with session_scope() as s:
        (await s.get(m.Workspace, chat["workspace_id"])).plan = "pro"
    out = await send(client, h, chat_id, "Make a counter", use_boost=True,
                     boost_models=["oa-mock", "oa-mock-2", "oa-mock-3"])
    b = out["boost"]
    assert b["status"] == "running" and len(b["items"]) == 3
    child_ids = [it["chat_id"] for it in b["items"]]
    # children are hidden from the project list; the parent is busy (prompts queue)
    lst = (await client.get("/api/v1/chats", headers=h)).json()["data"]["items"]
    assert {c["chat_id"] for c in lst} == {chat_id}
    q = await send(client, h, chat_id, "later")
    assert q["queued"] is True

    loop = asyncio.get_running_loop()
    end = loop.time() + 60
    while loop.time() < end:
        cur = (await client.get(f"/api/v1/chats/{chat_id}/boost", headers=h)).json()["data"]
        if cur["status"] == "ready":
            break
        await asyncio.sleep(0.1)
    assert cur["status"] == "ready" and cur["recommended"] == 0, cur
    assert all(it["status"] == "success" for it in cur["items"])
    # candidates explored different visual directions
    accents = [sandboxes[c].files["src/components/Counter.tsx"].decode() for c in child_ids]
    assert "bg-blue-600" in accents[0] and "bg-emerald-600" in accents[1] and "bg-violet-600" in accents[2]

    # merge candidate #2: its workspace becomes the parent's (sync of the host workspace)
    child_ws = settings.workspace_dir(child_ids[1])
    (child_ws / "src" / "Winner.txt").write_text("candidate 2")
    sel = await client.post(f"/api/v1/chats/{chat_id}/boost/select", headers=h, json={"index": 1})
    assert sel.status_code == 200, sel.text
    data = sel.json()["data"]
    assert data["status"] == "merged" and data["selected"] == 1 and data["stats"]["added"] >= 1
    assert (settings.workspace_dir(chat_id) / "src" / "Winner.txt").read_text() == "candidate 2"
    card = next(x for x in await messages(chat_id) if x.type == "boost")
    assert card.content["status"] == "merged" and card.is_finished
    again = await client.post(f"/api/v1/chats/{chat_id}/boost/select", headers=h, json={"index": 0})
    assert again.status_code == 400


async def test_boost_validation_and_abort(client, register, sandboxes):
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    r = await client.post(f"/api/v1/chats/{chat_id}/messages", headers=h,
                          json={"content": [{"insert": "x"}], "use_boost": True, "boost_models": ["oa-mock"]})
    assert r.status_code == 400
    await send(client, h, chat_id, "Make a todo list", use_boost=True, boost_models=["oa-mock", "oa-mock-2"])
    stop = (await client.post(f"/api/v1/chats/{chat_id}/stop", headers=h)).json()["data"]
    assert stop["cancelled"] is True
    cur = (await client.get(f"/api/v1/chats/{chat_id}/boost", headers=h)).json()["data"]
    assert cur["status"] == "aborted"
    chat = (await client.get(f"/api/v1/chats/{chat_id}", headers=h)).json()["data"]
    assert chat["metadata"]["is_running"] is False
    gone = (await client.delete(f"/api/v1/chats/{chat_id}/boost", headers=h)).json()
    assert gone["code"] == 0


async def test_parent_sandbox_stays_down_while_boost_builds(client, register, sandboxes):
    """The workbench wakes the project's sandbox on page load; during a boost that would take a plan slot from a
    candidate (free plan: 2), so wake must be refused until the candidates are done."""
    from oa.domain import boost
    from oa.sandbox import manager

    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    await send(client, h, chat_id, "Make a counter", use_boost=True, boost_models=["oa-mock", "oa-mock-2"])
    assert await boost.is_building(chat_id)
    assert await manager.wake(chat_id) is False and chat_id not in manager._wake_tasks
    r = (await client.post(f"/api/v1/chats/{chat_id}/runtime/start", headers=h)).json()["data"]
    assert r["status"] == "boosting"
    await client.post(f"/api/v1/chats/{chat_id}/stop", headers=h)  # aborts the boost
    assert not await boost.is_building(chat_id)


async def test_sandbox_full_fails_the_run_instead_of_faking_success(client, register, monkeypatch):
    from oa.sandbox.manager import SandboxFull

    async def full(chat_id: str):
        raise SandboxFull("workspace already runs 2/2 sandboxes allowed by its plan")

    monkeypatch.setattr(runner, "SANDBOX_FACTORY", full)
    chat_id, h, _ = await make_chat(client, register, agent_mode="lite")
    await send(client, h, chat_id, "Make a counter")
    run = await wait_idle(chat_id)
    assert run.status == "failed" and "SandboxFull" in (run.error or "")
    errs = [x for x in await messages(chat_id) if x.type == "error"]
    assert errs and "sandboxes" in errs[-1].content["message"]
    assert all(x.is_finished for x in await messages(chat_id))
