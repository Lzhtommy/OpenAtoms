"""Real OpenAI-compatible client path against the mock provider (oa.llm.mock_server) — no oa-mock shortcut.

Covers SSE parsing, tool-call reassembly across many deltas, include_usage, retries on 503, auth, strict
request validation of every registered tool schema, and a full agent round (team mode) through the client.
"""
from __future__ import annotations

import json

import httpx
import pytest
from test_agents_runtime import fake_sandbox, make_chat, messages, send, wait_run  # noqa: F401

from oa.llm import get_llm, mock_server
from oa.llm.base import LLMError
from oa.llm.openai_compat import OpenAICompatLLM


@pytest.fixture
def provider(monkeypatch):
    """Route OpenAICompatLLM to the in-process mock provider; reset counters and knobs."""
    monkeypatch.setattr(OpenAICompatLLM, "transport", httpx.ASGITransport(app=mock_server.app))
    monkeypatch.setattr(mock_server.Cfg, "api_key", "sk-test")
    monkeypatch.setattr(mock_server.Cfg, "fail_every", 0)
    monkeypatch.setattr("oa.llm.openai_compat.settings.llm_base_url", "http://provider/v1")
    monkeypatch.setattr("oa.llm.openai_compat.settings.llm_api_key", "sk-test")
    monkeypatch.setattr("oa.llm.openai_compat.asyncio.sleep", _no_sleep)
    for k, v in list(mock_server.STATS.items()):
        mock_server.STATS[k] = type(v)() if not isinstance(v, int) else 0
    return mock_server


async def _no_sleep(_s: float) -> None:
    return None


def _tools(*names: str) -> list[dict]:
    import oa.tools  # noqa: F401  registers tools
    from oa.tools.base import schemas

    return schemas(list(names))


async def test_stream_text_and_reassembled_tool_calls(provider):
    llm = get_llm("claude-sonnet-4-5")
    assert isinstance(llm, OpenAICompatLLM)
    seen: list[str] = []

    async def on_text(t: str) -> None:
        seen.append(t)

    msgs = [{"role": "system", "content": "You are **Alex**, the Engineer."},
            {"role": "user", "content": "Make a counter app"}]
    res = await llm.stream(msgs, _tools("editor_read", "editor_write", "terminal_run", "reply_to_human"),
                           on_text=on_text, role="Engineer")
    assert res.text and "".join(seen) == res.text and len(seen) > 3
    assert [t.name for t in res.tool_calls] == ["editor_read"]
    assert json.loads(res.tool_calls[0].arguments) == {"path": "src/App.tsx"}
    assert res.tool_calls[0].id.startswith("call_") and res.finish_reason == "tool_calls"
    assert res.usage.input_tokens > 0 and res.usage.output_tokens > 0  # from the provider's usage chunk

    # next turn: long arguments (a whole component) arrive in 7-char fragments and must reassemble exactly
    msgs += [res.assistant_message(), {"role": "tool", "tool_call_id": res.tool_calls[0].id, "content": "export default 1"}]
    res2 = await llm.stream(msgs, _tools("editor_read", "editor_write", "terminal_run", "reply_to_human"), role="Engineer")
    writes = [json.loads(t.arguments) for t in res2.tool_calls if t.name == "editor_write"]
    assert writes and len(writes[0]["content"]) > 500 and "export default" in writes[0]["content"]
    assert provider.STATS["streams"] == 2 and provider.STATS["rejected"] == 0


async def test_retries_injected_503(provider, monkeypatch):
    monkeypatch.setattr(provider.Cfg, "fail_every", 2)  # requests 2, 4, … fail
    llm = get_llm("gpt-5")
    msgs = [{"role": "user", "content": "hi"}]
    for _ in range(3):
        assert (await llm.stream(msgs)).text.startswith("Summary:")
    assert provider.STATS["injected_failures"] >= 1 and provider.STATS["completions"] == 3


async def test_auth_and_validation_errors(provider, monkeypatch):
    with pytest.raises(LLMError, match="401"):
        await OpenAICompatLLM("gpt-5", api_key="wrong").stream([{"role": "user", "content": "x"}])
    bad = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]}]
    with pytest.raises(LLMError, match="never answered"):
        await get_llm("gpt-5").stream(bad)


def test_every_registered_tool_schema_is_provider_valid():
    import oa.tools  # noqa: F401
    from oa.tools.base import REGISTRY, schemas

    tools = schemas(sorted(REGISTRY))
    assert len(tools) >= 20
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": tools, "tool_choice": "auto",
            "stream": True, "stream_options": {"include_usage": True}}
    assert mock_server.validate(body) is None, mock_server.validate(body)


async def test_team_round_through_real_client(client, register, fake_sandbox, provider):  # noqa: F811
    chat_id, uid = await make_chat(client, register, agent_mode="full")
    r = await client.patch(f"/api/v1/chats/{chat_id}", headers={},
                           json={"llm": {"default_model": "claude-sonnet-4-5"}})
    assert r.status_code in (200, 401)  # model also forced via message metadata below
    await send(chat_id, uid, "Make a pomodoro timer", default_model="claude-sonnet-4-5")
    run = await wait_run(chat_id, timeout=120)
    assert run.status == "success", run.error
    assert run.model == "claude-sonnet-4-5"
    st = provider.STATS
    assert st["rejected"] == 0, st["errors"]
    assert {"Team Leader", "Product Manager", "Architect", "Engineer", "Data Analyst"} <= set(st["roles"])
    assert st["tool_calls"] >= 15 and st["models"] == {"claude-sonnet-4-5": st["completions"]}
    assert "src/components/Pomodoro.tsx" in fake_sandbox.files
    roles = {x.role for x in await messages(chat_id)}
    assert {"Team Leader", "Engineer"} <= roles
    # usage reported by the provider was billed
    from sqlalchemy import select

    from oa.db import models as m
    from oa.db.session import session_scope

    async with session_scope() as s:
        usage = (await s.execute(select(m.UsageEvent).where(m.UsageEvent.chat_id == chat_id))).scalars().all()
    assert usage and all(u.model == "claude-sonnet-4-5" for u in usage)
    assert sum(u.input_tokens for u in usage) == st["prompt_tokens"]
