"""OpenAI-compatible streaming chat client (LiteLLM proxy, OpenAI, DeepSeek, Moonshot, ...)."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from oa.config import settings
from oa.llm.base import LLMError, LLMResult, TextCallback, ToolCall, Usage, estimate_tokens

log = logging.getLogger("oa.llm")

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 4


class OpenAICompatLLM:
    transport: httpx.AsyncBaseTransport | None = None  # test seam

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: TextCallback | None = None,
        *,
        role: str = "",
        temperature: float | None = None,
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if temperature is not None:
            body["temperature"] = temperature

        last_err: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await self._once(body, on_text)
            except _Retryable as e:
                if e.emitted:
                    raise LLMError(str(e)) from e  # can't transparently retry after partial output
                last_err = e
            except httpx.TransportError as e:
                last_err = e
            delay = min(20.0, (2**attempt) + random.random())
            log.warning("LLM %s attempt %d failed (%s); retrying in %.1fs", self.model, attempt + 1, last_err, delay)
            await asyncio.sleep(delay)
        raise LLMError(f"LLM request failed after {MAX_ATTEMPTS} attempts: {last_err}")

    async def _once(self, body: dict[str, Any], on_text: TextCallback | None) -> LLMResult:
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish: str | None = None
        emitted = False
        timeout = httpx.Timeout(300.0, connect=10.0, read=180.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as http:
            async with http.stream("POST", f"{self.base_url}/chat/completions", json=body,
                                   headers=self._headers()) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:2000]
                    if r.status_code in RETRY_STATUS:
                        raise _Retryable(f"HTTP {r.status_code}: {detail}", emitted=False)
                    raise LLMError(f"LLM HTTP {r.status_code}: {detail}")
                try:
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(f"LLM stream error: {chunk['error']}")
                        if u := chunk.get("usage"):
                            usage = Usage(u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0)
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
                            if c := delta.get("content"):
                                text_parts.append(c)
                                emitted = True
                                if on_text:
                                    await on_text(c)
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": []})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["args"].append(fn["arguments"])
                except httpx.TransportError as e:
                    raise _Retryable(f"stream interrupted: {e}", emitted=emitted) from e

        tool_calls = [
            ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments="".join(s["args"]) or "{}")
            for i, s in sorted(calls.items())
            if s["name"]
        ]
        text = "".join(text_parts)
        if not usage.input_tokens:
            usage = Usage(estimate_tokens(body["messages"]), estimate_tokens(text) + estimate_tokens(
                [t.arguments for t in tool_calls]))
        return LLMResult(text=text, tool_calls=tool_calls, usage=usage, finish_reason=finish, model=self.model)


class _Retryable(Exception):
    def __init__(self, msg: str, emitted: bool) -> None:
        super().__init__(msg)
        self.emitted = emitted
