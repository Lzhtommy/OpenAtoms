"""LLM access (ARCHITECTURE §7). `get_llm(model)` returns the scripted mock for `oa-mock*`, else an
OpenAI-compatible client pointed at OA_LLM_BASE_URL (LiteLLM proxy or any compatible provider)."""
from __future__ import annotations

from typing import Any

from oa.config import settings
from oa.llm.base import LLM, LLMError, LLMResult, ToolCall, Usage, estimate_tokens

__all__ = ["LLM", "LLMError", "LLMResult", "ToolCall", "Usage", "complete", "estimate_tokens", "get_llm", "resolve_model"]


def resolve_model(model: str | None) -> str:
    if not model or model == "auto":
        return settings.default_model
    return model


def get_llm(model: str | None = None) -> LLM:
    model = resolve_model(model)
    if model.startswith("oa-mock"):
        from oa.llm.mock import MockLLM

        return MockLLM(model)
    from oa.llm.openai_compat import OpenAICompatLLM

    return OpenAICompatLLM(model)


async def complete(messages: list[dict[str, Any]], model: str | None = None) -> LLMResult:
    """One-shot (non-tool) completion, e.g. titles and version summaries with the basic model."""
    llm = get_llm(model or settings.basic_model)
    if getattr(llm, "model", "").startswith("oa-mock"):
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        text = " ".join(str(user).split())[:60]
        return LLMResult(text=text, model=llm.model, usage=Usage(estimate_tokens(messages), estimate_tokens(text)))
    return await llm.stream(messages)
