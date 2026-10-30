"""Message persistence + streaming emission (CONTRACT §2 msg:create / msg:update)."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from oa.core.events import bus
from oa.db.models import Message
from oa.db.session import session_scope
from oa.domain import serializers as ser

COALESCE_SECONDS = 0.05


class Coalescer:
    """Batches pushes and flushes at most every `interval` seconds, preserving order."""

    def __init__(self, flush: Callable[[list[Any]], Awaitable[None]], interval: float = COALESCE_SECONDS) -> None:
        self._flush_fn = flush
        self._interval = interval
        self._buf: list[Any] = []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def push(self, item: Any) -> None:
        self._buf.append(item)
        if self._task is None:
            self._task = asyncio.create_task(self._later())

    async def _later(self) -> None:
        await asyncio.sleep(self._interval)
        self._task = None
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buf:
                return
            items, self._buf = self._buf, []
            await self._flush_fn(items)

    async def close(self) -> None:
        t, self._task = self._task, None
        if t is not None:
            t.cancel()  # only ever cancelled while sleeping (see _later), so no items are lost
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await self.flush()


def delta_text(content: Any) -> str:
    """Plain text of a Delta (or a string)."""
    if isinstance(content, str):
        return content
    out = []
    for op in content or []:
        ins = op.get("insert") if isinstance(op, dict) else None
        if isinstance(ins, str):
            out.append(ins)
        elif isinstance(ins, dict) and "mentiontrigger" in ins:
            out.append("@" + str(ins["mentiontrigger"].get("value", "")))
        elif isinstance(ins, dict) and "attachment" in ins:
            out.append(f"[attachment: {ins['attachment'].get('name', '')}]\n")
    return "".join(out)


async def post_message(
    chat_id: str,
    *,
    role: str,
    name: str,
    type: str,
    content: Any,
    round_id: int,
    attempt: int,
    refer_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    is_finished: bool = True,
    version: str | None = None,
) -> Message:
    async with session_scope() as s:
        m = Message(
            chat_id=chat_id, role=role, name=name, type=type, content=content, round_id=round_id, attempt=attempt,
            refer_id=refer_id, metadata_=metadata or {}, is_finished=is_finished, version=version,
        )
        s.add(m)
        await s.flush()
        await s.refresh(m)
        payload = ser.message(m)
    await bus.emit(chat_id, "msg:create", {"message": payload})
    return m


async def update_message(
    chat_id: str,
    msg_id: int,
    *,
    content: Any = None,
    metadata: dict[str, Any] | None = None,
    is_finished: bool | None = None,
    merge_metadata: bool = True,
) -> None:
    async with session_scope() as s:
        m = await s.get(Message, msg_id)
        if m is None:
            return
        if content is not None:
            m.content = content
        if metadata is not None:
            m.metadata_ = {**(m.metadata_ or {}), **metadata} if merge_metadata else metadata
        if is_finished is not None:
            m.is_finished = is_finished
        meta = m.metadata_
    ev: dict[str, Any] = {"id": msg_id}
    if content is not None:
        ev["content"] = content
    if metadata is not None:
        ev["metadata"] = meta
    if is_finished is not None:
        ev["is_finished"] = is_finished
    await bus.emit(chat_id, "msg:update", ev)


class StreamingMessage:
    """A message (usually type=think) whose Delta content grows while the model streams.

    The row is created lazily on the first text/action so tool-only steps without visible output
    (e.g. a bare reply_to_human) do not leave empty think bubbles.
    """

    def __init__(self, chat_id: str, *, role: str, name: str, round_id: int, attempt: int, type: str = "think",
                 metadata: dict[str, Any] | None = None, on_open: Callable[[int], None] | None = None) -> None:
        self.chat_id = chat_id
        self.role, self.name, self.type = role, name, type
        self.round_id, self.attempt = round_id, attempt
        self.metadata = metadata
        self.id: int | None = None
        self.uuid: str | None = None
        self.content: list[dict[str, Any]] = []
        self.finished = False
        self._on_open = on_open
        self._open_lock = asyncio.Lock()
        self._text = Coalescer(self._flush_text)

    async def ensure(self) -> None:
        if self.id is not None:
            return
        async with self._open_lock:
            if self.id is not None:
                return
            m = await post_message(self.chat_id, role=self.role, name=self.name, type=self.type, content=[],
                                   round_id=self.round_id, attempt=self.attempt, is_finished=False,
                                   metadata=self.metadata)
            self.id, self.uuid = m.id, m.uuid
            if self._on_open:
                self._on_open(m.id)

    @property
    def text(self) -> str:
        return delta_text(self.content)

    async def _flush_text(self, parts: list[str]) -> None:
        await bus.emit(self.chat_id, "msg:update", {"id": self.id, "delta": [{"insert": "".join(parts)}]})

    async def append_text(self, text: str) -> None:
        if not text:
            return
        await self.ensure()
        if self.content and isinstance(self.content[-1].get("insert"), str) and len(self.content[-1]) == 1:
            self.content[-1] = {"insert": self.content[-1]["insert"] + text}
        else:
            self.content.append({"insert": text})
        await self._text.push(text)

    async def add_action(self, uuid: str) -> None:
        await self.ensure()
        await self._text.flush()
        op = {"action_data": {"uuid": uuid}}
        self.content.append(op)
        await bus.emit(self.chat_id, "msg:update", {"id": self.id, "delta": [op]})

    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        await self._text.close()
        if self.id is not None:
            await update_message(self.chat_id, self.id, content=self.content, is_finished=True)
