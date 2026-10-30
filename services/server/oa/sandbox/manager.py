"""Sandbox lifecycle (CONTRACT §10.1, ARCHITECTURE §8.2).

stopped ──wake──▶ activating ──ready──▶ running ──idle──▶ stopping ──▶ stopped
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets as _secrets
import shutil
import socket
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select

from oa.config import settings
from oa.core.events import bus
from oa.core.observability import SANDBOX_START, SANDBOX_START_FAILURES
from oa.db.models import Chat, Sandbox
from oa.db.session import session_scope
from oa.sandbox.client import SandboxClient, SandboxError
from oa.sandbox.providers import DEV_PORT, Handle, get_provider, sanitized_host_env

log = logging.getLogger("oa.sandbox")

HEALTH_TIMEOUT = 30.0
INSTALL_TIMEOUT_MS = 15 * 60 * 1000


class SandboxFull(Exception):
    """Workspace reached settings.sandbox_max_running_per_workspace (error code 4122)."""


_locks: dict[str, asyncio.Lock] = {}
_clients: dict[str, SandboxClient] = {}
_wake_tasks: dict[str, asyncio.Task] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@contextlib.asynccontextmanager
async def _chat_lock(chat_id: str) -> AsyncIterator[None]:
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        if settings.use_redis:
            import redis.asyncio as aioredis

            r = aioredis.from_url(settings.redis_url)
            try:
                async with r.lock(f"oa:sbxlock:{chat_id}", timeout=INSTALL_TIMEOUT_MS / 1000 + 60, blocking_timeout=900):
                    yield
            finally:
                await r.aclose()
        else:
            yield


# ---------------------------------------------------------------- status bookkeeping
async def _set_status(chat_id: str, status: str, **fields: object) -> None:
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        if sb is None:
            sb = Sandbox(chat_id=chat_id, provider=settings.sandbox_provider, status=status)
            s.add(sb)
        sb.status = status
        for k, v in fields.items():
            setattr(sb, k, v)
        chat = await s.get(Chat, chat_id)
        if chat is not None:
            chat.runtime_status = status
            is_running = chat.is_running
        else:
            is_running = False
    await bus.emit(
        chat_id,
        "chat:update",
        {"chat": {"chat_id": chat_id, "metadata": {"status": status, "is_running": is_running, "preview_port": DEV_PORT}}},
    )


def _handle(sb: Sandbox) -> Handle:
    return Handle(external_id=sb.external_id or "", endpoint=sb.endpoint or "", ports=dict(sb.ports or {}))


async def _healthy(client: SandboxClient, timeout: float = 2.0) -> bool:
    try:
        await asyncio.wait_for(client.health(), timeout)
        return True
    except Exception:
        return False


async def _wait_healthy(client: SandboxClient, deadline: float = HEALTH_TIMEOUT) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        if await _healthy(client, 1.0):
            return
        await asyncio.sleep(0.2)
    raise SandboxError("sandboxd did not become healthy in time")


# ---------------------------------------------------------------- workspace
def _git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=OpenAtoms", "-c", "user.email=bot@openatoms.local", *args],
        cwd=ws, capture_output=True, text=True, env=sanitized_host_env(),
    )


DEFAULT_TEMPLATE = "react-shadcn-vite"


def template_path(name: str | None) -> Path:
    """Project template directory for `config.template_name`; the default/unknown names map to
    settings.template_dir (resolved at call time so deployments/tests can point it elsewhere)."""
    default = settings.template_dir
    if not name or name in (DEFAULT_TEMPLATE, default.name) or "/" in name or ".." in name:
        return default
    candidate = default.parent / name
    return candidate if (candidate / "package.json").exists() else default


def _init_workspace_sync(chat_id: str, from_chat: str | None, version: str | None,
                         template: str | None = None) -> None:
    ws = settings.workspace_dir(chat_id)
    if (ws / ".git").exists() or (ws / "package.json").exists():
        return
    ws.parent.mkdir(parents=True, exist_ok=True)
    src: Path | None = None
    if from_chat:
        src = settings.workspace_dir(from_chat)
        if not src.exists():
            raise FileNotFoundError(f"source workspace {from_chat} missing")
        if version:
            # export the tagged tree without touching the source workspace
            tmp = ws.parent / "_export"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            r = subprocess.run(
                f"git archive --format=tar {version} | tar -x -C '{tmp}'", shell=True, cwd=src,
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                log.warning("git archive %s failed (%s); copying working tree", version, r.stderr.strip())
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                src = tmp
    else:
        tpl = template_path(template)
        src = tpl if tpl.exists() else None

    if src is not None:
        shutil.copytree(src, ws, ignore=_ignore_all, dirs_exist_ok=True)
        if src.name == "_export":
            shutil.rmtree(src, ignore_errors=True)
    else:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "README.md").write_text("# New project\n")

    mem = ws / ".atoms" / "memory.md"
    mem.parent.mkdir(parents=True, exist_ok=True)
    if not mem.exists() or from_chat:
        origin = f"Forked from project {from_chat}{' @ ' + version if version else ''}.\n" if from_chat else ""
        stack = "Stack: React + Vite + TypeScript + Tailwind (shadcn-style).\n\n"
        tpl_mem = template_path(template) / ".atoms" / "memory.md"
        if not from_chat and tpl_mem.exists():  # template-specific memory (e.g. Expo / React Native)
            stack = tpl_mem.read_text().split("\n", 2)[-1].strip() + "\n\n"
        mem.write_text(f"# Project memory\n\n{origin}{stack}")
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text("node_modules\ndist\n.vite\n*.tsbuildinfo\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "init" if not from_chat else f"fork of {from_chat}")


def _ignore_all(dirpath: str, names: list[str]) -> set[str]:
    skip = {"node_modules", "dist", ".pnpm-store", ".vite", ".git"}
    return {n for n in names if n in skip or n.endswith(".tsbuildinfo")}


async def init_workspace(chat_id: str, from_chat: str | None = None, version: str | None = None) -> None:
    template = None
    if not from_chat:
        async with session_scope() as s:
            chat = await s.get(Chat, chat_id)
            template = ((chat.config or {}).get("template_name") if chat else None) or None
    await asyncio.to_thread(_init_workspace_sync, chat_id, from_chat, version, template)


# ---------------------------------------------------------------- lifecycle
async def _secrets_env(chat_id: str) -> dict[str, str]:
    try:
        from oa.domain.secrets import secrets_env

        return await secrets_env(chat_id, "dev")
    except Exception as e:  # pragma: no cover - module owned by core workstream
        log.debug("secrets_env unavailable: %s", e)
        return {}


def _has_dev_script(ws: Path) -> bool:
    try:
        return "dev" in json.loads((ws / "package.json").read_text()).get("scripts", {})
    except Exception:
        return False


async def _check_quota(chat_id: str) -> None:
    async with session_scope() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None:
            raise SandboxError(f"chat {chat_id} not found")
        n = (
            await s.execute(
                select(func.count())
                .select_from(Sandbox)
                .join(Chat, Chat.id == Sandbox.chat_id)
                .where(
                    Chat.workspace_id == chat.workspace_id,
                    Sandbox.status.in_(("running", "activating")),
                    Sandbox.chat_id != chat_id,
                )
            )
        ).scalar_one()
    limit = settings.sandbox_max_running_per_workspace
    try:
        from oa.domain.quotas import max_sandboxes

        limit = await max_sandboxes(chat.workspace_id)
    except Exception:
        log.debug("plan limits unavailable", exc_info=True)
    if n >= limit:
        raise SandboxFull(f"workspace already runs {n}/{limit} sandboxes allowed by its plan")


_cap_lock = asyncio.Lock()


@contextlib.asynccontextmanager
async def _capacity_slot(chat_id: str) -> AsyncIterator[None]:
    """Global admission control (OA_SANDBOX_MAX_TOTAL, 0 = unlimited): if the deployment already runs that many
    sandboxes, wait (up to OA_SANDBOX_CAPACITY_WAIT seconds, default 600) for one to stop instead of starting
    another. The load test showed 20 concurrent sandboxes OOM-killing processes on an 8 GB Docker VM.
    The check + the transition to `activating` happen under one lock (Redis lock across processes in full mode)."""
    import os

    cap = int(os.environ.get("OA_SANDBOX_MAX_TOTAL", "0"))
    if cap <= 0:
        yield
        return
    deadline = asyncio.get_running_loop().time() + float(os.environ.get("OA_SANDBOX_CAPACITY_WAIT", "600"))
    waited = False
    while True:
        async with _cap_lock:
            if settings.use_redis:
                import redis.asyncio as aioredis

                r = aioredis.from_url(settings.redis_url)
                lock = r.lock("oa:sbxcap", timeout=30, blocking_timeout=30)
                got = await lock.acquire()
            else:
                r = lock = None
                got = True
            try:
                async with session_scope() as s:
                    n = (await s.execute(select(func.count()).select_from(Sandbox).where(
                        Sandbox.status.in_(("running", "activating")), Sandbox.chat_id != chat_id))).scalar_one()
                if got and n < cap:
                    if waited:
                        log.info("capacity available for %s after waiting", chat_id)
                    yield
                    return
            finally:
                if lock is not None and got:
                    with contextlib.suppress(Exception):
                        await lock.release()
                if r is not None:
                    await r.aclose()
        if asyncio.get_running_loop().time() > deadline:
            raise SandboxFull(f"no sandbox capacity: {n}/{cap} running")
        # (status is not set to `activating` while queued: that would count against the cap itself)
        waited = True
        await asyncio.sleep(2.0)


async def _start_dev(chat_id: str, client: SandboxClient, dev_env: dict[str, str]) -> None:
    ws = settings.workspace_dir(chat_id)
    if not _has_dev_script(ws):
        return
    env = {**(await _secrets_env(chat_id)), **dev_env}
    await client.process_ensure("dev", "pnpm dev", env=env)


async def _install_deps(client: SandboxClient) -> None:
    code, out = await client.run(
        "pnpm install --prefer-offline --reporter=append-only 2>&1 | tail -n 40", timeout_ms=INSTALL_TIMEOUT_MS
    )
    if code != 0:
        log.warning("pnpm install failed: %s", out[-2000:])


async def ensure_running(chat_id: str) -> SandboxClient:
    cached = _clients.get(chat_id)
    if cached is not None and await _healthy(cached, 1.5):
        return cached
    async with _chat_lock(chat_id):
        async with session_scope() as s:
            sb = await s.get(Sandbox, chat_id)
            prev = (sb.status, _handle(sb), sb.token) if sb else None
        provider = get_provider()
        if prev and prev[0] == "running" and prev[1].endpoint:
            client = SandboxClient(prev[1].endpoint, prev[2])
            if await provider.alive(prev[1]) and await _healthy(client):
                _clients[chat_id] = client
                return client
            await client.aclose()

        await _check_quota(chat_id)
        async with _capacity_slot(chat_id):  # node/cluster admission: wait instead of overcommitting memory
            await _set_status(chat_id, "activating")
        t0 = asyncio.get_running_loop().time()
        kind = "cold"
        try:
            from oa.sandbox import pool

            # a previous slot-backed sandbox died without a clean stop: bring the workspace home first
            if prev and (prev[1].ports or {}).get("_slot"):
                await pool.release(chat_id, str(prev[1].ports["_slot"]), prev[1].external_id)
            else:
                await pool.recover_workspace(chat_id)
            await init_workspace(chat_id)
            ws = settings.workspace_dir(chat_id)
            slot = await pool.claim(chat_id)
            if slot is not None:
                kind = "warm"
                handle, token = slot.handle(), slot.token
            else:
                token = _secrets.token_hex(16)
                if prev and prev[1].external_id and provider.name == "local":
                    # a stale host process may survive an api restart; never run two sandboxd per workspace
                    with contextlib.suppress(Exception):
                        await provider.destroy(chat_id, prev[1])
                handle = await provider.start(chat_id, ws, token, prev[1] if prev else None)
            client = SandboxClient(handle.endpoint, token)
            await _wait_healthy(client)
            if (ws / "package.json").exists() and not (ws / "node_modules").exists():
                await _install_deps(client)
            await _start_dev(chat_id, client, handle.dev_env)
            ports = dict(handle.ports)
            ports["_dev_env"] = handle.dev_env  # type: ignore[assignment]
            if slot is not None:
                ports["_slot"] = slot.slot_id
            await _set_status(
                chat_id, "running", provider=provider.name, external_id=handle.external_id,
                endpoint=handle.endpoint, token=token, ports=ports, last_heartbeat=_utcnow(),
            )
            SANDBOX_START.labels(provider.name, kind).observe(asyncio.get_running_loop().time() - t0)
        except SandboxFull:
            raise
        except Exception:
            log.exception("sandbox start failed for %s", chat_id)
            SANDBOX_START_FAILURES.labels(provider.name).inc()
            await _set_status(chat_id, "failed")
            raise
        _clients[chat_id] = client
        return client


async def get_client(chat_id: str) -> SandboxClient | None:
    cached = _clients.get(chat_id)
    if cached is not None:
        return cached
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        if sb is None or sb.status != "running" or not sb.endpoint:
            return None
        client = SandboxClient(sb.endpoint, sb.token)
    if await _healthy(client):
        _clients[chat_id] = client
        return client
    await client.aclose()
    return None


async def restart_dev(chat_id: str) -> None:
    """Restart the dev server (e.g. after secrets changed)."""
    client = await get_client(chat_id)
    if client is None:
        return
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        dev_env = dict((sb.ports or {}).get("_dev_env") or {}) if sb else {}
    with contextlib.suppress(Exception):
        await client.process_stop("dev")
    await _start_dev(chat_id, client, dev_env)


async def stop(chat_id: str) -> None:
    async with _chat_lock(chat_id):
        async with session_scope() as s:
            sb = await s.get(Sandbox, chat_id)
            if sb is None or sb.status in ("stopped",):
                return
            handle = _handle(sb)
        await _set_status(chat_id, "stopping")
        client = _clients.pop(chat_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.process_stop("dev")
            await client.aclose()
        try:
            slot_id = (handle.ports or {}).get("_slot")
            if slot_id:
                from oa.sandbox import pool

                await pool.release(chat_id, str(slot_id), handle.external_id)
            else:
                await get_provider().stop(chat_id, handle)
        finally:
            await _set_status(chat_id, "stopped", endpoint=None)


async def status(chat_id: str) -> str:
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        return sb.status if sb else "stopped"


def _tcp_open(url: str, timeout: float = 0.5) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


async def resolve_port(chat_id: str, port: int) -> str | None:
    """Upstream URL for a logical in-sandbox port, only if something is actually listening.

    Readiness is probed from inside the sandbox (sandboxd /ports): host-side port proxies (Docker Desktop)
    accept TCP connections even when nothing listens in the container.
    """
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        if sb is None or sb.status != "running":
            return None
        ports = dict(sb.ports or {})
        endpoint, token = sb.endpoint, sb.token
    upstream = ports.get(str(port))
    if not isinstance(upstream, str):
        return None
    dev_env = ports.get("_dev_env") or {}
    internal = int(dev_env.get("PORT", port)) if port == DEV_PORT else port
    client = _clients.get(chat_id)
    if client is None and endpoint:
        client = SandboxClient(endpoint, token)
        _clients[chat_id] = client
    if client is not None:
        try:
            return upstream if internal in await asyncio.wait_for(client.ports([internal]), 3) else None
        except Exception:
            log.debug("ports probe failed for %s", chat_id, exc_info=True)
    return upstream if await asyncio.to_thread(_tcp_open, upstream) else None


async def wait_port(chat_id: str, port: int = DEV_PORT, timeout: float = 60.0) -> str | None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        up = await resolve_port(chat_id, port)
        if up:
            return up
        await asyncio.sleep(0.5)
    return None


async def wake(chat_id: str) -> bool:
    """Start the sandbox in the background. False when it is deliberately kept down (boost in progress)."""
    t = _wake_tasks.get(chat_id)
    if t is not None and not t.done():
        return True
    from oa.domain import boost

    if await boost.is_building(chat_id):
        return False

    async def _run() -> None:
        try:
            await ensure_running(chat_id)
        except Exception as e:
            log.warning("wake %s failed: %s", chat_id, e)
        finally:
            _wake_tasks.pop(chat_id, None)

    _wake_tasks[chat_id] = asyncio.create_task(_run())
    return True


async def heartbeat(chat_id: str) -> None:
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        if sb is not None:
            sb.last_heartbeat = _utcnow()


async def destroy(chat_id: str) -> None:
    """Remove the sandbox entirely (chat deletion). Workspace files are left to the caller."""
    with contextlib.suppress(Exception):
        await stop(chat_id)
    async with session_scope() as s:
        sb = await s.get(Sandbox, chat_id)
        handle = _handle(sb) if sb else None
        if sb is not None:
            await s.delete(sb)
    with contextlib.suppress(Exception):
        if handle is not None and (handle.ports or {}).get("_slot"):
            from oa.sandbox import pool

            await pool.release(chat_id, str(handle.ports["_slot"]), handle.external_id)
        else:
            await get_provider().destroy(chat_id, handle)


async def reap_idle_once() -> list[str]:
    cutoff = _utcnow() - timedelta(seconds=settings.sandbox_idle_seconds)
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Sandbox.chat_id, Sandbox.last_heartbeat, Chat.is_running)
                .join(Chat, Chat.id == Sandbox.chat_id)
                .where(Sandbox.status == "running")
            )
        ).all()
    victims = [cid for cid, hb, running in rows if not running and (_aware(hb) or cutoff) <= cutoff]
    for cid in victims:
        log.info("reaping idle sandbox %s", cid)
        with contextlib.suppress(Exception):
            await stop(cid)
    return victims


async def reaper_loop(interval: float = 60.0) -> None:
    while True:
        try:
            await reap_idle_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reaper iteration failed")
        await asyncio.sleep(interval)
