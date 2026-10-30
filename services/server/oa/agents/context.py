"""Per-run runtime context shared by the orchestrator, role loops and tools."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from oa.config import settings
from oa.db.models import Run
from oa.db.session import session_scope
from oa.sandbox.client import SandboxClient

log = logging.getLogger("oa.agents")


class RunCancelled(Exception):
    pass


class Suspend(Exception):
    """Raised when the run must wait for the human (ask_human / plan_ready / questions / build decision)."""

    def __init__(self, reason: str, message_id: int | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message_id = message_id


SandboxFactory = Callable[[str], Awaitable[SandboxClient]]
PreviewResolver = Callable[[str], Awaitable[str | None]]


async def _default_sandbox(chat_id: str) -> SandboxClient:
    from oa.sandbox import manager

    return await manager.ensure_running(chat_id)


async def _default_preview(chat_id: str) -> str | None:
    from oa.sandbox import manager

    return await manager.wait_port(chat_id, 5173, timeout=60)


@dataclass
class RunContext:
    run_id: int
    chat_id: str
    round_id: int
    attempt: int
    model: str
    agent_mode: str = "lite"
    plan_mode: bool = False
    user_id: int | None = None
    state: dict[str, Any] = field(default_factory=dict)
    version_label: str | None = None
    secrets: list[str] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    sandbox_factory: SandboxFactory = _default_sandbox
    preview_resolver: PreviewResolver = _default_preview
    open_messages: set[int] = field(default_factory=set)  # streaming messages to close on cancel/failure
    _sandbox: SandboxClient | None = None
    _last_cancel_poll: float = 0.0

    async def sandbox(self) -> SandboxClient:
        self.check_cancel()
        if self._sandbox is None:
            self._sandbox = await self.sandbox_factory(self.chat_id)
        return self._sandbox

    async def preview_upstream(self) -> str | None:
        return await self.preview_resolver(self.chat_id)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise RunCancelled()

    async def poll_cancel(self) -> None:
        """Cross-process cancellation (arq workers): Redis flag, polled at most every 0.5s."""
        self.check_cancel()
        if not settings.use_redis:
            return
        now = time.monotonic()
        if now - self._last_cancel_poll < 0.5:
            return
        self._last_cancel_poll = now
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(settings.redis_url)
            try:
                if await r.get(f"oa:cancel:{self.run_id}"):
                    self.cancel_event.set()
            finally:
                await r.aclose()
        except Exception:  # pragma: no cover
            log.debug("cancel poll failed", exc_info=True)
        self.check_cancel()

    async def save_state(self) -> None:
        async with session_scope() as s:
            run = await s.get(Run, self.run_id)
            if run is not None:
                run.state = _jsonable(self.state)

    def mask(self, text: str) -> tuple[str, bool]:
        if not self.secrets or not text:
            return text, False
        try:
            from oa.domain.secrets import mask

            return mask(text, self.secrets)
        except Exception:
            hit = False
            for v in self.secrets:
                if len(v) >= 6 and v in text:
                    text = text.replace(v, "••••••")
                    hit = True
            return text, hit


def _jsonable(v: Any) -> Any:
    # Run.state goes through a JSON column; copy so SQLAlchemy sees a new object and flushes it.
    import json

    return json.loads(json.dumps(v, default=str))
