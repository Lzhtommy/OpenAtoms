"""Visual editing: JSX source rewriting (real Node + TypeScript), staging/merge semantics and the edit-session API."""
from __future__ import annotations

import shutil
import textwrap

import pytest

from oa import visual_edit as ve
from oa.config import settings
from tests.conftest import register_user

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (settings.template_dir / "node_modules" / "typescript").exists(),
    reason="needs node + template node_modules/typescript",
)

SRC = textwrap.dedent('''\
    import { cn } from "@/lib/utils";

    export default function App({ active }: { active: boolean }) {
      const k = "x";
      return (
        <main className="flex min-h-screen p-6">
          <h1 className="text-2xl font-bold">Hello world</h1>
          <p>Plain text</p>
          <img src="/logo.svg" />
          <button className={cn("rounded px-3", active && "bg-blue-600")}>Go</button>
          <span className={`text-sm ${k}`}>tpl</span>
          <div className={k}>dyn</div>
          <label className={"text-xs"}>{"quoted"}</label>
          <section>
            <b>nested</b> text
          </section>
          <em></em>
        </main>
      );
    }
''')


def loc_of(snippet: str, src: str = SRC) -> str:
    """1-based line:col of the `<` that starts `snippet`."""
    i = src.index(snippet)
    line = src.count("\n", 0, i) + 1
    col = i - (src.rfind("\n", 0, i) + 1) + 1
    return f"src/App.tsx:{line}:{col}"


async def run(edits: list[dict]) -> tuple[str, list[dict]]:
    return await ve.rewrite_source(settings.template_dir, "src/App.tsx", SRC, [ve.normalize(e) for e in edits])


async def test_string_classname_replace_and_text():
    out, res = await run([{"loc": loc_of("<h1"), "add": ["text-4xl"], "remove": ["text-2xl"], "text": "Hi <there>"}])
    assert res == [{"ok": True}]
    assert '<h1 className="font-bold text-4xl">{"Hi <there>"}</h1>' in out


async def test_insert_classname_and_plain_text():
    out, res = await run([{"loc": loc_of("<p>"), "add": ["text-red-500"], "text": "New copy"}])
    assert res[0]["ok"] and '<p className="text-red-500">New copy</p>' in out


async def test_self_closing_classes_ok_text_rejected():
    out, res = await run([{"loc": loc_of("<img"), "add": ["rounded-full"]}])
    assert res[0]["ok"] and '<img className="rounded-full" src="/logo.svg" />' in out
    _, res = await run([{"loc": loc_of("<img"), "text": "x"}])
    assert not res[0]["ok"] and "no text" in res[0]["error"]


async def test_cn_call_appends_and_removes():
    out, res = await run([{"loc": loc_of("<button"), "add": ["px-6"], "remove": ["px-3"]}])
    assert res[0]["ok"]
    assert 'className={cn("rounded", active && "bg-blue-600", "px-6")}' in out


async def test_template_and_dynamic_classname_fallback():
    _, res = await run([{"loc": loc_of("<span"), "add": ["font-bold"]}, {"loc": loc_of("<div className={k}"), "add": ["p-2"]}])
    assert not res[0]["ok"] and "template literal" in res[0]["error"]
    assert not res[1]["ok"] and "dynamically" in res[1]["error"]


async def test_expression_literals_and_nested_text():
    out, res = await run([{"loc": loc_of("<label"), "add": ["font-mono"], "text": "Name"}])
    assert res[0]["ok"] and '<label className={"text-xs font-mono"}>{"Name"}</label>' in out
    _, res = await run([{"loc": loc_of("<section"), "text": "flat"}])
    assert not res[0]["ok"] and "nested" in res[0]["error"]
    out, res = await run([{"loc": loc_of("<em>"), "text": "fill"}])
    assert res[0]["ok"] and "<em>fill</em>" in out


async def test_multiple_edits_one_parse_and_stale_loc():
    out, res = await run([
        {"loc": loc_of("<h1"), "text": "A"},
        {"loc": loc_of("<p>"), "text": "B"},
        {"loc": "src/App.tsx:999:1", "text": "C"},
    ])
    assert [r["ok"] for r in res] == [True, True, False] and "file changed" in res[2]["error"]
    assert ">A</h1>" in out and "<p>B</p>" in out


def test_normalize_and_merge():
    with pytest.raises(ve.EditError):
        ve.normalize({"loc": "../etc/passwd:1:1"})
    with pytest.raises(ve.EditError):
        ve.normalize({"loc": "node_modules/x/a.tsx:1:1"})
    with pytest.raises(ve.EditError):
        ve.normalize({"loc": "src/App.tsx:1:1", "add": ['a"b']})
    e = ve.normalize({"loc": "src/App.tsx:3:5", "classes": "p-4 text-lg", "orig_classes": "p-4 text-sm"})
    assert e.add == ["text-lg"] and e.remove == ["text-sm"]
    # text-sm → text-lg → text-xl folds to one net edit
    merged = ve.merge([e], [ve.normalize({"loc": "src/App.tsx:3:5", "add": ["text-xl"], "remove": ["text-lg"]})])
    assert len(merged) == 1 and merged[0].add == ["text-xl"] and set(merged[0].remove) == {"text-sm", "text-lg"}
    # an edit undone back to the original disappears
    undo = ve.merge([ve.normalize({"loc": "src/App.tsx:3:5", "add": ["p-8"]})],
                    [ve.normalize({"loc": "src/App.tsx:3:5", "remove": ["p-8"]})])
    assert undo == [] or (not undo[0].add)


async def test_edit_session_api_commit(client, fake_runtime):
    u = await register_user(client)
    h = u["headers"]
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "VE"})).json()["data"]["chat_id"]
    # an existing conversation round that must stay visible after the visual edit
    from oa.domain.messages import add_message
    from oa.db.session import SessionLocal as _S

    async with _S() as s:
        await add_message(s, cid, role="User", name="u", type="message", content=[{"insert": "build it"}], round_id=1)
        await add_message(s, cid, role="Engineer", name="Alex", type="message", content=[{"insert": "done"}], round_id=1)
        await s.commit()
    ws = settings.workspace_dir(cid)
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "App.tsx").write_text(SRC)
    (ws / "index.html").write_text("<html><body><div id=root></div></body></html>")

    base = f"/api/v1/chats/{cid}/edit-session"
    assert (await client.post(base, headers=h, json={"edits": [{"loc": "/abs/x.tsx:1:1"}]})).json()["code"] == 4400
    r = (await client.post(base, headers=h, json={"edits": [
        {"loc": loc_of("<h1"), "classes": ["font-bold", "text-5xl"], "orig_classes": ["text-2xl", "font-bold"],
         "tag": "h1", "selector": "main > h1"}]})).json()["data"]
    assert r["count"] == 1 and r["edits"][0]["add"] == ["text-5xl"]
    r = (await client.post(base, headers=h, json={"edits": [{"loc": loc_of("<h1"), "text": "Visual!"}]})).json()["data"]
    assert r["count"] == 1 and r["edits"][0]["text"] == "Visual!"
    assert (await client.get(base, headers=h)).json()["data"]["count"] == 1

    # failing edit → nothing written, staged edits kept
    await client.post(base, headers=h, json={"edits": [{"loc": loc_of("<span"), "add": ["x-1"]}]})
    bad = (await client.post(base + "/commit", headers=h)).json()
    assert bad["code"] == 4400 and bad["data"]["errors"][0]["loc"] == loc_of("<span")
    assert (ws / "src" / "App.tsx").read_text() == SRC
    await client.delete(base, headers=h)
    assert (await client.get(base, headers=h)).json()["data"]["count"] == 0

    await client.post(base, headers=h, json={"edits": [{"loc": loc_of("<h1"), "add": ["text-5xl"], "remove": ["text-2xl"],
                                                       "text": "Visual!", "tag": "h1"}]})
    res = (await client.post(base + "/commit", headers=h)).json()
    assert res["code"] == 0, res
    assert res["data"]["files"] == ["src/App.tsx"] and res["data"]["version"]["status"] == "ready"
    assert '<h1 className="font-bold text-5xl">Visual!</h1>' in (ws / "src" / "App.tsx").read_text()
    assert (await client.get(base, headers=h)).json()["data"]["count"] == 0

    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    ve_msg = next(x for x in hist["messages"] if x["metadata"].get("visual_edit"))
    assert ve_msg["role"] == "System" and any("action_data" in op for op in ve_msg["content"])
    texts = [x["content"][0].get("insert") for x in hist["messages"] if isinstance(x["content"], list) and x["content"]]
    assert "build it" in texts and "done" in texts  # regression: the round tree is untouched
    ad = next(a for a in hist["action_datas"] if a["block_type"] == "Editor")
    assert ad["status"] == "success" and ad["file"].startswith(".storage/")
    tls = (await client.get(f"/api/v1/chats/{cid}/block-timelines", headers=h)).json()["data"]
    assert any(t["block_owner"] == "User" and t["command_name"] == "editor_edit_file_by_replace" for t in tls)
    vs = (await client.get(f"/api/v1/chats/{cid}/versions", headers=h)).json()["data"]
    assert vs[0]["summary"].startswith("Visual edit:")

    # agents busy → commit refused
    from oa.db import models as m
    from oa.db.session import SessionLocal

    async with SessionLocal() as s:
        chat = await s.get(m.Chat, cid)
        chat.is_running = True
        await s.commit()
    await client.post(base, headers=h, json={"edits": [{"loc": loc_of("<p>"), "text": "z"}]})
    assert (await client.post(base + "/commit", headers=h)).json()["code"] == 4409


def test_selection_reaches_prompt():
    from oa.agents.runner import _prompt_text
    from oa.db import models as m

    msg = m.Message(content=[{"insert": "make this bigger"}], metadata_={"selection": {
        "loc": "src/App.tsx:7:7", "tag": "h1", "selector": "main > h1", "classes": ["text-2xl"], "text": "Hello"}})
    p = _prompt_text(msg)
    assert "src/App.tsx:7:7" in p and "text-2xl" in p and "make this bigger" in p
