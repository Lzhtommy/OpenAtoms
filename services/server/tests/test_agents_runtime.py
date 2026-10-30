"""Agent runtime tests with a fake in-memory sandbox (fast, no sandboxd/pnpm needed)."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import posixpath
from typing import Any

import pytest
from sqlalchemy import select

os.environ.setdefault("OA_MOCK_DELAY", "0")

from oa.agents import runner
from oa.core.events import bus
from oa.db import models as m
from oa.db.session import session_scope


class FakeSandbox:
    """In-memory stand-in for SandboxClient. `pnpm build` fails while any file contains BREAK_BUILD."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {
            "package.json": json.dumps({"name": "app", "scripts": {"dev": "vite", "build": "vite build"}}),
            "src/App.tsx": "export default function App() { return <div>template</div>; }\n",
            "src/main.tsx": "import App from './App';\n",
        }
        self.commands: list[str] = []
        self.always_fail_build = False
        self._ns: dict[str, Any] = {}

    async def read(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path].encode()

    async def write(self, path: str, content: str | bytes) -> None:
        self.files[path] = content.decode() if isinstance(content, bytes) else content

    async def list(self, path: str = ".", depth: int = 1) -> list[dict[str, Any]]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        names = sorted({p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)})
        return [{"name": n, "path": posixpath.join(prefix, n), "type": "file"} for n in names]

    async def exec(self, cmd: str, cwd: str | None = None, env: dict | None = None, timeout_ms: int | None = None):
        self.commands.append(cmd)
        if "build" in cmd:
            broken = self.always_fail_build or any("BREAK_BUILD" in c for c in self.files.values())
            if broken:
                yield {"type": "stderr", "data": "src/App.tsx(3,1): error TS2304: Cannot find name 'BREAK_BUILD'.\n"}
                yield {"type": "exit", "code": 1, "timed_out": False}
                return
            yield {"type": "stdout", "data": "vite v5.4 building for production...\n✓ built in 0.42s\n"}
            yield {"type": "exit", "code": 0, "timed_out": False}
            return
        yield {"type": "stdout", "data": "\n".join(sorted(self.files)) + "\n"}
        yield {"type": "exit", "code": 0, "timed_out": False}

    async def kernel_execute(self, code: str, timeout_ms: int = 120000) -> dict[str, Any]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(code, self._ns)
        return {"ok": True, "outputs": [{"type": "stream", "name": "stdout", "text": buf.getvalue()}]}

    async def process_logs(self, name: str, follow: bool = True, tail: int = 200):
        for line in ["VITE ready"]:
            yield line


@pytest.fixture
def fake_sandbox(monkeypatch):
    sb = FakeSandbox()

    async def factory(chat_id: str) -> FakeSandbox:
        return sb

    async def preview(chat_id: str) -> str | None:
        return None

    monkeypatch.setattr(runner, "SANDBOX_FACTORY", factory)
    monkeypatch.setattr(runner, "PREVIEW_RESOLVER", preview)
    return sb


@pytest.fixture
def events():
    got: list[tuple[str, str, dict]] = []

    def listener(chat_id: str, event: str, payload: dict) -> None:
        got.append((chat_id, event, payload))

    bus.add_listener(listener)
    yield got
    bus.remove_listener(listener)


async def make_chat(client, register, **config: Any) -> tuple[str, int]:
    u = await register()
    r = await client.post("/api/v1/chats", headers=u["headers"], json={"title": "t", "config": config})
    assert r.status_code == 200, r.text
    return r.json()["data"]["chat_id"], u["user"]["id"]


async def send(chat_id: str, user_id: int, text: str, **meta: Any) -> int:
    async with session_scope() as s:
        rid = int((await s.execute(select(m.Message.round_id).where(m.Message.chat_id == chat_id)
                                   .order_by(m.Message.round_id.desc()).limit(1))).scalar() or 0) + 1
        msg = m.Message(chat_id=chat_id, role="User", name="Tester", type="message", content=[{"insert": text}],
                        round_id=rid, attempt=0, user_id=user_id, metadata_=meta)
        s.add(msg)
        await s.flush()
        mid = msg.id
    await runner.start_round(chat_id, mid)
    return mid


async def wait_run(chat_id: str, statuses=("success", "failed", "cancelled", "suspended"), timeout: float = 60) -> m.Run:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        async with session_scope() as s:
            run = (await s.execute(select(m.Run).where(m.Run.chat_id == chat_id).order_by(m.Run.id.desc()).limit(1))
                   ).scalar_one_or_none()
            chat = await s.get(m.Chat, chat_id)
        if run and run.status in statuses and not runner.is_running(chat_id) and not chat.is_running:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach {statuses}: {run.status if run else None}")


async def messages(chat_id: str) -> list[m.Message]:
    async with session_scope() as s:
        return list((await s.execute(select(m.Message).where(m.Message.chat_id == chat_id).order_by(m.Message.id)))
                    .scalars())


async def timelines(chat_id: str) -> list[m.BlockTimeline]:
    async with session_scope() as s:
        return list((await s.execute(select(m.BlockTimeline).where(m.BlockTimeline.chat_id == chat_id)
                                     .order_by(m.BlockTimeline.index))).scalars())


async def action_datas(chat_id: str) -> list[m.ActionData]:
    async with session_scope() as s:
        return list((await s.execute(select(m.ActionData).where(m.ActionData.chat_id == chat_id))).scalars())


# ---------------------------------------------------------------- tests
async def test_lite_round_builds_app(client, register, fake_sandbox, events):
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    await send(chat_id, uid, "Make a pomodoro timer")
    run = await wait_run(chat_id)
    assert run.status == "success", run.error

    comp = fake_sandbox.files["src/components/Pomodoro.tsx"]
    assert "DURATIONS" in comp and "bg-blue-600" in comp
    app = fake_sandbox.files["src/App.tsx"]
    assert "Draft" not in app and "Focus for 25 minutes" in app  # edit_by_replace applied
    assert any("pnpm build" in c for c in fake_sandbox.commands)

    msgs = await messages(chat_id)
    assert all(x.is_finished for x in msgs)
    thinks = [x for x in msgs if x.type == "think"]
    assert thinks and all(x.role == "Engineer" and x.name == "Alex" for x in thinks)
    assert any(op.get("action_data") for x in thinks for op in x.content)
    final = [x for x in msgs if x.type == "message" and x.role == "Engineer"]
    assert final and "Pomodoro Timer" in final[-1].content[0]["insert"]

    tls = await timelines(chat_id)
    assert [t.index for t in tls] == list(range(tls[0].index, tls[0].index + len(tls)))
    assert {t.block_type for t in tls} >= {"Editor", "Terminal", "Browser-RT"}
    assert not any(t.is_running for t in tls)
    ads = await action_datas(chat_id)
    assert all(a.file and a.file.startswith(".storage/") for a in ads)
    editor = next(a for a in ads if a.command_name == "editor_write")
    from oa.core.storage import get_storage

    snap = json.loads(await get_storage().get_bytes(f"chats/{chat_id}/{editor.file}"))
    assert snap["after"] and snap["path"].startswith("src/")

    seqs = [p["seq"] for c, _, p in events if c == chat_id]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    names = [e for c, e, _ in events if c == chat_id]
    for ev in ("run:start", "run:end", "msg:create", "msg:update", "timeline:start", "timeline:content",
               "timeline:complete", "action_data:update", "chat:update"):
        assert ev in names, ev


async def test_full_team_round(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="full")
    await send(chat_id, uid, "做一个带登录的待办应用")
    run = await wait_run(chat_id)
    assert run.status == "success", run.error
    for f in ("docs/prd.md", "docs/design.md", "docs/research/notes.md", "src/components/TodoApp.tsx"):
        assert f in fake_sandbox.files, f
    assert "AuthForm" in fake_sandbox.files["src/components/TodoApp.tsx"]
    msgs = await messages(chat_id)
    roles = {x.role for x in msgs}
    assert roles >= {"Team Leader", "Product Manager", "Architect", "Engineer", "Data Analyst"}
    mentions = [x for x in msgs if x.type == "message" and x.role == "Team Leader"
                and any(isinstance(op.get("insert"), dict) and "mentiontrigger" in op["insert"] for op in x.content)]
    assert len(mentions) == 4
    emojis = [x for x in msgs if x.type == "emoji"]
    assert len(emojis) == 4 and {e.refer_id for e in emojis} == {x.id for x in mentions}
    assert msgs[-1].role == "Team Leader" and msgs[-1].type == "message" and "待办" in msgs[-1].content[0]["insert"]
    tasks = run.state["tasks"]
    assert [t["status"] for t in tasks] == ["done"] * 4
    tls = await timelines(chat_id)
    assert {"Task", "Search", "Browser", "Notebook", "Editor", "Terminal"} <= {t.block_type for t in tls}
    nb = next(t for t in tls if t.block_type == "Notebook")
    assert "Priority" in nb.content


async def test_plan_mode_approve(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="full", plan_mode=True)
    await send(chat_id, uid, "Make a counter")
    run = await wait_run(chat_id)
    assert run.status == "suspended" and run.suspend_reason == "plan_ready"
    card = next(x for x in await messages(chat_id) if x.type == "plan_ready")
    assert card.content["status"] == "pending" and len(card.content["tasks"]) == 4
    assert await runner.resume(chat_id, "approve", card.id)
    run = await wait_run(chat_id)
    assert run.status == "success", run.error
    assert "src/components/Counter.tsx" in fake_sandbox.files
    card = next(x for x in await messages(chat_id) if x.type == "plan_ready")
    assert card.content["status"] == "approved"


async def test_plan_mode_decline(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="full", plan_mode=True)
    await send(chat_id, uid, "Make a counter")
    await wait_run(chat_id)
    assert await runner.resume(chat_id, "decline", None, {"reason": "too big"})
    run = await wait_run(chat_id)
    assert run.status == "success"
    assert "src/components/Counter.tsx" not in fake_sandbox.files
    assert "too big" in (await messages(chat_id))[-1].content[0]["insert"]


async def test_ask_human_resume(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    await send(chat_id, uid, "Build a counter, ask me about the color")
    run = await wait_run(chat_id)
    assert run.status == "suspended" and run.suspend_reason == "ask_human"
    card = next(x for x in await messages(chat_id) if x.type == "task")
    assert [o["label"] for o in card.content["option"]] == ["Blue", "Green", "Purple"]
    assert await runner.resume(chat_id, "reply", None, {"text": "Green", "task_reply": {"message_id": card.id,
                                                                                         "option_index": 1}})
    run = await wait_run(chat_id)
    assert run.status == "success", run.error
    assert "bg-emerald-600" in fake_sandbox.files["src/components/Counter.tsx"]
    card = next(x for x in await messages(chat_id) if x.type == "task")
    assert card.content["status"] == "finished" and card.content["selected"] == 1


async def test_questions_card(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="full")
    await send(chat_id, uid, "Please clarify and then build a landing page for my bakery")
    run = await wait_run(chat_id)
    assert run.status == "suspended" and run.suspend_reason == "questions"
    card = next(x for x in await messages(chat_id) if x.type == "questions_card")
    assert len(card.content["questions"]) == 3
    await runner.resume(chat_id, "questions", card.id, {"answers": {"audience": "Public users", "style": "Playful"}})
    run = await wait_run(chat_id)
    assert run.status == "success", run.error
    assert "src/components/Landing.tsx" in fake_sandbox.files
    mike_hist = run.state["histories"]["Mike"]
    assert any("Public users" in str(h.get("content")) for h in mike_hist if h.get("role") == "tool")


async def test_build_repair_then_human_stop(client, register, fake_sandbox):
    fake_sandbox.always_fail_build = True
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    await send(chat_id, uid, "Make a counter")
    run = await wait_run(chat_id)
    assert run.status == "suspended" and run.suspend_reason == "ask_human"
    card = [x for x in await messages(chat_id) if x.type == "task"][-1]
    assert card.content["name"] == "Decision to continue fixing the build"
    assert run.state["build_fix_rounds"] == 3
    await runner.resume(chat_id, "reply", None, {"text": "Do not continue",
                                                 "task_reply": {"message_id": card.id, "option_index": 1}})
    run = await wait_run(chat_id)
    assert run.status == "success"
    assert run.state["build_failed"] is True


async def test_build_repair_continue_then_fixed(client, register, fake_sandbox):
    fake_sandbox.always_fail_build = True
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    await send(chat_id, uid, "Make a counter")
    await wait_run(chat_id)
    card = [x for x in await messages(chat_id) if x.type == "task"][-1]
    fake_sandbox.always_fail_build = False  # "the user fixed an env issue"
    await runner.resume(chat_id, "reply", None, {"text": "Yes, continue"})
    run = await wait_run(chat_id)
    assert run.status == "success" and not run.state.get("build_failed")
    card = next(x for x in await messages(chat_id) if x.id == card.id)
    assert card.content["status"] == "finished"


async def test_cancel_running(client, register, fake_sandbox, monkeypatch):
    monkeypatch.setenv("OA_MOCK_DELAY", "0.02")
    chat_id, uid = await make_chat(client, register, agent_mode="full")
    await send(chat_id, uid, "Make a todo app")
    await asyncio.sleep(0.6)
    assert runner.is_running(chat_id)
    assert await runner.cancel(chat_id)
    run = await wait_run(chat_id)
    assert run.status == "cancelled"
    assert all(x.is_finished for x in await messages(chat_id))
    assert not any(t.is_running for t in await timelines(chat_id))


async def test_new_round_supersedes_suspended(client, register, fake_sandbox):
    chat_id, uid = await make_chat(client, register, agent_mode="full", plan_mode=True)
    await send(chat_id, uid, "Make a counter")
    first = await wait_run(chat_id)
    assert first.status == "suspended"
    await send(chat_id, uid, "Actually, make a pomodoro timer", plan_mode=False)
    run = await wait_run(chat_id, statuses=("success",))
    assert run.id != first.id
    async with session_scope() as s:
        assert (await s.get(m.Run, first.id)).status == "cancelled"
    assert "src/components/Pomodoro.tsx" in fake_sandbox.files


async def test_queued_prompt_runs_after_real_run(client, register, fake_sandbox):
    """Regression: the finishing run calls queue.pop_next from inside its own task; the next round must start."""
    u = await register()
    h = u["headers"]
    r = await client.post("/api/v1/chats", headers=h, json={"title": "q", "config": {"agent_mode": "lite"}})
    chat_id = r.json()["data"]["chat_id"]
    first = (await client.post(f"/api/v1/chats/{chat_id}/messages", headers=h,
                               json={"content": [{"insert": "Make a counter"}]})).json()["data"]
    assert first["queued"] is False
    second = (await client.post(f"/api/v1/chats/{chat_id}/messages", headers=h,
                                json={"content": [{"insert": "Make a todo list"}]})).json()["data"]
    assert second["queued"] is True

    loop = asyncio.get_running_loop()
    end = loop.time() + 60
    while loop.time() < end:
        async with session_scope() as s:
            runs = list((await s.execute(select(m.Run).where(m.Run.chat_id == chat_id))).scalars())
            pending = list((await s.execute(select(m.PromptQueueItem).where(
                m.PromptQueueItem.chat_id == chat_id, m.PromptQueueItem.status == "queued"))).scalars())
        if len(runs) == 2 and all(x.status == "success" for x in runs) and not pending:
            break
        await asyncio.sleep(0.05)
    assert [x.status for x in runs] == ["success", "success"] and not pending
    assert "src/components/TodoList.tsx" in fake_sandbox.files or any("todo" in p.lower() for p in fake_sandbox.files)
