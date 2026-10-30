"""Versions: git commit + tag vN → build → upload dist to storage deploys/{chat}/{vN}/ (ARCHITECTURE §10)."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from collections import defaultdict
from pathlib import Path

from sqlalchemy import func, select

from oa.config import settings
from oa.core.events import bus
from oa.core.storage import get_storage
from oa.db import models as m
from oa.db.session import SessionLocal
from oa.domain import gitops, hooks
from oa.domain import serializers as ser
from oa.domain.messages import add_message

log = logging.getLogger("oa.versions")

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
SKIP_DIRS = {".git", "node_modules", ".storage", ".atoms", ".venv", "__pycache__"}
BUILD_TIMEOUT = 600


class BuildResult:
    def __init__(self, ok: bool, log_text: str, dist: Path | None) -> None:
        self.ok, self.log, self.dist = ok, log_text, dist


def _has_build_script(ws: Path) -> bool:
    try:
        return "build" in json.loads((ws / "package.json").read_text()).get("scripts", {})
    except (OSError, ValueError):
        return False


async def _host_shell(ws: Path, cmd: str, timeout: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", cmd, cwd=str(ws), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "CI": "1"}, start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, f"`{cmd}` timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace")


async def build_workspace(chat_id: str, ws: Path) -> BuildResult:
    """Run the project build. Prefers the running sandbox (same toolchain the agent used)."""
    if _has_build_script(ws):
        mgr = hooks.manager()
        client = None
        if mgr is not None:
            client = await mgr.get_client(chat_id)
            if client is None and settings.sandbox_provider != "local":
                client = await mgr.ensure_running(chat_id)
        if client is not None:
            code, out = await client.run("pnpm build", timeout_ms=BUILD_TIMEOUT * 1000)
        else:
            code, out = 0, ""
            if not (ws / "node_modules").exists():
                code, out = await _host_shell(ws, "pnpm install --prefer-offline", BUILD_TIMEOUT)
            if code == 0:
                code, out2 = await _host_shell(ws, "pnpm build", BUILD_TIMEOUT)
                out += out2
        if code != 0:
            return BuildResult(False, out, None)
        for name in ("dist", "build", "out"):
            if (ws / name / "index.html").is_file():
                return BuildResult(True, out, ws / name)
        return BuildResult(False, out + "\nBuild finished but no dist/index.html was produced.", None)
    if (ws / "index.html").is_file():
        return BuildResult(True, "static site (no build step)", ws)
    return BuildResult(False, "Nothing to build: no package.json build script and no index.html", None)


async def upload_dir(src: Path, prefix: str) -> int:
    storage = get_storage()
    await storage.delete_prefix(prefix)
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            p = Path(root) / f
            rel = p.relative_to(src).as_posix()
            await storage.put_bytes(f"{prefix}/{rel}", p.read_bytes(), mimetypes.guess_type(f)[0])
            n += 1
    return n


async def _emit_version(chat_id: str, event: str, v: m.Version) -> None:
    await bus.emit(chat_id, event, {"version": ser.version(v)})


async def create_version(chat_id: str, summary: str, run_id: int | None = None) -> m.Version:
    async with _locks[chat_id]:
        return await _create_version(chat_id, summary, run_id)


async def _create_version(chat_id: str, summary: str, run_id: int | None) -> m.Version:
    ws = settings.workspace_dir(chat_id)
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None:
            raise ValueError("chat not found")
        n = int((await s.execute(select(func.max(m.Version.number)).where(m.Version.chat_id == chat_id))).scalar() or 0) + 1
        v = m.Version(chat_id=chat_id, version=f"v{n}", number=n, summary=(summary or f"Version {n}")[:2000],
                      status="building")
        s.add(v)
        await s.commit()
        await _emit_version(chat_id, "version:create", v)

        try:
            v.commit = await gitops.commit_all(ws, f"{v.version}: {v.summary.splitlines()[0][:200]}")
            await gitops.tag(ws, v.version)
        except gitops.GitError as e:
            log.warning("git commit failed for %s: %s", chat_id, e)

        await bus.emit(chat_id, "build:update", {"status": "BUILDING", "version": v.version})
        try:
            res = await build_workspace(chat_id, ws)
        except Exception as e:
            log.exception("build crashed")
            res = BuildResult(False, str(e), None)
        if res.ok and res.dist is not None:
            await upload_dir(res.dist, f"deploys/{chat_id}/{v.version}")
            from oa.domain.covers import make_cover

            v.cover = await make_cover(chat_id, v.version, res.dist)
            v.status = "ready"
            v.link = settings.version_url(chat.user_id, chat_id, v.version, 0)
            chat.current_version = v.version
            await bus.emit(chat_id, "build:complete", {"status": "SUCCESS", "version": v.version})
        else:
            v.status = "failed"
            await bus.emit(chat_id, "build:error", {"status": "FAILURE", "version": v.version, "log": res.log[-4000:]})
        await s.commit()
        await _emit_version(chat_id, "version:update" if v.status == "failed" else "version:complete", v)

        round_id, attempt = 0, 0
        if run_id:
            run = await s.get(m.Run, run_id)
            if run:
                round_id, attempt = run.round_id, run.attempt
        await add_message(s, chat_id, role="System", type="version", round_id=round_id, attempt=attempt,
                          content={"version": v.version, "summary": v.summary, "link": v.link, "status": v.status})
        await bus.emit(chat_id, "chat:update", {"chat": {"version": {"version": v.version, "summary": v.summary,
                                                                     "cover": v.cover, "link": v.link}}})
        if v.status == "ready":
            dep = await s.get(m.Deployment, chat_id)
            if dep is not None and dep.is_always_latest:
                from oa.domain.deployments import point_to

                await point_to(s, chat, dep, v.version)
        await s.refresh(v)
        return v


async def restore_version(chat_id: str, version: str, user_id: int) -> m.Version:
    ws = settings.workspace_dir(chat_id)
    async with SessionLocal() as s:
        v = (await s.execute(select(m.Version).where(m.Version.chat_id == chat_id, m.Version.version == version))).scalar_one_or_none()
        if v is None:
            raise LookupError(version)
        await gitops.checkout_tree(ws, version)
        user = await s.get(m.User, user_id)
        await add_message(s, chat_id, role="System", type="restore",
                          content={"version": version, "message": f"Restored to {version}",
                                   "user": user.name if user else ""})
    return await create_version(chat_id, f"Restore to {version}")
