"""Shared test fixtures. Environment is configured BEFORE importing `oa` so settings pick up a temp data dir."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

_DATA = Path(tempfile.mkdtemp(prefix="oa-test-"))
os.environ.update({
    "OA_MODE": "lite",
    "OA_DATA_DIR": str(_DATA),
    "OA_DATABASE_URL": "",
    "OA_SANDBOX_PROVIDER": "local",
    "OA_QUEUE": "inproc",
    "OA_STORAGE": "local",
    "OA_DEFAULT_MODEL": "oa-mock",
    "OA_BASIC_MODEL": "oa-mock",
    "OA_LLM_API_KEY": "",
    "OA_STRIPE_SECRET": "",
    "OA_SMTP_HOST": "",
    "OA_INTERNAL_TOKEN": "test-internal",
    "OA_RATE_LIMIT": "0",  # functional tests hammer auth from one IP; test_prod_* enables it explicitly
    "OA_SANDBOX_WARM_POOL": "0",
})

import httpx
import pytest
import pytest_asyncio

from oa.config import settings
from oa.db.session import init_db
from oa.main import app

INTERNAL = {"X-Internal-Token": "test-internal"}


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _DATA


@pytest_asyncio.fixture(scope="session")
async def client() -> httpx.AsyncClient:
    await init_db()
    from oa.core.events import bus
    from oa.realtime.server import sio

    bus.attach(sio)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
        yield c


async def register_user(client: httpx.AsyncClient, email: str | None = None, password: str = "secret123",
                        name: str = "Tester") -> dict[str, Any]:
    email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post("/api/v1/user/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return {"headers": {"Authorization": data["access_token"]}, "user": data["user"], "email": email,
            "password": password, "token": data["access_token"]}


@pytest.fixture
def register(client):
    async def _r(**kw: Any) -> dict[str, Any]:
        return await register_user(client, **kw)

    return _r


class FakeRunner:
    """Stands in for oa.agents.runner in platform tests (records calls, never runs agents)."""

    def __init__(self) -> None:
        self.started: list[tuple[str, int]] = []
        self.resumed: list[tuple[str, str, int | None, dict | None]] = []
        self.cancelled: list[str] = []
        self.suspended: dict[str, Any] = {}

    async def start_round(self, chat_id: str, message_id: int) -> int:
        self.started.append((chat_id, message_id))
        return len(self.started)

    async def resume(self, chat_id: str, action: str, message_id: int | None = None, payload: dict | None = None) -> bool:
        self.resumed.append((chat_id, action, message_id, payload))
        self.suspended.pop(chat_id, None)
        return True

    async def cancel(self, chat_id: str) -> bool:
        self.cancelled.append(chat_id)
        return True

    async def suspended_run(self, chat_id: str) -> Any:
        return self.suspended.get(chat_id)


class FakeManager:
    def __init__(self) -> None:
        self.woken: list[str] = []
        self.upstreams: dict[str, str] = {}

    async def init_workspace(self, chat_id: str, from_chat: str | None = None, version: str | None = None) -> None:
        import shutil

        dst = settings.workspace_dir(chat_id)
        if from_chat and settings.workspace_dir(from_chat).exists():
            if version and (settings.workspace_dir(from_chat) / ".git").exists():
                from oa.domain import gitops

                await gitops.export_ref(settings.workspace_dir(from_chat), version, dst)
            else:
                shutil.copytree(settings.workspace_dir(from_chat), dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(".git", "node_modules"))
        dst.mkdir(parents=True, exist_ok=True)

    async def get_client(self, chat_id: str) -> None:
        return None

    async def ensure_running(self, chat_id: str) -> None:
        return None

    async def resolve_port(self, chat_id: str, port: int) -> str | None:
        return self.upstreams.get(chat_id)

    async def wake(self, chat_id: str) -> None:
        self.woken.append(chat_id)

    async def stop(self, chat_id: str) -> None:
        pass

    async def heartbeat(self, chat_id: str) -> None:
        pass


@pytest.fixture
def fake_runtime(monkeypatch):
    from oa.domain import hooks

    runner, manager = FakeRunner(), FakeManager()
    monkeypatch.setattr(hooks, "runner", lambda: runner)
    monkeypatch.setattr(hooks, "manager", lambda: manager)
    return runner, manager
