"""OpenAI-compatible HTTP server backed by the oa-mock policy — a stand-in *provider* for verifying the real
client path (OpenAICompatLLM → HTTP/SSE → tool-call reassembly → retries → usage) without API keys.

    uv run python -m oa.llm.mock_server --port 4010 [--api-key K] [--fail-every N] [--delay 0.002]

Endpoints: GET /v1/models, POST /v1/chat/completions (stream and non-stream), POST /v1/images/generations,
GET /stats (counters for assertions). Requests are validated as strictly as hosted providers do (message
roles/ordering, tool-call ids, function-name pattern, JSON-schema `parameters` shape, stream_options only with
stream) so provider-incompatible payloads fail loudly here instead of in production.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import struct
import time
import uuid
import zlib
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from oa.llm.base import estimate_tokens
from oa.llm.mock import MockLLM

NAME_TO_ROLE = {"Mike": "Team Leader", "Emma": "Product Manager", "Bob": "Architect", "Alex": "Engineer",
                "David": "Data Analyst", "Iris": "Deep Researcher", "Sarah": "SEO Specialist", "Adrian": "Ads Specialist"}
FN_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MODELS = ["claude-sonnet-4-5", "gpt-5", "deepseek-chat", "kimi-k2", "oa-mock-remote"]


class Cfg:
    api_key = ""
    fail_every = 0
    delay = 0.0
    chunk = 7  # tool-call argument chunk size (forces reassembly across many deltas)


STATS: dict[str, Any] = {"requests": 0, "completions": 0, "streams": 0, "injected_failures": 0, "rejected": 0,
                         "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "models": Counter(),
                         "roles": Counter(), "errors": []}


def _err(status: int, msg: str, typ: str = "invalid_request_error") -> JSONResponse:
    if status == 400:
        STATS["rejected"] += 1
        STATS["errors"].append(msg)
    return JSONResponse({"error": {"message": msg, "type": typ, "code": None}}, status_code=status)


def _check_schema(name: str, params: Any) -> str | None:
    if not isinstance(params, dict) or params.get("type") != "object":
        return f"tools[{name}].function.parameters must be a JSON schema with type 'object'"
    props = params.get("properties", {})
    if not isinstance(props, dict):
        return f"tools[{name}]: properties must be an object"
    for k, v in props.items():
        if not isinstance(v, dict):
            return f"tools[{name}].{k}: property schema must be an object"
        bad = set(v) - {"type", "description", "enum", "items", "properties", "required", "default", "minimum",
                        "maximum", "minItems", "maxItems", "additionalProperties", "anyOf", "oneOf", "format",
                        "pattern", "minLength", "maxLength", "title", "nullable", "const"}
        if bad:
            return f"tools[{name}].{k}: unsupported schema keywords {sorted(bad)}"
    req = params.get("required", [])
    if not isinstance(req, list) or any(r not in props for r in req):
        return f"tools[{name}]: 'required' must list existing properties"
    return None


def validate(body: dict[str, Any]) -> str | None:
    if not isinstance(body.get("model"), str) or not body["model"]:
        return "model is required"
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return "messages must be a non-empty array"
    open_calls: set[str] = set()
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            return f"messages[{i}].role invalid: {role!r}"
        if role == "tool":
            tid = m.get("tool_call_id")
            if tid not in open_calls:
                return f"messages[{i}]: tool message with unknown tool_call_id {tid!r}"
            open_calls.discard(tid)
            continue
        if open_calls and role != "tool":
            return f"messages[{i}]: tool_calls {sorted(open_calls)} must be answered by tool messages first"
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("type") != "function" or not tc.get("id"):
                    return f"messages[{i}].tool_calls: each call needs id and type='function'"
                fn = tc.get("function") or {}
                try:
                    json.loads(fn.get("arguments") or "")
                except (TypeError, ValueError):
                    return f"messages[{i}].tool_calls[{tc.get('id')}].arguments must be a JSON string"
                open_calls.add(tc["id"])
            if m.get("content") is None and not m.get("tool_calls"):
                return f"messages[{i}]: assistant message needs content or tool_calls"
        elif not isinstance(m.get("content"), (str, list)):
            return f"messages[{i}].content must be a string or content parts"
    if open_calls:
        return f"tool_calls {sorted(open_calls)} were never answered"
    names: set[str] = set()
    for t in body.get("tools") or []:
        fn = (t or {}).get("function") or {}
        if t.get("type") != "function" or not FN_NAME.match(fn.get("name", "")):
            return f"invalid tool definition: {json.dumps(t)[:200]}"
        if fn["name"] in names:
            return f"duplicate tool name {fn['name']}"
        names.add(fn["name"])
        if msg := _check_schema(fn["name"], fn.get("parameters")):
            return msg
    tc = body.get("tool_choice")
    if tc is not None and tc not in ("auto", "none", "required") and not isinstance(tc, dict):
        return "tool_choice must be auto|none|required|object"
    if body.get("stream_options") is not None and not body.get("stream"):
        return "stream_options is only allowed when stream=true"
    return None


def infer_role(msgs: list[dict[str, Any]]) -> str:
    for m in msgs:
        if m.get("role") == "system":
            if hit := re.search(r"You are \*\*(\w+)\*\*", str(m.get("content") or "")):
                return NAME_TO_ROLE.get(hit.group(1), "")
            break
    return ""


def _chunk(cid: str, model: str, delta: dict[str, Any] | None = None, finish: str | None = None,
           usage: dict[str, int] | None = None) -> str:
    obj: dict[str, Any] = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                           "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        obj["usage"] = usage
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


app = FastAPI(title="oa mock provider")


def _auth(req: Request) -> JSONResponse | None:
    if Cfg.api_key and req.headers.get("authorization") != f"Bearer {Cfg.api_key}":
        return _err(401, "Incorrect API key provided", "authentication_error")
    return None


@app.get("/v1/models")
async def models(req: Request):
    if bad := _auth(req):
        return bad
    return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "openatoms-mock"} for m in MODELS]}


@app.get("/stats")
async def stats():
    return {**STATS, "models": dict(STATS["models"]), "roles": dict(STATS["roles"]), "errors": STATS["errors"][-20:]}


@app.post("/stats/reset")
async def stats_reset():
    for k, v in list(STATS.items()):
        STATS[k] = Counter() if isinstance(v, Counter) else [] if isinstance(v, list) else 0
    return {"ok": True}


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    if bad := _auth(req):
        return bad
    STATS["requests"] += 1
    if Cfg.fail_every and STATS["requests"] % Cfg.fail_every == 0:
        STATS["injected_failures"] += 1
        return _err(503, "upstream overloaded (injected)", "server_error")
    try:
        body = await req.json()
    except ValueError:
        return _err(400, "body must be JSON")
    if msg := validate(body):
        return _err(400, msg)
    model, msgs, tools = body["model"], body["messages"], body.get("tools") or []
    role = infer_role(msgs)
    STATS["models"][model] += 1
    STATS["roles"][role or "-"] += 1
    STATS["completions"] += 1
    if role or tools:
        result = await MockLLM(model, delay=0).stream(msgs, tools, role=role)
    else:  # plain completion (titles, summaries, app ai-hub)
        user = next((str(m.get("content") or "") for m in reversed(msgs) if m.get("role") == "user"), "")
        from oa.llm.base import LLMResult

        result = LLMResult(text=f"Summary: {' '.join(user.split())[:80]}", model=model)
    usage = {"prompt_tokens": estimate_tokens(msgs),
             "completion_tokens": estimate_tokens(result.text) + estimate_tokens([t.arguments for t in result.tool_calls])}
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    STATS["prompt_tokens"] += usage["prompt_tokens"]
    STATS["completion_tokens"] += usage["completion_tokens"]
    STATS["tool_calls"] += len(result.tool_calls)
    finish = "tool_calls" if result.tool_calls else "stop"
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    calls = [{"id": f"call_{uuid.uuid4().hex[:16]}", "name": t.name, "arguments": t.arguments} for t in result.tool_calls]

    if not body.get("stream"):
        message: dict[str, Any] = {"role": "assistant", "content": result.text or None}
        if calls:
            message["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
        return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}

    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    STATS["streams"] += 1

    async def gen():
        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        for tok in re.findall(r"[一-鿿]|\s*\S{1,6}|\s+", result.text):
            yield _chunk(cid, model, {"content": tok})
            if Cfg.delay:
                await asyncio.sleep(Cfg.delay)
        for i, c in enumerate(calls):
            # like real providers: first delta carries id+name, arguments follow in small fragments
            yield _chunk(cid, model, {"tool_calls": [{"index": i, "id": c["id"], "type": "function",
                                                      "function": {"name": c["name"], "arguments": ""}}]})
            args = c["arguments"]
            for j in range(0, len(args), Cfg.chunk):
                yield _chunk(cid, model, {"tool_calls": [{"index": i, "function": {"arguments": args[j:j + Cfg.chunk]}}]})
        yield _chunk(cid, model, {}, finish)
        if include_usage:
            yield _chunk(cid, model, usage=usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _png(w: int = 64, h: int = 64, rgb: tuple[int, int, int] = (66, 103, 255)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@app.post("/v1/images/generations")
async def images(req: Request):
    if bad := _auth(req):
        return bad
    body = await req.json()
    if not body.get("prompt"):
        return _err(400, "prompt is required")
    n = int(body.get("n") or 1)
    return {"created": int(time.time()),
            "data": [{"b64_json": base64.b64encode(_png()).decode(), "revised_prompt": body["prompt"]} for _ in range(n)]}


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4010)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--fail-every", type=int, default=0, help="return 503 on every Nth completion request")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between streamed text tokens")
    a = ap.parse_args()
    Cfg.api_key, Cfg.fail_every, Cfg.delay = a.api_key, a.fail_every, a.delay
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
