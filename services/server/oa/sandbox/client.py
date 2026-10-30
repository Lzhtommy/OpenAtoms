"""Async HTTP client for sandboxd (CONTRACT §5)."""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class SandboxError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Sandbox-Token": token},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------------------------------------------------------- helpers
    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise SandboxError(f"sandboxd unreachable: {e}") from e
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            raise SandboxError(f"{method} {path} -> {r.status_code}: {msg}")
        return r.json() if r.content else {}

    async def _sse(self, method: str, path: str, **kw: Any) -> AsyncIterator[dict[str, Any]]:
        try:
            async with self._http.stream(method, path, timeout=httpx.Timeout(None, connect=5.0), **kw) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")
                    raise SandboxError(f"{method} {path} -> {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError as e:
            raise SandboxError(f"sandboxd stream failed: {e}") from e

    # ---------------------------------------------------------------- api
    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/health")

    async def exec(
        self, cmd: str, cwd: str | None = None, env: dict[str, str] | None = None, timeout_ms: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        body: dict[str, Any] = {"cmd": cmd}
        if cwd:
            body["cwd"] = cwd
        if env:
            body["env"] = env
        if timeout_ms:
            body["timeout_ms"] = timeout_ms
        async for ev in self._sse("POST", "/exec", json=body):
            yield ev

    async def run(
        self, cmd: str, cwd: str | None = None, env: dict[str, str] | None = None, timeout_ms: int | None = None
    ) -> tuple[int, str]:
        out: list[str] = []
        code = -1
        async for ev in self.exec(cmd, cwd=cwd, env=env, timeout_ms=timeout_ms):
            t = ev.get("type")
            if t in ("stdout", "stderr"):
                out.append(ev.get("data", ""))
            elif t == "exit":
                code = int(ev.get("code", -1))
                if ev.get("timed_out"):
                    out.append("\n[timed out]\n")
        return code, "".join(out)

    async def read(self, path: str) -> bytes:
        try:
            r = await self._http.get("/fs/read", params={"path": path})
        except httpx.HTTPError as e:
            raise SandboxError(f"sandboxd unreachable: {e}") from e
        if r.status_code == 404:
            raise FileNotFoundError(path)
        if r.status_code >= 400:
            raise SandboxError(f"read {path} -> {r.status_code}: {r.text}")
        return r.content

    async def write(self, path: str, content: str | bytes) -> None:
        if isinstance(content, bytes):
            body = {"path": path, "content_b64": base64.b64encode(content).decode()}
        else:
            body = {"path": path, "content": content}
        await self._json("POST", "/fs/write", json=body)

    async def list(self, path: str = ".", depth: int = 1) -> list[dict[str, Any]]:
        return (await self._json("GET", "/fs/list", params={"path": path, "depth": depth})).get("entries", [])

    async def stat(self, path: str) -> dict[str, Any]:
        return await self._json("GET", "/fs/stat", params={"path": path})

    async def rm(self, path: str) -> None:
        await self._json("POST", "/fs/rm", json={"path": path})

    async def mv(self, src: str, dst: str) -> None:
        await self._json("POST", "/fs/mv", json={"from": src, "to": dst})

    async def mkdir(self, path: str) -> None:
        await self._json("POST", "/fs/mkdir", json={"path": path})

    async def process_ensure(
        self, name: str, cmd: str, cwd: str | None = None, env: dict[str, str] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "cmd": cmd}
        if cwd:
            body["cwd"] = cwd
        if env:
            body["env"] = env
        return await self._json("POST", "/process/ensure", json=body)

    async def process_stop(self, name: str) -> None:
        await self._json("POST", "/process/stop", json={"name": name})

    async def process_list(self) -> list[dict[str, Any]]:
        res = await self._json("GET", "/process/list")
        return res if isinstance(res, list) else res.get("processes", [])

    async def process_logs(self, name: str, follow: bool = True, tail: int = 200) -> AsyncIterator[str]:
        if follow:
            async for ev in self._sse("GET", "/process/logs", params={"name": name, "follow": 1, "tail": tail}):
                yield ev.get("line", "")
        else:
            res = await self._json("GET", "/process/logs", params={"name": name, "follow": 0, "tail": tail})
            for line in res.get("lines", []):
                yield line

    async def ports(self, check: list[int]) -> list[int]:
        res = await self._json("GET", "/ports", params={"check": ",".join(str(p) for p in check)})
        return [int(p) for p in res.get("open", [])]

    async def kernel_execute(self, code: str, timeout_ms: int = 120000) -> dict[str, Any]:
        return await self._json(
            "POST", "/kernel/execute", json={"code": code, "timeout_ms": timeout_ms},
            timeout=httpx.Timeout(timeout_ms / 1000 + 15, connect=5.0),
        )

    async def kernel_restart(self) -> None:
        await self._json("POST", "/kernel/restart")
