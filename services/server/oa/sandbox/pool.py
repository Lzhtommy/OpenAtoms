"""Warm sandbox pool (PLAN P8 "预热池（空闲 Pod 秒级认领）").

Problem: a sandbox's workspace is a bind mount / PVC subPath fixed when the container/pod is created, but a pool
slot is created before we know which chat will claim it.

Design (keeps storage/compute separation — the workspace always lives on the shared data volume):

    data/pool/<slot>/            ← mounted into the slot sandbox (docker: bind → /slot, k8s: PVC subPath → /slot)
    data/pool/<slot>/ws/         ← sandboxd --root (created empty when the slot boots)

  claim(chat):   rmdir  data/pool/<slot>/ws                       (empty)
                 rename data/chats/<chat>/workspace → data/pool/<slot>/ws          (same filesystem: O(1))
                 symlink data/chats/<chat>/workspace → ../../pool/<slot>/ws       (relative: valid on every mount)
  release(chat): remove symlink, rename data/pool/<slot>/ws → data/chats/<chat>/workspace, destroy slot sandbox

Platform code keeps using settings.workspace_dir(chat) — the symlink makes the move transparent (file API, git,
versions). Slots are single-use (tenant code ran in them) and are destroyed on release; the pool refills itself.

Coordination: in full mode the ready list lives in Redis (atomic LPOP claim, any api/worker process can claim) and
one process (Redis lock) maintains the pool; in lite mode it is an in-process list.
Crash safety: `recover_workspace(chat)` (called before every start/after stop) moves a workspace back from a
slot if a process died mid-claim; orphan slots are cleaned on maintainer start.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oa.config import settings
from oa.core.observability import POOL_EVENTS
from oa.sandbox.client import SandboxClient
from oa.sandbox.providers import Handle, get_provider

log = logging.getLogger("oa.sandbox.pool")

READY_KEY = "oa:pool:ready"
LEADER_KEY = "oa:pool:leader"


@dataclass
class Slot:
    slot_id: str
    token: str
    external_id: str
    endpoint: str
    ports: dict[str, str] = field(default_factory=dict)
    dev_env: dict[str, str] = field(default_factory=dict)
    created: float = 0.0

    def handle(self) -> Handle:
        return Handle(self.external_id, self.endpoint, dict(self.ports), dict(self.dev_env))


def target_size() -> int:
    raw = os.environ.get("OA_SANDBOX_WARM_POOL")
    if raw is not None:
        return max(0, int(raw))
    return 2 if settings.mode == "full" and settings.sandbox_provider in ("docker", "k8s") else 0


def pool_root() -> Path:
    return settings.data_dir / "pool"


def slot_dir(slot_id: str) -> Path:
    return pool_root() / slot_id


_local_ready: list[Slot] = []
_ready_cached = 0
_starting = 0


def ready_count_cached() -> int:
    return _ready_cached


# ---------------------------------------------------------------- storage of ready slots
async def _redis():
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _push(slot: Slot) -> None:
    global _ready_cached
    if settings.use_redis:
        r = await _redis()
        try:
            await r.rpush(READY_KEY, json.dumps(asdict(slot)))
            _ready_cached = await r.llen(READY_KEY)
        finally:
            await r.aclose()
    else:
        _local_ready.append(slot)
        _ready_cached = len(_local_ready)


async def _pop() -> Slot | None:
    global _ready_cached
    if settings.use_redis:
        r = await _redis()
        try:
            raw = await r.lpop(READY_KEY)
            _ready_cached = await r.llen(READY_KEY)
        finally:
            await r.aclose()
        return Slot(**json.loads(raw)) if raw else None
    slot = _local_ready.pop(0) if _local_ready else None
    _ready_cached = len(_local_ready)
    return slot


async def ready_count() -> int:
    if settings.use_redis:
        r = await _redis()
        try:
            return int(await r.llen(READY_KEY))
        finally:
            await r.aclose()
    return len(_local_ready)


# ---------------------------------------------------------------- slot lifecycle
def supported() -> bool:
    return hasattr(get_provider(), "start_slot")


async def _create_slot() -> Slot:
    provider = get_provider()
    slot_id = "s" + secrets.token_hex(6)
    d = slot_dir(slot_id)
    (d / "ws").mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    t0 = time.monotonic()
    handle: Handle = await provider.start_slot(slot_id, d, token)  # type: ignore[attr-defined]
    client = SandboxClient(handle.endpoint, token)
    try:
        for _ in range(150):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.health(), 1.0)
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("pool slot did not become healthy")
    finally:
        await client.aclose()
    POOL_EVENTS.labels("created").inc()
    log.info("pool slot %s ready in %.2fs", slot_id, time.monotonic() - t0)
    return Slot(slot_id, token, handle.external_id, handle.endpoint, dict(handle.ports), dict(handle.dev_env),
                time.time())


async def destroy_slot(slot_id: str, external_id: str | None) -> None:
    provider = get_provider()
    with contextlib.suppress(Exception):
        await provider.destroy_slot(slot_id, external_id)  # type: ignore[attr-defined]
    d = slot_dir(slot_id)
    if d.exists():
        await asyncio.to_thread(shutil.rmtree, d, True)


async def claim(chat_id: str) -> Slot | None:
    """Take a ready slot and move the chat workspace into it. None → caller does a cold start."""
    if target_size() <= 0 or not supported():
        return None
    ws = settings.workspace_dir(chat_id)
    if ws.is_symlink():
        await recover_workspace(chat_id)
    for _ in range(3):
        slot = await _pop()
        if slot is None:
            POOL_EVENTS.labels("miss").inc()
            return None
        client = SandboxClient(slot.endpoint, slot.token)
        try:
            await asyncio.wait_for(client.health(), 2.0)
        except Exception:
            POOL_EVENTS.labels("stale").inc()
            await destroy_slot(slot.slot_id, slot.external_id)
            continue
        finally:
            await client.aclose()
        try:
            await asyncio.to_thread(_attach, chat_id, slot.slot_id)
        except Exception:
            log.exception("attaching workspace of %s to slot %s failed", chat_id, slot.slot_id)
            await destroy_slot(slot.slot_id, slot.external_id)
            return None
        POOL_EVENTS.labels("claimed").inc()
        return slot
    return None


def _attach(chat_id: str, slot_id: str) -> None:
    ws = settings.workspace_dir(chat_id)
    target = slot_dir(slot_id) / "ws"
    # sandboxd created an empty root at boot; replace it with the real workspace
    if target.exists():
        if any(target.iterdir()):
            raise RuntimeError(f"slot {slot_id} root is not empty")
        target.rmdir()
    ws.parent.mkdir(parents=True, exist_ok=True)
    if ws.exists():
        os.rename(ws, target)
    else:
        target.mkdir()
    os.symlink(os.path.relpath(target, ws.parent), ws)


def _detach(chat_id: str) -> str | None:
    ws = settings.workspace_dir(chat_id)
    if not ws.is_symlink():
        return None
    target = Path(os.path.normpath(ws.parent / os.readlink(ws)))
    slot_id = target.parent.name
    ws.unlink()
    if target.exists():
        os.rename(target, ws)
    else:  # should not happen; never leave the chat without a workspace dir
        ws.mkdir(parents=True, exist_ok=True)
    return slot_id


async def recover_workspace(chat_id: str) -> str | None:
    """Move a chat workspace back from a pool slot (after stop, or after a crash). Returns the slot id."""
    return await asyncio.to_thread(_detach, chat_id)


async def release(chat_id: str, slot_id: str | None, external_id: str | None) -> None:
    """Called after the slot sandbox was stopped: restore the workspace and destroy the slot."""
    detached = await recover_workspace(chat_id)
    sid = slot_id or detached
    if sid:
        await destroy_slot(sid, external_id)
        POOL_EVENTS.labels("released").inc()


# ---------------------------------------------------------------- maintainer
async def _prune_ready() -> int:
    """Drop ready entries whose sandbox is gone (engine restart, node loss, manual cleanup). fill_once trusts the
    ready count, so without this dead entries would block refills until a claim happened to pop them."""
    global _ready_cached
    if settings.use_redis:
        r = await _redis()
        try:
            entries = [(raw, Slot(**json.loads(raw))) for raw in await r.lrange(READY_KEY, 0, -1)]
        finally:
            await r.aclose()
    else:
        entries = [(None, s) for s in list(_local_ready)]
    pruned = 0
    for raw, slot in entries:
        client = SandboxClient(slot.endpoint, slot.token)
        try:
            await asyncio.wait_for(client.health(), 2.0)
            continue
        except Exception:
            pass
        finally:
            await client.aclose()
        if settings.use_redis:
            r = await _redis()
            try:
                removed = await r.lrem(READY_KEY, 1, raw)
                _ready_cached = await r.llen(READY_KEY)
            finally:
                await r.aclose()
            if not removed:  # claimed by someone else meanwhile
                continue
        elif slot in _local_ready:
            _local_ready.remove(slot)
            _ready_cached = len(_local_ready)
        POOL_EVENTS.labels("stale").inc()
        log.info("pruning dead pool slot %s", slot.slot_id)
        await destroy_slot(slot.slot_id, slot.external_id)
        pruned += 1
    return pruned


async def _cleanup_orphans() -> None:
    """At maintainer start: drop slots that are neither ready nor attached to a chat."""
    root = pool_root()
    if not root.exists():
        return
    attached: set[str] = set()
    chats = settings.data_dir / "chats"
    if chats.exists():
        for ws in chats.glob("*/workspace"):
            if ws.is_symlink():
                attached.add(Path(os.readlink(ws)).parent.name)
    ready: set[str] = set()
    if settings.use_redis:
        r = await _redis()
        try:
            for raw in await r.lrange(READY_KEY, 0, -1):
                ready.add(json.loads(raw)["slot_id"])
        finally:
            await r.aclose()
    else:
        ready = {s.slot_id for s in _local_ready}
    for d in root.iterdir():
        if d.is_dir() and d.name not in attached and d.name not in ready:
            log.info("removing orphan pool slot %s", d.name)
            await destroy_slot(d.name, None)
    provider = get_provider()
    if hasattr(provider, "list_slots"):
        for sid, ext in await provider.list_slots():  # type: ignore[attr-defined]
            if sid not in attached and sid not in ready:
                await destroy_slot(sid, ext)


async def fill_once() -> int:
    """Start slots until ready + starting >= target. Returns number started."""
    global _starting
    want = target_size() - (await ready_count()) - _starting
    if want <= 0 or not supported():
        return 0
    started = 0

    async def one() -> None:
        global _starting
        nonlocal started
        _starting += 1
        try:
            slot = await _create_slot()
            await _push(slot)
            started += 1
        except Exception:
            POOL_EVENTS.labels("create_failed").inc()
            log.exception("failed to create pool slot")
        finally:
            _starting -= 1

    await asyncio.gather(*(one() for _ in range(want)))
    return started


async def maintain_loop(interval: float = 3.0) -> None:
    if target_size() <= 0:
        return
    await asyncio.sleep(1.0)
    cleaned = False
    me = secrets.token_hex(8)
    tick = 0
    while True:
        tick += 1
        try:
            leader = True
            if settings.use_redis:
                r = await _redis()
                try:
                    cur = await r.get(LEADER_KEY)
                    if cur in (None, me):
                        await r.set(LEADER_KEY, me, ex=int(interval * 5))
                        leader = True
                    else:
                        leader = False
                finally:
                    await r.aclose()
            if leader:
                if not cleaned or tick % 20 == 0:  # at startup, then every ~60 s
                    await _prune_ready()
                if not cleaned:
                    await _cleanup_orphans()
                    cleaned = True
                await fill_once()
            else:
                global _ready_cached
                _ready_cached = await ready_count()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pool maintenance failed")
        await asyncio.sleep(interval)


async def shutdown() -> None:
    """lite mode: destroy in-process ready slots (nobody else can claim them)."""
    if settings.use_redis:
        return
    while _local_ready:
        s = _local_ready.pop()
        await destroy_slot(s.slot_id, s.external_id)
