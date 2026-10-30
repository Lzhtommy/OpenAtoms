from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TextCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model

    def to_openai(self) -> dict[str, Any]:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str = ""

    def assistant_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        return msg


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    model: str

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: TextCallback | None = None,
        *,
        role: str = "",
        temperature: float | None = None,
    ) -> LLMResult: ...


def estimate_tokens(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, str):
        return max(1, len(obj) // 4)
    if isinstance(obj, dict):
        return sum(estimate_tokens(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(estimate_tokens(v) for v in obj)
    return max(1, len(str(obj)) // 4)
