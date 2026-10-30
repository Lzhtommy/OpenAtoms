"""Real-browser verification (runs only when Playwright + Chromium are installed).

1. Lite round on the real LocalProvider where Alex ships a component that throws in useEffect:
   checkui_run (headless Chromium against the live Vite preview) must catch the uncaught error, Alex repairs
   it, the re-check is clean, and the version gets a rendered cover served by the signed cover endpoint.
2. Team round (fake sandbox): David's browser_goto renders a real page and stores a real PNG screenshot.
"""
from __future__ import annotations

import json
import os
import shutil

import httpx
import pytest
from sqlalchemy import select
from test_agents_runtime import fake_sandbox, make_chat, send, timelines, wait_run  # noqa: F401

from oa.agents import runner
from oa.config import settings
from oa.core.storage import get_storage
from oa.db import models as m
from oa.db.session import session_scope
from oa.sandbox import manager
from oa.tools.web import playwright_available

PNG = b"\x89PNG\r\n\x1a\n"


def _chromium_ok() -> bool:
    if not playwright_available():
        return False
    import asyncio

    from playwright.async_api import async_playwright

    async def probe() -> bool:
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True)
                await b.close()
            return True
        except Exception:
            return False

    return asyncio.new_event_loop().run_until_complete(probe())


pytestmark = pytest.mark.skipif(not _chromium_ok(), reason="Playwright/Chromium not installed")

needs_sandbox = pytest.mark.skipif(
    bool(os.environ.get("OA_SKIP_INTEGRATION")) or not settings.sandboxd_bin.exists()
    or not (settings.template_dir / "package.json").exists() or shutil.which("pnpm") is None,
    reason="sandboxd / template / pnpm not available",
)


@needs_sandbox
async def test_checkui_catches_runtime_error_and_alex_repairs(client, register, monkeypatch):
    monkeypatch.setattr(runner, "SANDBOX_FACTORY", None)
    monkeypatch.setattr(runner, "PREVIEW_RESOLVER", None)
    chat_id, uid = await make_chat(client, register, agent_mode="lite")
    try:
        await send(chat_id, uid, "Build a todo list app (runtime error drill)")
        run = await wait_run(chat_id, timeout=600)
        assert run.status == "success", run.error

        checks = [t for t in await timelines(chat_id) if t.block_type == "Browser-RT"]
        assert len(checks) >= 2, [t.content for t in checks]
        first, last = json.loads(checks[0].content), json.loads(checks[-1].content)
        assert any("OA_INJECTED_RUNTIME_ERROR" in e for e in first["errors"]), first
        assert last["errors"] == [] and last["status"] == 200, last
        for doc in (first, last):  # real Chromium screenshots of the live preview
            png = await get_storage().get_bytes(f"chats/{chat_id}/{doc['screenshot']}")
            assert png and png.startswith(PNG) and len(png) > 2000
        async with session_scope() as s:
            ads = (await s.execute(select(m.ActionData).where(m.ActionData.chat_id == chat_id,
                                                               m.ActionData.command_name == "checkui_run"))).scalars().all()
        assert [a.status for a in ads][:1] == ["failed"] and ads[-1].status == "success"
        ws = settings.workspace_dir(chat_id)
        assert "throw" not in (ws / "src/components/Telemetry.tsx").read_text()

        # version cover rendered from the built dist and served through the signed endpoint
        async with session_scope() as s:
            v = (await s.execute(select(m.Version).where(m.Version.chat_id == chat_id))).scalar_one()
        assert v.status == "ready" and v.cover and v.cover.startswith(f"/api/v1/covers/{chat_id}/v1.png?sig=")
        r = await client.get(v.cover)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(PNG)
        assert (await client.get(f"/api/v1/covers/{chat_id}/v1.png?sig=deadbeef")).status_code == 404
        listed = (await client.get(f"/api/v1/chats/{chat_id}/versions",
                                   headers=(await register())["headers"])).status_code
        assert listed in (403, 404)  # other users can't list versions; the cover URL itself is capability-based
    finally:
        await manager.stop(chat_id)


def _online() -> bool:
    try:
        from oa.tools.web import UA

        return httpx.get("https://en.wikipedia.org/wiki/Main_Page", timeout=8, headers={"User-Agent": UA}).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _online(), reason="needs internet for the real browser target")
async def test_browser_goto_real_screenshot(client, register, fake_sandbox):  # noqa: F811
    chat_id, uid = await make_chat(client, register, agent_mode="full")
    await send(chat_id, uid, "Make a pomodoro timer")
    run = await wait_run(chat_id, timeout=300)
    assert run.status == "success", run.error
    browser = [t for t in await timelines(chat_id) if t.block_type == "Browser"]
    assert browser
    doc = json.loads(browser[0].content)
    assert "Pomodoro" in doc["title"] and doc["screenshot"], doc
    png = await get_storage().get_bytes(f"chats/{chat_id}/{doc['screenshot']}")
    assert png and png.startswith(PNG) and len(png) > 10_000
