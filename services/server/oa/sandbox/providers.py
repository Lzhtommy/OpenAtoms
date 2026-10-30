"""Sandbox providers (ARCHITECTURE §8.1).

A provider only knows how to start/stop the process or container that runs sandboxd for one chat and
how to map logical in-sandbox ports (5173) to host-reachable upstream URLs. Everything else (workspace
init, dev server, status bookkeeping) lives in manager.py and talks to sandboxd over HTTP.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from oa.config import settings

log = logging.getLogger("oa.sandbox")

DEV_PORT = 5173
SANDBOXD_PORT = 49999


@dataclass
class Handle:
    external_id: str
    endpoint: str  # sandboxd base url
    ports: dict[str, str] = field(default_factory=dict)  # logical port -> upstream base url
    dev_env: dict[str, str] = field(default_factory=dict)  # env the dev server must get (PORT etc.)


class Provider(Protocol):
    name: str

    async def start(self, chat_id: str, workspace: Path, token: str, prev: Handle | None) -> Handle: ...
    async def stop(self, chat_id: str, handle: Handle) -> None: ...
    async def destroy(self, chat_id: str, handle: Handle | None) -> None: ...
    async def alive(self, handle: Handle) -> bool: ...


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_SECRETISH = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def sanitized_host_env() -> dict[str, str]:
    """Env for processes we spawn on the host: never leak platform credentials into user code."""
    env = {}
    for k, v in os.environ.items():
        if k.startswith("OA_") or _SECRETISH.search(k) or k.startswith("CLAUDE"):
            continue
        env[k] = v
    return env


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LocalProvider:
    """Runs sandboxd directly on the host (dev / single machine). No isolation — dev only."""

    name = "local"

    async def start(self, chat_id: str, workspace: Path, token: str, prev: Handle | None) -> Handle:
        return self._spawn(workspace, token, settings.chat_dir(chat_id) / "sandboxd.log")

    async def start_slot(self, slot_id: str, slot_dir: Path, token: str) -> Handle:
        """Warm-pool slot: sandboxd rooted at <slot_dir>/ws (see oa.sandbox.pool)."""
        return self._spawn(slot_dir / "ws", token, slot_dir / "sandboxd.log")

    async def destroy_slot(self, slot_id: str, external_id: str | None) -> None:
        if external_id:
            await self.destroy("", Handle(external_id, ""))

    def _spawn(self, root: Path, token: str, log_path: Path) -> Handle:
        binary = Path(settings.sandboxd_bin)
        if not binary.exists():
            raise RuntimeError(f"sandboxd binary missing at {binary}; run `make sandboxd`")
        root.mkdir(parents=True, exist_ok=True)
        sbx_port = free_port()
        dev_port = free_port()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = sanitized_host_env()
        env.update({"SANDBOXD_TOKEN": token, "HOME": env.get("HOME", str(root))})
        logf = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [str(binary), "--listen", f"127.0.0.1:{sbx_port}", "--root", str(root), "--token", token],
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=str(root),
                start_new_session=True,
            )
        finally:
            logf.close()
        # Reap the child so it never becomes a zombie. A dedicated daemon thread, NOT the default executor:
        # proc.wait() blocks for the sandbox's whole lifetime, and parking one executor thread per sandbox
        # starved asyncio.to_thread (storage, git, workspace copy) once ~20 sandboxes ran (found by loadtest).
        threading.Thread(target=proc.wait, name=f"reap-sandboxd-{proc.pid}", daemon=True).start()
        return Handle(
            external_id=str(proc.pid),
            endpoint=f"http://127.0.0.1:{sbx_port}",
            ports={str(DEV_PORT): f"http://127.0.0.1:{dev_port}"},
            dev_env={"PORT": str(dev_port), "OA_HMR_CLIENT_PORT": _gateway_port()},
        )

    async def stop(self, chat_id: str, handle: Handle) -> None:
        await self.destroy(chat_id, handle)

    async def destroy(self, chat_id: str, handle: Handle | None) -> None:
        if not handle or not handle.external_id:
            return
        try:
            pid = int(handle.external_id)
        except ValueError:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            for _ in range(20):
                if not _pid_alive(pid):
                    return
                await asyncio.sleep(0.1)

    async def alive(self, handle: Handle) -> bool:
        try:
            return _pid_alive(int(handle.external_id))
        except ValueError:
            return False


def _gateway_port() -> str:
    dom = settings.app_domain
    return dom.rsplit(":", 1)[1] if ":" in dom else ("443" if settings.app_scheme == "https" else "80")


class DockerProvider:
    """One container per chat; workspace bind-mounted at /workspace (storage/compute separation)."""

    name = "docker"

    def __init__(self) -> None:
        import docker

        self.client = docker.from_env()

    def _name(self, chat_id: str) -> str:
        return f"oa-sbx-{chat_id}"

    def _host_workspace(self, workspace: Path) -> str:
        # When the API itself runs in a container, OA_SANDBOX_HOST_DATA_DIR maps data_dir to the host path.
        host_root = os.environ.get("OA_SANDBOX_HOST_DATA_DIR")
        if host_root:
            return str(Path(host_root) / workspace.resolve().relative_to(settings.data_dir.resolve()))
        return str(workspace.resolve())

    def _run_kwargs(self, token: str, root: str, host_dir: str, mount: str, labels: dict[str, str]) -> dict:
        kw: dict = dict(
            # Explicit command line: the image ENTRYPOINT hard-codes `--root /workspace`, which would override
            # SANDBOXD_ROOT and make warm-pool slots (root /slot/ws) serve an empty directory.
            entrypoint=["/usr/bin/tini", "--", "sandboxd"],
            command=["--listen", f":{SANDBOXD_PORT}", "--root", root],
            detach=True,
            labels={"openatoms.role": "sandbox", **labels},
            environment={
                "SANDBOXD_TOKEN": token,
                "SANDBOXD_ROOT": root,
                "SANDBOXD_LISTEN": f":{SANDBOXD_PORT}",
            },
            volumes={host_dir: {"bind": mount, "mode": "rw"}},
            ports={f"{SANDBOXD_PORT}/tcp": ("127.0.0.1", None), f"{DEV_PORT}/tcp": ("127.0.0.1", None)},
            mem_limit=os.environ.get("OA_SANDBOX_MEM", "2g"),
            nano_cpus=int(float(os.environ.get("OA_SANDBOX_CPUS", "2")) * 1e9),
            pids_limit=int(os.environ.get("OA_SANDBOX_PIDS", "1024")),
            security_opt=["no-new-privileges"],
            cap_drop=["NET_RAW", "SYS_ADMIN"],
        )
        # Egress policy: run sandboxes on a dedicated network (e.g. created with a firewall/proxy in front,
        # or `--internal` to cut egress entirely). Default: docker's bridge.
        net = os.environ.get("OA_SANDBOX_NETWORK")
        if net:
            kw["network"] = net
        runtime = os.environ.get("OA_SANDBOX_RUNTIME")  # e.g. "runsc" when gVisor is installed on the host
        if runtime:
            kw["runtime"] = runtime
        return kw

    def _wait_ports(self, c) -> Handle:
        import time

        for _ in range(50):
            c.reload()
            pmap = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            if pmap.get(f"{SANDBOXD_PORT}/tcp"):
                break
            time.sleep(0.1)
        pmap = c.attrs["NetworkSettings"]["Ports"]

        def hp(p: int) -> str:
            b = (pmap.get(f"{p}/tcp") or [{}])[0]
            return f"http://127.0.0.1:{b.get('HostPort')}"

        return Handle(
            external_id=c.id,
            endpoint=hp(SANDBOXD_PORT),
            ports={str(DEV_PORT): hp(DEV_PORT)},
            dev_env={"PORT": str(DEV_PORT), "OA_HMR_CLIENT_PORT": _gateway_port()},
        )

    async def start(self, chat_id: str, workspace: Path, token: str, prev: Handle | None) -> Handle:
        import docker.errors

        workspace.mkdir(parents=True, exist_ok=True)

        def _run() -> Handle:
            name = self._name(chat_id)
            try:
                c = self.client.containers.get(name)
                env = dict(e.split("=", 1) for e in (c.attrs.get("Config", {}).get("Env") or []) if "=" in e)
                if env.get("SANDBOXD_TOKEN") != token:
                    # The manager mints a fresh token per start; a leftover container (stopped sandbox, api
                    # restart) would answer 401 to everything. Containers are disposable: replace it.
                    c.remove(force=True)
                    raise docker.errors.NotFound("replaced")
                if c.status != "running":
                    c.start()
            except docker.errors.NotFound:
                c = self.client.containers.run(
                    settings.sandbox_image, name=name,
                    **self._run_kwargs(token, "/workspace", self._host_workspace(workspace), "/workspace",
                                       {"openatoms.chat": chat_id}),
                )
            return self._wait_ports(c)

        return await asyncio.to_thread(_run)

    async def start_slot(self, slot_id: str, slot_dir: Path, token: str) -> Handle:
        """Warm-pool slot: the slot dir is mounted at /slot and sandboxd serves /slot/ws (see oa.sandbox.pool)."""

        def _run() -> Handle:
            c = self.client.containers.run(
                settings.sandbox_image, name=f"oa-pool-{slot_id}",
                **self._run_kwargs(token, "/slot/ws", self._host_workspace(slot_dir), "/slot",
                                   {"openatoms.slot": slot_id, "openatoms.pool": "1"}),
            )
            return self._wait_ports(c)

        return await asyncio.to_thread(_run)

    async def destroy_slot(self, slot_id: str, external_id: str | None) -> None:
        import docker.errors

        def _rm() -> None:
            for ref in filter(None, (external_id, f"oa-pool-{slot_id}")):
                try:
                    self.client.containers.get(ref).remove(force=True)
                    return
                except docker.errors.NotFound:
                    continue

        await asyncio.to_thread(_rm)

    async def list_slots(self) -> list[tuple[str, str]]:
        def _ls() -> list[tuple[str, str]]:
            cs = self.client.containers.list(all=True, filters={"label": "openatoms.pool=1"})
            return [(c.labels.get("openatoms.slot", ""), c.id) for c in cs]

        return await asyncio.to_thread(_ls)

    async def stop(self, chat_id: str, handle: Handle) -> None:
        # The workspace lives on the bind-mounted volume, so a stopped sandbox has no state worth keeping:
        # remove the container (frees memory/disk; the next wake creates a fresh one with a fresh token).
        await self.destroy(chat_id, handle)

    async def destroy(self, chat_id: str, handle: Handle | None) -> None:
        import docker.errors

        def _rm() -> None:
            try:
                self.client.containers.get(self._name(chat_id)).remove(force=True)
            except docker.errors.NotFound:
                pass

        await asyncio.to_thread(_rm)

    async def alive(self, handle: Handle) -> bool:
        import docker.errors

        def _a() -> bool:
            try:
                return self.client.containers.get(handle.external_id).status == "running"
            except docker.errors.NotFound:
                return False

        return await asyncio.to_thread(_a)


_provider: Provider | None = None


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        kind = settings.sandbox_provider
        if kind == "docker":
            _provider = DockerProvider()
        elif kind == "k8s":
            from oa.sandbox.k8s import K8sProvider

            _provider = K8sProvider()
        else:
            _provider = LocalProvider()
    return _provider


def set_provider(p: Provider | None) -> None:
    """Test seam."""
    global _provider
    _provider = p
