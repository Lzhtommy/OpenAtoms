"""Tool registry + block recording (CONTRACT §3).

Every tool with a block_type records exactly one ActionData + one BlockTimeline:
  start    → action_data:update(running) + timeline:start, inline {"action_data":{uuid}} in the think message
  content  → timeline:content (append|replace, coalesced ~50ms)
  complete → snapshot to storage chats/{id}/.storage/{index}/{hash8}/{name}, timeline:complete, action_data:update
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import update

from oa.agents.emit import Coalescer
from oa.core.events import bus
from oa.core.storage import get_storage
from oa.db.models import ActionData, BlockTimeline, Chat
from oa.db.session import session_scope
from oa.domain import serializers as ser

if TYPE_CHECKING:
    from oa.agents.context import RunContext
    from oa.agents.emit import StreamingMessage
    from oa.agents.roles import RoleDef

log = logging.getLogger("oa.tools")

MAX_TIMELINE_CONTENT = 200_000


@dataclass
class ToolResult:
    output: str  # what the model sees
    ok: bool = True
    end_loop: bool = False  # role loop should stop after this step (finish task / reply / mention)
    suspend: str | None = None  # ask_human | questions | plan_ready
    message_id: int | None = None  # card message for suspension
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    rc: RunContext
    role: RoleDef
    message: StreamingMessage | None
    tool_call_id: str = ""

    @property
    def chat_id(self) -> str:
        return self.rc.chat_id

    def record(self, command_name: str, block_type: str, *, src_path: str | None = None,
               extra: dict[str, Any] | None = None) -> Block:
        return Block(self, command_name, block_type, src_path=src_path, extra=extra or {})


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    block_type: str | None = None

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, parameters: dict[str, Any], block_type: str | None = None):
    """Decorator registering a tool handler."""

    def deco(fn: Handler) -> Handler:
        REGISTRY[name] = ToolSpec(name, description, _obj(parameters), fn, block_type)
        return fn

    return deco


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    if props.get("type") == "object":
        return props
    required = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": required}


def schemas(names: list[str]) -> list[dict[str, Any]]:
    return [REGISTRY[n].schema() for n in names if n in REGISTRY]


async def execute(ctx: ToolContext, name: str, arguments: str | dict[str, Any]) -> ToolResult:
    spec = REGISTRY.get(name)
    if spec is None:
        return ToolResult(f"Error: unknown tool '{name}'. Available: {', '.join(ctx.role.tools)}", ok=False)
    if name not in ctx.role.tools:
        return ToolResult(f"Error: tool '{name}' is not available to {ctx.role.name}.", ok=False)
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            return ToolResult(f"Error: arguments are not valid JSON ({e}). Retry with valid JSON.", ok=False)
    else:
        args = arguments
    if not isinstance(args, dict):
        return ToolResult("Error: arguments must be a JSON object.", ok=False)
    missing = [k for k in spec.parameters.get("required", []) if k not in args]
    if missing:
        return ToolResult(f"Error: missing required argument(s): {', '.join(missing)}", ok=False)
    ctx.rc.check_cancel()
    try:
        return await spec.handler(ctx, args)
    except Exception as e:
        from oa.agents.context import RunCancelled, Suspend
        from oa.sandbox.manager import SandboxFull

        # no sandbox capacity: every further tool call would fail too — end the run with a clear error
        if isinstance(e, (RunCancelled, Suspend, SandboxFull)):
            raise
        log.exception("tool %s failed", name)
        return ToolResult(f"Error while running {name}: {type(e).__name__}: {e}", ok=False)


# ---------------------------------------------------------------- block recording
async def next_timeline_index(chat_id: str) -> int:
    async with session_scope() as s:
        res = await s.execute(
            update(Chat).where(Chat.id == chat_id).values(timeline_index=Chat.timeline_index + 1)
            .returning(Chat.timeline_index)
        )
        return int(res.scalar_one())


class Block:
    def __init__(self, ctx: ToolContext, command_name: str, block_type: str, *, src_path: str | None,
                 extra: dict[str, Any]) -> None:
        self.ctx = ctx
        self.command_name = command_name
        self.block_type = block_type
        self.src_path = src_path
        self.extra = extra
        self.index = 0
        self.ad_id = 0
        self.uuid = ""
        self.tl_id = 0
        self.content = ""
        self.has_secret = False
        self.done = False
        self._co = Coalescer(self._flush)

    async def __aenter__(self) -> Block:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self.done:
            await self.complete(status="failed" if exc_type else "success")

    async def start(self) -> None:
        rc = self.ctx.rc
        if self.ctx.message is not None:
            await self.ctx.message.ensure()
        self.index = await next_timeline_index(rc.chat_id)
        async with session_scope() as s:
            ad = ActionData(
                chat_id=rc.chat_id, message_uuid=self.ctx.message.uuid if self.ctx.message else None,
                command_name=self.command_name, block_type=self.block_type, src_path=self.src_path,
                extra=self.extra, status="running", timeline_index=self.index,
            )
            tl = BlockTimeline(
                chat_id=rc.chat_id, index=self.index, block_type=self.block_type, block_owner=self.ctx.role.role,
                command_name=self.command_name, src_path=self.src_path, content="", is_running=True,
                extra_data=self.extra or None, version=rc.version_label,
            )
            s.add_all([ad, tl])
            await s.flush()
            await s.refresh(ad)
            await s.refresh(tl)
            self.ad_id, self.uuid, self.tl_id = ad.id, ad.uuid, tl.id
            ad_payload, tl_payload = ser.action_data(ad), ser.timeline(tl)
        await bus.emit(rc.chat_id, "action_data:update", {"action_data": ad_payload})
        await bus.emit(rc.chat_id, "timeline:start", {"timeline": tl_payload})
        if self.ctx.message is not None:
            await self.ctx.message.add_action(self.uuid)

    async def _flush(self, items: list[tuple[str, str]]) -> None:
        # collapse: any replace wins over earlier items; consecutive appends concatenate
        mode, buf = "append", []
        for m, text in items:
            if m == "replace":
                mode, buf = "replace", [text]
            else:
                buf.append(text)
        payload = self.content if mode == "replace" else "".join(buf)
        await bus.emit(self.ctx.rc.chat_id, "timeline:content",
                       {"index": self.index, "content": payload, "mode": mode})

    async def append(self, text: str) -> None:
        text, hit = self.ctx.rc.mask(text)
        self.has_secret |= hit
        self.content += text
        await self._co.push(("append", text))

    async def replace(self, text: str) -> None:
        text, hit = self.ctx.rc.mask(text)
        self.has_secret |= hit
        self.content = text
        await self._co.push(("replace", text))

    async def complete(
        self,
        *,
        status: str = "success",
        snapshots: dict[str, bytes | str] | None = None,
        extra_data: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        """Finish the block; returns the `.storage/...` path of the primary snapshot (first entry)."""
        if self.done:
            return None
        self.done = True
        await self._co.close()
        rc = self.ctx.rc
        file_rel: str | None = None
        if snapshots:
            blob = b"".join(_b(v) for v in snapshots.values())
            h = hashlib.sha1(blob).hexdigest()[:8]
            st = get_storage()
            for i, (name, data) in enumerate(snapshots.items()):
                raw = _b(data)
                if self.ctx.rc.secrets and name.endswith((".json", ".log", ".txt", ".md", ".ipynb")):
                    masked, hit = rc.mask(raw.decode("utf-8", "replace"))
                    self.has_secret |= hit
                    raw = masked.encode()
                rel = f".storage/{self.index}/{h}/{name}"
                await st.put_bytes(f"chats/{rc.chat_id}/{rel}", raw)
                if i == 0:
                    file_rel = rel
        if extra:
            self.extra = {**self.extra, **extra}
        stored = self.content if len(self.content) <= MAX_TIMELINE_CONTENT else (
            self.content[:2000] + "\n…[truncated]…\n" + self.content[-(MAX_TIMELINE_CONTENT - 2100):])
        async with session_scope() as s:
            ad = await s.get(ActionData, self.ad_id)
            tl = await s.get(BlockTimeline, self.tl_id)
            ad.status = status
            ad.file = file_rel
            ad.extra = self.extra
            tl.file = file_rel
            tl.content = stored
            tl.is_running = False
            tl.has_secret = self.has_secret
            if extra_data is not None:
                tl.extra_data = extra_data
            ad_payload = ser.action_data(ad)
            tl_extra = tl.extra_data
        await bus.emit(rc.chat_id, "timeline:complete",
                       {"index": self.index, "file": file_rel, "extra_data": tl_extra, "is_running": False})
        await bus.emit(rc.chat_id, "action_data:update", {"action_data": ad_payload})
        return file_rel


def _b(v: bytes | str) -> bytes:
    return v if isinstance(v, bytes) else v.encode()


def truncate_for_llm(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return text[:head] + f"\n…[{len(text) - limit} chars truncated]…\n" + text[-(limit - head):]
