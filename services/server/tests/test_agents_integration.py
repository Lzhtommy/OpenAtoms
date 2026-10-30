"""End-to-end: real LocalProvider (sandboxd on the host) + template + pnpm, driven by oa-mock in lite mode.

Skipped when the sandboxd binary, the template or pnpm is unavailable, or OA_SKIP_INTEGRATION is set.
"""
from __future__ import annotations

import json
import os
import shutil

import httpx
import pytest
from sqlalchemy import select
from test_agents_runtime import action_datas, events, make_chat, messages, send, timelines, wait_run  # noqa: F401

from oa.agents import runner
from oa.config import settings
from oa.db import models as m
from oa.db.session import session_scope
from oa.sandbox import manager

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("OA_SKIP_INTEGRATION"))
    or not settings.sandboxd_bin.exists()
    or not (settings.template_dir / "package.json").exists()
    or shutil.which("pnpm") is None,
    reason="sandboxd binary / template / pnpm not available",
)


async def test_real_lite_round_local_provider(client, register, events, monkeypatch):  # noqa: F811
    monkeypatch.setattr(runner, "SANDBOX_FACTORY", None)
    monkeypatch.setattr(runner, "PREVIEW_RESOLVER", None)
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    try:
        await send(chat_id, uid, "Make a counter app")
        run = await wait_run(chat_id, timeout=600)
        assert run.status == "success", run.error

        ws = settings.workspace_dir(chat_id)
        assert (ws / ".git").exists() and (ws / "node_modules").exists()
        assert "Undo" in (ws / "src/components/Counter.tsx").read_text()
        assert "A simple, reliable tally" in (ws / "src/App.tsx").read_text()
        assert "Make a counter app" in (ws / ".atoms/memory.md").read_text()

        ads = await action_datas(chat_id)
        build = [a for a in ads if a.command_name == "terminal_run"]
        assert build and all(a.status == "success" for a in build), [a.extra for a in build]
        tls = await timelines(chat_id)
        check = next(t for t in tls if t.block_type == "Browser-RT")
        doc = json.loads(check.content)
        assert doc["errors"] == [] and doc["status"] == 200, doc
        term = next(t for t in tls if t.block_type == "Terminal")
        assert term.content.startswith("$ pnpm build") and "built in" in term.content

        # the dev server answers through the recorded upstream
        up = await manager.resolve_port(chat_id, 5173)
        assert up
        async with httpx.AsyncClient() as c:
            r = await c.get(up + "/src/components/Counter.tsx")
        assert r.status_code == 200

        # version created by the platform after a successful run
        async with session_scope() as s:
            versions = (await s.execute(select(m.Version).where(m.Version.chat_id == chat_id))).scalars().all()
            chat = await s.get(m.Chat, chat_id)
        assert [v.version for v in versions] == ["v1"] and versions[0].status == "ready"
        assert chat.runtime_status == "running" and not chat.is_running
        assert all(x.is_finished for x in await messages(chat_id))

        evs = [e for c, e, _ in events if c == chat_id]
        assert "version:complete" in evs
        statuses = [p["chat"]["metadata"]["status"] for c, e, p in events
                    if c == chat_id and e == "chat:update" and "metadata" in p.get("chat", {})]
        assert "activating" in statuses and "running" in statuses
    finally:
        await manager.stop(chat_id)
    assert await manager.status(chat_id) == "stopped"
    assert await manager.resolve_port(chat_id, 5173) is None


async def test_reaper_stops_idle(client, register, monkeypatch):
    chat_id, _ = await make_chat(client, register, agent_mode="lite")
    await manager.ensure_running(chat_id)
    try:
        assert await manager.status(chat_id) == "running"
        monkeypatch.setattr(settings, "sandbox_idle_seconds", 0)
        reaped = await manager.reap_idle_once()
        assert chat_id in reaped
        assert await manager.status(chat_id) == "stopped"
        # wake brings it back; files persist (storage/compute separation)
        client2 = await manager.ensure_running(chat_id)
        assert (await client2.read("package.json"))
    finally:
        await manager.stop(chat_id)


def _docker_image_present() -> bool:
    try:
        import docker

        docker.from_env().images.get(settings.sandbox_image)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_image_present(), reason="sandbox docker image not built")
async def test_docker_provider_lifecycle(client, register, monkeypatch):
    from oa.sandbox import providers

    monkeypatch.setattr(settings, "sandbox_provider", "docker")
    providers.set_provider(None)
    chat_id, _ = await make_chat(client, register, agent_mode="lite")
    try:
        sb = await manager.ensure_running(chat_id)
        assert json.loads(await sb.read("package.json"))["scripts"]["build"]
        code, out = await sb.run("node --version && pnpm --version && whoami")
        assert code == 0, out
        up = await manager.wait_port(chat_id, 5173, timeout=120)
        assert up
        async with httpx.AsyncClient() as c:
            assert (await c.get(up + "/")).status_code == 200
        code, out = await sb.run("pnpm build", timeout_ms=300_000)
        assert code == 0, out
    finally:
        await manager.destroy(chat_id)
        providers.set_provider(None)
