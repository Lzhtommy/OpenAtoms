"""Terminal block: terminal_run (non-interactive `$ cmd` + streamed output, MetaGPT style)."""
from __future__ import annotations

import re
from typing import Any

from oa.tools.base import ToolContext, ToolResult, tool, truncate_for_llm

DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1200
_DEV_SERVER = re.compile(r"(^|[;&|]\s*)(pnpm|npm|yarn|bun)\s+(run\s+)?(dev|start|preview)\b|(^|\s)vite(\s|$)(?!build)")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def is_build_cmd(cmd: str) -> bool:
    return bool(re.search(r"\b(pnpm|npm|yarn|bun)\s+(run\s+)?build\b|\bvite\s+build\b|\btsc\b", cmd))


@tool(
    "terminal_run",
    "Run a shell command (bash) in the project root and return its output. Commands are non-interactive; "
    "do not start the dev server (it is managed automatically and the preview updates live).",
    {
        "cmd": {"type": "string", "description": "Shell command, e.g. `pnpm add zod` or `pnpm build`"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default 300)", "optional": True},
        "cwd": {"type": "string", "description": "Working dir relative to project root", "optional": True},
    },
    block_type="Terminal",
)
async def terminal_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    cmd = str(args["cmd"]).strip()
    if _DEV_SERVER.search(cmd) and not is_build_cmd(cmd):
        return ToolResult(
            "The dev server is already managed by the platform (it restarts automatically and hot-reloads). "
            "Do not start it manually; use `pnpm build` to verify or checkui_run to inspect the preview.",
            ok=False,
        )
    if cmd.rstrip().endswith("&"):
        return ToolResult("Background commands are not supported; run commands that terminate.", ok=False)
    timeout = min(int(args.get("timeout") or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S)
    cwd = args.get("cwd") or None
    sb = await ctx.rc.sandbox()
    code: int = -1
    timed_out = False
    out: list[str] = []
    async with ctx.record("terminal_run", "Terminal", extra={"cmd": cmd}) as blk:
        await blk.append(f"$ {cmd}\n")
        async for ev in sb.exec(cmd, cwd=cwd, env={"CI": "1", "FORCE_COLOR": "0"}, timeout_ms=timeout * 1000):
            await ctx.rc.poll_cancel()
            t = ev.get("type")
            if t in ("stdout", "stderr"):
                chunk = _ANSI.sub("", ev.get("data", ""))
                out.append(chunk)
                await blk.append(chunk)
            elif t == "exit":
                code = int(ev.get("code", -1))
                timed_out = bool(ev.get("timed_out"))
        if timed_out:
            await blk.append(f"\n[timed out after {timeout}s]\n")
        await blk.complete(
            status="success" if code == 0 else "failed",
            snapshots={"terminal.log": blk.content},
            extra_data={"cmd": cmd, "exit_code": code},
            extra={"exit_code": code},
        )
    output = "".join(out)
    if is_build_cmd(cmd):
        ctx.rc.state["last_build_ok"] = code == 0
        ctx.rc.state["last_build_output"] = output[-6000:]
        if code == 0:
            ctx.rc.state["dirty"] = False
    masked, _ = ctx.rc.mask(output)
    tail = " (timed out)" if timed_out else ""
    return ToolResult(f"{truncate_for_llm(masked) or '(no output)'}\n[exit code: {code}{tail}]", ok=code == 0)
