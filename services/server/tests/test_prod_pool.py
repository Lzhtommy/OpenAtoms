"""P8: warm pool claim/release with real sandboxd (local provider) + abuse monitor decisions."""
from __future__ import annotations

import pytest

from oa.config import settings
from oa.sandbox import abuse

SANDBOXD = settings.sandboxd_bin


@pytest.fixture
def warm_pool(monkeypatch, tmp_path):
    from oa.sandbox import pool
    from oa.sandbox.providers import set_provider

    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "README.md").write_text("# tiny template (no dev script → no pnpm)\n")
    monkeypatch.setattr(settings, "template_dir", tpl)
    monkeypatch.setenv("OA_SANDBOX_WARM_POOL", "1")
    set_provider(None)
    yield pool
    set_provider(None)


@pytest.mark.skipif(not SANDBOXD.exists(), reason="sandboxd binary not built")
async def test_warm_pool_claim_and_release(client, register, warm_pool):
    from oa.core.observability import SANDBOX_START
    from oa.sandbox import manager

    pool = warm_pool
    assert await pool.fill_once() == 1 and await pool.ready_count() == 1
    u = await register()
    cid = (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "p"})).json()["data"]["chat_id"]
    ws = settings.workspace_dir(cid)
    warm_before = SANDBOX_START.labels("local", "warm")._sum.get()

    cl = await manager.ensure_running(cid)
    try:
        assert ws.is_symlink(), "workspace should be attached to the slot"
        slot_root = ws.resolve()
        assert slot_root.parent.parent == (settings.data_dir / "pool").resolve()
        assert await pool.ready_count() == 0
        # sandbox sees the chat workspace; platform-side writes through the symlink are visible in the sandbox
        assert (await cl.read("README.md")).startswith(b"# tiny template")
        (ws / "from_host.txt").write_text("hi")
        assert await cl.read("/workspace/from_host.txt") == b"hi"
        await cl.write("from_sandbox.txt", "yo")
        assert (ws / "from_sandbox.txt").read_text() == "yo"
        code, out = await cl.run("pwd && git log --oneline | head -1")
        assert code == 0 and "init" in out
        assert SANDBOX_START.labels("local", "warm")._sum.get() > warm_before
        from oa.db.models import Sandbox
        from oa.db.session import session_scope

        async with session_scope() as s:
            sb = await s.get(Sandbox, cid)
            assert sb.ports.get("_slot") == slot_root.parent.name
    finally:
        await manager.stop(cid)
    assert ws.is_dir() and not ws.is_symlink(), "workspace must be home again after stop"
    assert (ws / "from_sandbox.txt").read_text() == "yo"
    assert not slot_root.parent.exists(), "used slot is destroyed (single-use)"

    # next start: pool empty → cold start, workspace stays a plain dir
    cl = await manager.ensure_running(cid)
    try:
        assert not ws.is_symlink() and await cl.read("from_host.txt") == b"hi"
    finally:
        await manager.stop(cid)


@pytest.mark.skipif(not SANDBOXD.exists(), reason="sandboxd binary not built")
async def test_crash_recovery_restores_workspace(client, register, warm_pool):
    pool = warm_pool
    await pool.fill_once()
    u = await register()
    cid = (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "r"})).json()["data"]["chat_id"]
    from oa.sandbox import manager

    await manager.init_workspace(cid)
    slot = await pool.claim(cid)
    assert slot is not None and settings.workspace_dir(cid).is_symlink()
    # simulate a crash: nobody released the slot. The next start must bring the workspace home first.
    sid = await pool.recover_workspace(cid)
    assert sid == slot.slot_id and settings.workspace_dir(cid).is_dir()
    await pool.destroy_slot(slot.slot_id, slot.external_id)
    await pool._cleanup_orphans()
    assert not (settings.data_dir / "pool" / slot.slot_id).exists()


@pytest.mark.skipif(not SANDBOXD.exists(), reason="sandboxd binary not built")
async def test_dead_ready_slot_is_pruned_and_refilled(warm_pool):
    """A ready slot whose sandbox died must not count toward the target, or the pool never refills."""
    pool = warm_pool
    assert await pool.fill_once() == 1
    dead = pool._local_ready[0]
    from oa.sandbox.providers import get_provider

    await get_provider().destroy_slot(dead.slot_id, dead.external_id)  # the sandbox dies behind the pool's back
    assert await pool.fill_once() == 0, "dead entry still counts as ready"
    assert await pool._prune_ready() == 1 and await pool.ready_count() == 0
    assert await pool.fill_once() == 1
    live = pool._local_ready[0]
    assert live.slot_id != dead.slot_id and await pool._prune_ready() == 0
    await pool.shutdown()


def _scan(procs, syn=0, dests=0):
    return {"self": 999, "procs": procs, "syn_sent": syn, "syn_dests": dests}


def test_abuse_miner_killed_immediately():
    acts = abuse.evaluate("c", _scan([{"pid": 42, "cpu": 5, "age": 3, "cmd": "./xmrig -o stratum+tcp://pool:3333"}]))
    assert acts == [{"reason": "miner", "action": "kill", "pid": 42, "detail": acts[0]["detail"]}]


def test_abuse_cpu_hog_needs_two_scans_and_spares_dev_tools(monkeypatch):
    monkeypatch.setenv("OA_ABUSE_MIN_AGE", "60")
    hog = {"pid": 7, "cpu": 99.0, "age": 900, "cmd": "/tmp/spin --forever"}
    node = {"pid": 8, "cpu": 99.0, "age": 900, "cmd": "node /workspace/node_modules/.bin/vite build"}
    young = {"pid": 9, "cpu": 99.0, "age": 10, "cmd": "/tmp/new"}
    assert abuse.evaluate("c2", _scan([hog, node, young])) == []
    acts = abuse.evaluate("c2", _scan([hog, node, young]))
    assert [a["pid"] for a in acts] == [7] and acts[0]["reason"] == "cpu_hog"
    # cooled down → counter resets
    assert abuse.evaluate("c2", _scan([{**hog, "cpu": 1.0}])) == []
    assert abuse.evaluate("c2", _scan([hog])) == []


def test_abuse_port_scan_stops_sandbox():
    acts = abuse.evaluate("c3", _scan([], syn=200, dests=150))
    assert acts and acts[0]["action"] == "stop" and acts[0]["reason"] == "port_scan"


def test_abuse_scanner_runs_in_sandbox(tmp_path):
    """The /proc scanner itself runs (Linux /proc needed: executed in the docker image if available)."""
    import shutil
    import subprocess

    if not shutil.which("docker") or subprocess.run(["docker", "image", "inspect", settings.sandbox_image],
                                                   capture_output=True).returncode != 0:
        pytest.skip("sandbox image not available")
    out = subprocess.run(["docker", "run", "--rm", "-i", "--entrypoint", "python3", settings.sandbox_image, "-"],
                         input=abuse.SCANNER, capture_output=True, text=True, timeout=60)
    import json

    scan = json.loads(out.stdout.strip().splitlines()[-1])
    assert scan["procs"] and {"pid", "cpu", "age", "cmd"} <= set(scan["procs"][0])
    assert abuse.evaluate("c4", scan) == []


@pytest.mark.skipif(not SANDBOXD.exists(), reason="sandboxd binary not built")
async def test_local_sandboxes_do_not_starve_default_executor(tmp_path):
    """Regression (loadtest @20 concurrent runs): reaping sandboxd via run_in_executor(None, proc.wait) parked one
    default-executor thread per live sandbox, deadlocking every asyncio.to_thread once the pool was exhausted."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from oa.sandbox.providers import LocalProvider

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    prov = LocalProvider()
    handles = [prov._spawn(tmp_path / f"r{i}", "tok", tmp_path / f"r{i}.log") for i in range(3)]
    try:
        assert await asyncio.wait_for(asyncio.to_thread(lambda: 42), 3) == 42
    finally:
        for h in handles:
            await prov.destroy("", h)
        loop.set_default_executor(ThreadPoolExecutor())


def _docker_image_ok() -> bool:
    import shutil
    import subprocess

    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "image", "inspect", settings.sandbox_image], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_image_ok(), reason="docker / sandbox image not available")
async def test_docker_wake_after_stop_uses_new_token(tmp_path):
    """Regression (full-mode wake measurement): stop() left the container behind and start() restarted it with
    its old SANDBOXD_TOKEN while the manager used a fresh one → every sandboxd call returned 401."""
    import uuid

    from oa.sandbox.client import SandboxClient
    from oa.sandbox.providers import DockerProvider

    prov = DockerProvider()
    chat_id = "t" + uuid.uuid4().hex[:31]
    ws = settings.data_dir / "chats" / chat_id / "workspace"
    ws.mkdir(parents=True)
    h = None
    try:
        for token in ("tok-one", "tok-two"):
            h = await prov.start(chat_id, ws, token, h)
            c = SandboxClient(h.endpoint, token)
            try:
                for _ in range(100):
                    try:
                        await c.health()
                        break
                    except Exception:
                        import asyncio

                        await asyncio.sleep(0.2)
                code, out = await c.run("echo ok")
                assert code == 0 and "ok" in out
            finally:
                await c.aclose()
            await prov.stop(chat_id, h)
    finally:
        await prov.destroy(chat_id, h)


@pytest.mark.skipif(not _docker_image_ok(), reason="docker / sandbox image not available")
async def test_docker_warm_pool_claim_serves_workspace(client, register, monkeypatch, tmp_path):
    """Regression (full-mode loadtest/e2e): the image ENTRYPOINT's `--root /workspace` overrode SANDBOXD_ROOT,
    so a claimed Docker slot served an empty dir (dev server crash-looped, preview stuck on 'waking')."""
    from oa.sandbox import manager, pool
    from oa.sandbox.providers import DockerProvider, set_provider

    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "README.md").write_text("# docker pool\n")
    monkeypatch.setattr(settings, "template_dir", tpl)
    monkeypatch.setattr(settings, "sandbox_provider", "docker")
    monkeypatch.setenv("OA_SANDBOX_WARM_POOL", "1")
    set_provider(DockerProvider())
    try:
        assert await pool.fill_once() == 1
        u = await register()
        cid = (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "dp"})).json()["data"]["chat_id"]
        cl = await manager.ensure_running(cid)
        try:
            assert settings.workspace_dir(cid).is_symlink()
            assert (await cl.read("README.md")) == b"# docker pool\n"
            code, out = await cl.run("pwd && ls")
            assert code == 0 and "/slot/ws" in out and "README.md" in out
        finally:
            await manager.stop(cid)
        assert settings.workspace_dir(cid).is_dir() and not settings.workspace_dir(cid).is_symlink()
    finally:
        await pool.shutdown()
        set_provider(None)


async def test_capacity_admission_waits_for_free_slot(client, register, monkeypatch):
    import asyncio

    from oa.db.models import Sandbox
    from oa.db.session import session_scope
    from oa.sandbox import manager

    u = await register()
    ids = [(await client.post("/api/v1/chats", headers=u["headers"], json={"title": f"c{i}"})).json()["data"]["chat_id"]
           for i in range(2)]
    async with session_scope() as s:
        s.add(Sandbox(chat_id=ids[0], provider="local", status="running"))
    monkeypatch.setenv("OA_SANDBOX_MAX_TOTAL", "1")
    monkeypatch.setenv("OA_SANDBOX_CAPACITY_WAIT", "10")
    entered = asyncio.Event()

    async def take() -> None:
        async with manager._capacity_slot(ids[1]):
            entered.set()

    t = asyncio.create_task(take())
    await asyncio.sleep(2.5)
    assert not entered.is_set(), "must wait while the cap is reached"
    async with session_scope() as s:
        (await s.get(Sandbox, ids[0])).status = "stopped"
    await asyncio.wait_for(t, 5)
    assert entered.is_set()
    monkeypatch.setenv("OA_SANDBOX_CAPACITY_WAIT", "0")
    async with session_scope() as s:
        (await s.get(Sandbox, ids[0])).status = "running"
    with pytest.raises(manager.SandboxFull):
        async with manager._capacity_slot(ids[1]):
            pass
    async with session_scope() as s:
        (await s.get(Sandbox, ids[0])).status = "stopped"
