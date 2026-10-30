"""Editor block tools: editor_read / editor_write / editor_edit_file_by_replace / editor_append_file."""
from __future__ import annotations

import difflib
import json
import posixpath
from pathlib import PurePosixPath
from typing import Any

from oa.tools.base import ToolContext, ToolResult, tool

LANG = {
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".json": "json",
    ".css": "css", ".html": "html", ".md": "markdown", ".py": "python", ".sql": "sql", ".yml": "yaml",
    ".yaml": "yaml", ".sh": "shell", ".toml": "toml", ".svg": "xml",
}
READ_LIMIT_LINES = 600


def norm_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    for prefix in ("/workspace/", "workspace/"):
        p = p.removeprefix(prefix)
    p = p.lstrip("/")
    p = posixpath.normpath(p) if p else ""
    if not p or p == "." or p.startswith("..") or "/../" in f"/{p}/":
        raise ValueError(f"invalid path: {p!r} (must be inside the workspace)")
    return p


def language(path: str) -> str:
    return LANG.get(PurePosixPath(path).suffix.lower(), "plaintext")


async def _read_or_none(ctx: ToolContext, path: str) -> str | None:
    sb = await ctx.rc.sandbox()
    try:
        return (await sb.read(path)).decode("utf-8", "replace")
    except FileNotFoundError:
        return None


def _snapshot(path: str, before: str | None, after: str) -> dict[str, str]:
    name = PurePosixPath(path).name + ".json"
    return {name: json.dumps({"path": path, "before": before, "after": after}, ensure_ascii=False)}


def _mark_dirty(ctx: ToolContext, path: str) -> None:
    ctx.rc.state["dirty"] = True
    changed = ctx.rc.state.setdefault("changed_files", [])
    if path not in changed:
        changed.append(path)


async def _write_block(ctx: ToolContext, command: str, path: str, before: str | None, after: str) -> None:
    async with ctx.record(command, "Editor", src_path=f"/workspace/{path}",
                          extra={"path": path, "language": language(path)}) as blk:
        await blk.replace(after)
        sb = await ctx.rc.sandbox()
        await sb.write(path, after)
        _mark_dirty(ctx, path)
        await blk.complete(snapshots=_snapshot(path, before, after),
                           extra_data={"path": path, "language": language(path)})


def _diffstat(before: str | None, after: str) -> str:
    if before is None:
        return f"created ({after.count(chr(10)) + 1} lines)"
    d = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
    add = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
    rem = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
    return f"+{add} -{rem} lines"


@tool(
    "editor_read",
    "Read a file from the project workspace. Returns numbered lines. Paths are relative to the project root.",
    {
        "path": {"type": "string", "description": "File path relative to the project root, e.g. src/App.tsx"},
        "start_line": {"type": "integer", "description": "1-based first line (optional)", "optional": True},
        "end_line": {"type": "integer", "description": "1-based last line (optional)", "optional": True},
    },
    block_type="Editor",
)
async def editor_read(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = norm_path(args["path"])
    content = await _read_or_none(ctx, path)
    if content is None:
        sb = await ctx.rc.sandbox()
        parent = posixpath.dirname(path) or "."
        try:
            siblings = ", ".join(e["name"] for e in await sb.list(parent))
        except Exception:
            siblings = "?"
        return ToolResult(f"Error: {path} does not exist. Files in {parent}/: {siblings}", ok=False)
    async with ctx.record("editor_read", "Editor", src_path=f"/workspace/{path}",
                          extra={"path": path, "language": language(path)}) as blk:
        await blk.replace(content)
        await blk.complete(snapshots=_snapshot(path, content, content),
                           extra_data={"path": path, "language": language(path)})
    lines = content.splitlines()
    start = max(1, int(args.get("start_line") or 1))
    end = min(len(lines), int(args.get("end_line") or len(lines)))
    clipped = end - start + 1 > READ_LIMIT_LINES
    if clipped:
        end = start + READ_LIMIT_LINES - 1
    body = "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n… ({len(lines) - end} more lines; use start_line/end_line)" if clipped else ""
    return ToolResult(f"{path} ({len(lines)} lines)\n{body}{more}")


@tool(
    "editor_write",
    "Create or overwrite a file with the full given content. Parent directories are created automatically.",
    {
        "path": {"type": "string", "description": "File path relative to the project root"},
        "content": {"type": "string", "description": "Complete new file content"},
    },
    block_type="Editor",
)
async def editor_write(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = norm_path(args["path"])
    after = str(args["content"])
    before = await _read_or_none(ctx, path)
    await _write_block(ctx, "editor_write", path, before, after)
    return ToolResult(f"Wrote {path}: {_diffstat(before, after)}.")


@tool(
    "editor_edit_file_by_replace",
    "Replace an exact snippet in a file. old_str must match the file content exactly (including whitespace) and "
    "occur exactly once; include enough surrounding lines to make it unique.",
    {
        "path": {"type": "string"},
        "old_str": {"type": "string", "description": "Exact existing text to replace"},
        "new_str": {"type": "string", "description": "Replacement text"},
    },
    block_type="Editor",
)
async def editor_edit_file_by_replace(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = norm_path(args["path"])
    old, new = str(args["old_str"]), str(args["new_str"])
    before = await _read_or_none(ctx, path)
    if before is None:
        return ToolResult(f"Error: {path} does not exist. Use editor_write to create it.", ok=False)
    if not old:
        return ToolResult("Error: old_str must not be empty.", ok=False)
    n = before.count(old)
    if n == 0:
        hint = _closest(before, old)
        return ToolResult(
            f"Error: old_str not found in {path}. Re-read the file and copy the text exactly."
            + (f"\nClosest match:\n{hint}" if hint else ""),
            ok=False,
        )
    if n > 1:
        return ToolResult(f"Error: old_str occurs {n} times in {path}; include more context to make it unique.",
                          ok=False)
    after = before.replace(old, new, 1)
    await _write_block(ctx, "editor_edit_file_by_replace", path, before, after)
    return ToolResult(f"Edited {path}: {_diffstat(before, after)}.")


@tool(
    "editor_append_file",
    "Append content to the end of a file (created if missing).",
    {"path": {"type": "string"}, "content": {"type": "string"}},
    block_type="Editor",
)
async def editor_append_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = norm_path(args["path"])
    before = await _read_or_none(ctx, path)
    add = str(args["content"])
    after = (before or "") + ("" if not before or before.endswith("\n") else "\n") + add
    await _write_block(ctx, "editor_append_file", path, before, after)
    return ToolResult(f"Appended to {path}: {_diffstat(before, after)}.")


def _closest(text: str, snippet: str) -> str:
    first = snippet.strip().splitlines()[0].strip() if snippet.strip() else ""
    if not first:
        return ""
    lines = text.splitlines()
    best = difflib.get_close_matches(first, [line.strip() for line in lines], n=1, cutoff=0.5)
    if not best:
        return ""
    for i, line in enumerate(lines):
        if line.strip() == best[0]:
            lo, hi = max(0, i - 2), min(len(lines), i + 3)
            return "\n".join(f"{j + 1:>4}| {lines[j]}" for j in range(lo, hi))
    return ""
