"""Visual editing (PLAN P9-2): stage element edits picked in the preview, rewrite JSX source, version the result.

Flow: bridge.js reports a selection with `loc` = "src/App.tsx:12:5" (stamped by the template's dev-only Babel
plugin) → the workbench stages edits `{loc, add, remove, text}` → commit rewrites the source with the project's
own TypeScript compiler API (rewrite.cjs), records Editor blocks so the timeline/chat show the change, and
creates a version (git commit + build).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from oa.config import settings

REWRITER = Path(__file__).with_name("rewrite.cjs")
EDITABLE_SUFFIXES = {".tsx", ".jsx", ".ts", ".js"}
LOC_RE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+)$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_:/\-\[\]\.#%()!,]+$")  # tailwind-ish class token, incl. arbitrary values


class EditError(ValueError):
    pass


@dataclass
class Loc:
    path: str
    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.col}"


def parse_loc(loc: str) -> Loc:
    m = LOC_RE.match(loc or "")
    if not m:
        raise EditError(f"invalid loc {loc!r}")
    p = PurePosixPath(m["path"])
    if p.is_absolute() or ".." in p.parts or p.suffix not in EDITABLE_SUFFIXES or "node_modules" in p.parts:
        raise EditError(f"loc points outside the editable sources: {loc!r}")
    return Loc(str(p), int(m["line"]), int(m["col"]))


def _tokens(v: Any) -> list[str]:
    if v is None:
        return []
    items = v.split() if isinstance(v, str) else list(v)
    out: list[str] = []
    for t in items:
        t = str(t).strip()
        if not t:
            continue
        if not _TOKEN_RE.match(t):
            raise EditError(f"invalid class token {t!r}")
        if t not in out:
            out.append(t)
    return out


@dataclass
class Edit:
    loc: str
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    text: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)  # selector/tag/original text for display

    def to_json(self) -> dict[str, Any]:
        return {"loc": self.loc, "add": self.add, "remove": self.remove, "text": self.text, **self.meta}


def normalize(raw: dict[str, Any]) -> Edit:
    """Accept {loc, add?, remove?} or {loc, classes (desired), orig_classes} plus optional text."""
    loc = str(parse_loc(str(raw.get("loc") or "")))
    add, remove = _tokens(raw.get("add")), _tokens(raw.get("remove"))
    if raw.get("classes") is not None:
        want, orig = _tokens(raw.get("classes")), _tokens(raw.get("orig_classes"))
        add += [c for c in want if c not in orig and c not in add]
        remove += [c for c in orig if c not in want and c not in remove]
    text = raw.get("text")
    if text is not None:
        text = str(text)
        if len(text) > 5000:
            raise EditError("text too long")
    meta = {k: raw[k] for k in ("selector", "tag", "orig_text") if raw.get(k) is not None}
    return Edit(loc, [c for c in add if c not in remove], remove, text, meta)


def merge(staged: list[Edit], incoming: list[Edit]) -> list[Edit]:
    """Fold later edits of the same element into the earlier one (net add/remove, last text wins)."""
    by = {e.loc: e for e in staged}
    order = [e.loc for e in staged]
    for e in incoming:
        cur = by.get(e.loc)
        if cur is None:
            by[e.loc] = e
            order.append(e.loc)
            continue
        add = [c for c in cur.add if c not in e.remove] + [c for c in e.add if c not in cur.add]
        remove = [c for c in cur.remove if c not in e.add] + [c for c in e.remove if c not in cur.remove]
        by[e.loc] = Edit(e.loc, add, remove, e.text if e.text is not None else cur.text, {**cur.meta, **e.meta})
    return [by[k] for k in order if (by[k].add or by[k].remove or by[k].text is not None)]


# ---------------------------------------------------------------- staging store (shared volume, per chat)
def _stage_file(chat_id: str) -> Path:
    return settings.chat_dir(chat_id) / "edit_session.json"


def load(chat_id: str) -> list[Edit]:
    f = _stage_file(chat_id)
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for r in raw.get("edits", []):
        meta = {k: r[k] for k in ("selector", "tag", "orig_text") if r.get(k) is not None}
        out.append(Edit(r["loc"], r.get("add") or [], r.get("remove") or [], r.get("text"), meta))
    return out


def save(chat_id: str, edits: list[Edit]) -> None:
    f = _stage_file(chat_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"edits": [e.to_json() for e in edits]}, ensure_ascii=False))
    os.replace(tmp, f)


def clear(chat_id: str) -> None:
    _stage_file(chat_id).unlink(missing_ok=True)


# ---------------------------------------------------------------- source rewriting
def _node_path(workspace: Path) -> str:
    for base in (workspace / "node_modules", settings.template_dir / "node_modules"):
        if (base / "typescript").exists():
            return str(base)
    raise EditError("TypeScript is not installed in the project (node_modules/typescript missing)")


async def rewrite_source(workspace: Path, rel_path: str, source: str, edits: list[Edit]) -> tuple[str, list[dict]]:
    node = shutil.which("node")
    if not node:
        raise EditError("node is not available to rewrite sources")
    payload = {"filename": rel_path, "source": source, "edits": []}
    for e in edits:
        loc = parse_loc(e.loc)
        payload["edits"].append({"line": loc.line, "col": loc.col, "add": e.add, "remove": e.remove, "text": e.text})
    env = {**os.environ, "NODE_PATH": _node_path(workspace)}
    proc = await asyncio.create_subprocess_exec(
        node, str(REWRITER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env,
    )
    out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout=60)
    if proc.returncode != 0:
        raise EditError(f"rewriter failed: {err.decode(errors='replace')[-500:]}")
    res = json.loads(out)
    return res["source"], res["results"]


@dataclass
class CommitResult:
    files: dict[str, tuple[str, str]]  # rel path -> (before, after)
    errors: list[dict[str, Any]]


async def plan_commit(chat_id: str, edits: list[Edit]) -> CommitResult:
    """Compute new file contents for all staged edits (no writes). All-or-nothing: any error aborts."""
    ws = settings.workspace_dir(chat_id)
    groups: dict[str, list[Edit]] = {}
    for e in edits:
        groups.setdefault(parse_loc(e.loc).path, []).append(e)
    files: dict[str, tuple[str, str]] = {}
    errors: list[dict[str, Any]] = []
    root = ws.resolve()
    for rel, es in groups.items():
        p = (ws / rel).resolve()
        if root not in p.parents or not p.is_file():
            errors += [{"loc": e.loc, "error": f"{rel} not found in the workspace"} for e in es]
            continue
        before = p.read_text()
        after, results = await rewrite_source(ws, rel, before, es)
        for e, r in zip(es, results, strict=True):
            if not r.get("ok"):
                errors.append({"loc": e.loc, "error": r.get("error") or "edit failed"})
        if after != before:
            files[rel] = (before, after)
    return CommitResult(files, errors)


def describe(edits: list[Edit]) -> str:
    parts = []
    for e in edits:
        what = []
        if e.text is not None:
            what.append(f"text → {e.text[:40]!r}")
        if e.add or e.remove:
            what.append(" ".join([f"+{c}" for c in e.add] + [f"-{c}" for c in e.remove]))
        parts.append(f"<{e.meta.get('tag') or 'element'}> {PurePosixPath(parse_loc(e.loc).path).name}: {', '.join(what)}")
    return "; ".join(parts)


async def apply_commit(chat_id: str, user_name: str, edits: list[Edit], plan: CommitResult) -> None:
    """Write files and record them as Editor blocks inside a chat message from the user."""
    from oa.agents.emit import StreamingMessage
    from oa.tools.base import Block, ToolContext
    from oa.tools.editor import _snapshot, language

    ws = settings.workspace_dir(chat_id)
    # role must not be "User": user messages are the nodes of the round/attempt branch tree, and a round-0 user
    # message would become the tree root and hide the real conversation. System + the user's name, like
    # version/restore notices; the Editor blocks themselves are owned by "User".
    msg = StreamingMessage(chat_id, role="System", name=user_name, round_id=0, attempt=0, type="message",
                           metadata={"visual_edit": True, "edits": [e.to_json() for e in edits]})
    await msg.append_text(f"🎨 可视化编辑 / Visual edit — {len(edits)} change(s)\n")
    rc = SimpleNamespace(chat_id=chat_id, version_label=None, secrets=[], mask=lambda t: (t, False))
    ctx = ToolContext(rc=rc, role=SimpleNamespace(role="User", name=user_name), message=msg)  # type: ignore[arg-type]
    for rel, (before, after) in plan.files.items():
        extra = {"path": rel, "language": language(rel)}
        blk = Block(ctx, "editor_edit_file_by_replace", "Editor", src_path=f"/workspace/{rel}", extra=extra)
        await blk.start()
        await blk.replace(after)
        (ws / rel).write_text(after)
        await blk.complete(snapshots=_snapshot(rel, before, after), extra_data=extra)
    await msg.finish()
