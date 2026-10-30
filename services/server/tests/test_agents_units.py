"""Unit tests: mock model decisions, tool helpers, context compaction, SSE client parsing."""
from __future__ import annotations

import json

import httpx
import pytest

from oa.agents.role_loop import compact
from oa.agents.roles import find_role
from oa.llm import get_llm
from oa.llm import mock_apps as apps
from oa.llm.mock import MockLLM
from oa.sandbox.client import SandboxClient
from oa.tools import REGISTRY
from oa.tools.editor import norm_path
from oa.tools.terminal import _DEV_SERVER, is_build_cmd
from oa.tools.web import extract_links, html_to_text


def test_registry_schemas_valid():
    for name, spec in REGISTRY.items():
        sch = spec.schema()
        assert sch["function"]["name"] == name
        assert sch["function"]["parameters"]["type"] == "object"
        for req in sch["function"]["parameters"].get("required", []):
            assert req in sch["function"]["parameters"]["properties"]


def test_get_llm_routes_mock():
    assert isinstance(get_llm("oa-mock"), MockLLM)
    assert get_llm("gpt-5").__class__.__name__ == "OpenAICompatLLM"


@pytest.mark.parametrize("req,kind", [
    ("make a todo app", "todo"), ("做一个番茄钟", "pomodoro"), ("tally counter please", "counter"),
    ("a site for my bakery", "landing"), ("带登录的待办应用", "todo"),
])
def test_detect_kind(req, kind):
    assert apps.detect_kind(req) == kind


def test_wants_login():
    assert apps.wants_login("做一个带登录的待办应用")
    assert not apps.wants_login("a simple todo list")


async def test_mock_streams_tokens_and_tool_calls():
    llm = MockLLM(delay=0)
    chunks: list[str] = []

    async def on_text(t: str) -> None:
        chunks.append(t)

    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "User requirement:\nMake a counter"}]
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in REGISTRY]
    res = await llm.stream(msgs, tools, on_text, role="Team Leader")
    assert "".join(chunks) == res.text and len(chunks) > 3
    assert [t.name for t in res.tool_calls] == ["plan_append_task"] * 4
    assert json.loads(res.tool_calls[0].arguments)["assignee"] == "David"
    assert res.usage.input_tokens > 0 and res.usage.output_tokens > 0


async def test_mock_respects_tool_availability():
    llm = MockLLM(delay=0)
    msgs = [{"role": "user", "content": "Make a counter"}]
    res = await llm.stream(msgs, [{"type": "function", "function": {"name": "reply_to_human", "parameters": {}}}],
                           role="Engineer")
    assert all(t.name == "reply_to_human" for t in res.tool_calls)


def test_norm_path():
    assert norm_path("/workspace/src/App.tsx") == "src/App.tsx"
    assert norm_path("./src//a.ts") == "src/a.ts"
    for bad in ("../etc/passwd", "src/../../x", "", "/"):
        with pytest.raises(ValueError):
            norm_path(bad)


def test_terminal_guards():
    assert _DEV_SERVER.search("pnpm dev")
    assert _DEV_SERVER.search("npm run dev -- --host")
    assert not _DEV_SERVER.search("pnpm build")
    assert not _DEV_SERVER.search("vite build")
    assert is_build_cmd("pnpm build") and is_build_cmd("npx tsc --noEmit") and not is_build_cmd("ls")


def test_find_role():
    assert find_role("David").role == "Data Analyst"
    assert find_role("@alex").name == "Alex"
    assert find_role("Engineer").name == "Alex"
    assert find_role("nobody") is None


def test_compact_shrinks_old_outputs():
    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(20):
        args = json.dumps({"path": f"f{i}.ts", "content": "x" * 5000})
        hist.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "editor_write",
                                                                                     "arguments": args}}]})
        hist.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 5000})
    out = compact(hist)
    assert len(out) == len(hist)
    assert len(out[3]["content"]) < 2000  # old tool output truncated
    assert len(out[-1]["content"]) == 5000  # recent kept
    old_args = json.loads(out[2]["tool_calls"][0]["function"]["arguments"])
    assert "already applied" in old_args["content"]
    assert json.loads(out[-2]["tool_calls"][0]["function"]["arguments"])["content"] == "x" * 5000


def test_html_helpers():
    raw = '<html><head><title>Hi &amp; bye</title><style>x{}</style></head><body><h1>Head</h1><p>Para <a href="/x">Link</a></p><script>bad()</script></body></html>'
    text = html_to_text(raw)
    assert "Head" in text and "Para" in text and "bad()" not in text
    assert extract_links(raw, "https://e.com/a") == [("Link", "https://e.com/x")]


async def test_sandbox_client_parses_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Sandbox-Token"] == "tok"
        body = (
            'data: {"type":"stdout","data":"hello\\n"}\n\n'
            'data: {"type":"stderr","data":"warn\\n"}\n\n'
            'data: {"type":"exit","code":3,"timed_out":false}\n\n'
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    c = SandboxClient("http://sbx", "tok")
    c._http = httpx.AsyncClient(base_url="http://sbx", headers={"X-Sandbox-Token": "tok"},
                                transport=httpx.MockTransport(handler))
    code, out = await c.run("echo hello")
    assert code == 3 and out == "hello\nwarn\n"


async def test_openai_compat_stream_accumulates_tool_calls(monkeypatch):
    from oa.llm import openai_compat
    from oa.llm.openai_compat import OpenAICompatLLM

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")  # retried transparently
        body = json.loads(request.content)
        assert body["stream"] is True and body["tools"][0]["function"]["name"] == "editor_write"
        chunks = [
            {"choices": [{"delta": {"content": "Writing "}}]},
            {"choices": [{"delta": {"content": "file."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "editor_write",
                                                                                              "arguments": '{"path":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a.ts","content":"x"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30}},
        ]
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    async def no_sleep(_):  # skip retry backoff
        return None

    monkeypatch.setattr(openai_compat.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(OpenAICompatLLM, "transport", httpx.MockTransport(handler))
    llm = OpenAICompatLLM("test-model", base_url="http://llm/v1", api_key="k")
    got: list[str] = []

    async def on_text(t: str) -> None:
        got.append(t)

    tools = [REGISTRY["editor_write"].schema()]
    res = await llm.stream([{"role": "user", "content": "hi"}], tools, on_text)
    assert "".join(got) == res.text == "Writing file."
    assert len(res.tool_calls) == 1 and res.tool_calls[0].id == "call_1"
    assert json.loads(res.tool_calls[0].arguments) == {"path": "a.ts", "content": "x"}
    assert (res.usage.input_tokens, res.usage.output_tokens) == (120, 30)
    assert res.assistant_message()["tool_calls"][0]["function"]["name"] == "editor_write"
    assert calls["n"] == 2
