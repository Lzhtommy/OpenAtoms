"""Git operations on the HOST workspace directory (workspace = git repo; versions = commits + tags)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "OpenAtoms",
    "GIT_AUTHOR_EMAIL": "agent@openatoms.local",
    "GIT_COMMITTER_NAME": "OpenAtoms",
    "GIT_COMMITTER_EMAIL": "agent@openatoms.local",
    "GIT_TERMINAL_PROMPT": "0",
}

DEFAULT_GITIGNORE = "node_modules/\ndist/\n.env\n.env.*\n*.log\n.DS_Store\n"


class GitError(RuntimeError):
    pass


async def run(cwd: Path, *args: str, check: bool = True, timeout: float = 120,
              env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd), env={**os.environ, **GIT_ENV, **(env or {})},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"git {' '.join(args)} timed out") from None
    text = out.decode(errors="replace")
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {text.strip()}")
    return proc.returncode or 0, text


async def ensure_repo(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        await run(ws, "init", "-q", "-b", "main")
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text(DEFAULT_GITIGNORE)


async def commit_all(ws: Path, message: str) -> str:
    await ensure_repo(ws)
    await run(ws, "add", "-A")
    await run(ws, "commit", "-q", "--allow-empty", "-m", message or "update")
    _, out = await run(ws, "rev-parse", "HEAD")
    return out.strip()


async def tag(ws: Path, name: str) -> None:
    await run(ws, "tag", "-f", name)


async def checkout_tree(ws: Path, ref: str) -> None:
    """Make the working tree identical to `ref` (tracked files), without moving HEAD."""
    _, listing = await run(ws, "ls-tree", "-r", "--name-only", ref)
    wanted = set(filter(None, listing.splitlines()))
    _, tracked = await run(ws, "ls-files")
    for f in set(filter(None, tracked.splitlines())) - wanted:
        p = ws / f
        if p.is_file():
            p.unlink()
    await run(ws, "checkout", ref, "--", ".")


async def export_ref(src: Path, ref: str, dst: Path) -> None:
    """Materialize `ref` of repo `src` into empty dir `dst` (used for remix of a specific version)."""
    dst.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_shell(
        f"git -C {_q(src)} archive --format=tar {_q(ref)} | tar -x -C {_q(dst)}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(out.decode(errors="replace"))


def _q(s: object) -> str:
    import shlex

    return shlex.quote(str(s))


def http_auth_env(username: str, secret: str) -> dict[str, str]:
    """Per-invocation HTTP auth for git without putting credentials in the remote URL (URLs end up in
    reflogs/FETCH_HEAD inside the workspace) or argv (visible in `ps`). Uses git's GIT_CONFIG_* env."""
    import base64

    basic = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": ""}
