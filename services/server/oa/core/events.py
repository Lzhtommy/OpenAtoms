"""Event bus: per-chat monotonically increasing `seq`, replay buffer, fan-out to Socket.IO rooms.

lite: in-process counter + deque buffer; emitter is the in-process socketio.AsyncServer.
full: Redis INCR for seq, Redis Stream (24h) for replay; emitter is socketio.AsyncRedisManager
      (write_only in worker processes) so any process can publish.

Usage (anywhere, incl. agents):
    from oa.core.events import bus
    seq = await bus.emit(chat_id, "msg:create", {"message": {...}})
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from typing import Any, Protocol

from oa.config import settings

log = logging.getLogger("oa.events")

BUFFER_MAX = 5000
STREAM_TTL = 24 * 3600


class Emitter(Protocol):
    async def emit(self, event: str, data: Any, room: str | None = None, **kw: Any) -> Any: ...


class EventBus:
    def __init__(self) -> None:
        self._emitter: Emitter | None = None
        self._seq: dict[str, int] = defaultdict(int)
        self._buf: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=BUFFER_MAX))
        self._lock = asyncio.Lock()
        self._redis = None
        self._local_listeners: list[Any] = []  # test hooks: callables(chat_id, event, payload)

    # -- wiring
    def attach(self, emitter: Emitter) -> None:
        self._emitter = emitter

    def add_listener(self, fn: Any) -> None:
        self._local_listeners.append(fn)

    def remove_listener(self, fn: Any) -> None:
        if fn in self._local_listeners:
            self._local_listeners.remove(fn)

    async def _r(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    async def _ensure_emitter(self) -> None:
        if self._emitter is None and settings.use_redis:
            import socketio

            self._emitter = socketio.AsyncRedisManager(settings.redis_url, write_only=True)

    # -- api
    async def next_seq(self, chat_id: str) -> int:
        if settings.use_redis:
            return int(await (await self._r()).incr(f"oa:seq:{chat_id}"))
        async with self._lock:
            if self._seq[chat_id] == 0:
                self._seq[chat_id] = await _load_seq(chat_id)
            self._seq[chat_id] += 1
            return self._seq[chat_id]

    async def emit(self, chat_id: str, event: str, payload: dict[str, Any]) -> int:
        seq = await self.next_seq(chat_id)
        data = {**payload, "chat_id": chat_id, "seq": seq}
        rec = {"seq": seq, "event": event, "payload": data}
        if settings.use_redis:
            r = await self._r()
            key = f"oa:events:{chat_id}"
            await r.xadd(key, {"d": json.dumps(rec, default=str)}, maxlen=BUFFER_MAX, approximate=True)
            await r.expire(key, STREAM_TTL)
        else:
            self._buf[chat_id].append(rec)
        await self._send(event, data, f"chat:{chat_id}")
        for fn in list(self._local_listeners):
            try:
                res = fn(chat_id, event, data)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # pragma: no cover
                log.exception("listener failed")
        return seq

    async def emit_user(self, user_id: int, event: str, payload: dict[str, Any]) -> None:
        await self._send(event, {**payload, "user_id": user_id}, f"user:{user_id}")

    async def replay(self, chat_id: str, after: int) -> tuple[list[dict[str, Any]], bool]:
        """Return events with seq > after. complete=False if the buffer no longer covers `after`."""
        if settings.use_redis:
            r = await self._r()
            rows = await r.xrange(f"oa:events:{chat_id}", "-", "+")
            events = [json.loads(v["d"]) for _, v in rows]
        else:
            events = list(self._buf.get(chat_id, ()))
        out = [e for e in events if e["seq"] > after]
        if after >= await self.current_seq(chat_id):
            return out, True
        return out, bool(events) and events[0]["seq"] <= after + 1

    async def current_seq(self, chat_id: str) -> int:
        if settings.use_redis:
            v = await (await self._r()).get(f"oa:seq:{chat_id}")
            return int(v or 0)
        if self._seq[chat_id] == 0:
            self._seq[chat_id] = await _load_seq(chat_id)
        return self._seq[chat_id]

    async def _send(self, event: str, data: dict[str, Any], room: str) -> None:
        await self._ensure_emitter()
        if self._emitter is None:
            return
        try:
            await self._emitter.emit(event, data, room=room)
        except Exception:  # pragma: no cover
            log.exception("socket emit failed: %s", event)


async def _load_seq(chat_id: str) -> int:
    # lite mode: survive restarts by persisting high-water mark lazily (see persist_seq)
    from sqlalchemy import select

    from oa.db.models import Chat
    from oa.db.session import SessionLocal

    async with SessionLocal() as s:
        v = (await s.execute(select(Chat.seq).where(Chat.id == chat_id))).scalar_one_or_none()
    # jump ahead so clients with a stale last_seq never see duplicates after a restart
    return int(v or 0) + 1000 if v else 0


async def persist_seq(chat_id: str) -> None:
    """Called at end of runs in lite mode so seq survives process restarts."""
    if settings.use_redis:
        return
    from sqlalchemy import update

    from oa.db.models import Chat
    from oa.db.session import SessionLocal

    async with SessionLocal() as s:
        await s.execute(update(Chat).where(Chat.id == chat_id).values(seq=bus._seq.get(chat_id, 0)))
        await s.commit()


bus = EventBus()
