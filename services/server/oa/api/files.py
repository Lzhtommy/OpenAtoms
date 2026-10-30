"""Workspace files (CONTRACT §1.6). Operates on the host workspace directory; no sandbox needed."""
from __future__ import annotations

import asyncio
import io
import mimetypes
import os
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from oa.config import settings
from oa.core.deps import chat_guard
from oa.core.envelope import bad_request, not_found, ok
from oa.core.storage import get_storage
from oa.db import models as m
from oa.domain.secrets import mask, secret_values

router = APIRouter(tags=["files"])

MAX_INLINE = 2 * 1024 * 1024
TREE_SKIP = {"node_modules", ".git", "dist", ".storage", ".venv", "__pycache__", ".pnpm-store", ".next"}
MAX_UPLOAD = 100 * 1024 * 1024


def workspace(chat_id: str) -> Path:
    return settings.workspace_dir(chat_id)


def safe_path(chat_id: str, rel: str) -> Path:
    root = workspace(chat_id).resolve()
    rel = (rel or "").strip()
    rel = rel.removeprefix("/workspace")
    p = (root / rel.lstrip("/")).resolve()
    if p != root and root not in p.parents:
        raise bad_request("path escapes workspace")
    return p


def _decode(data: bytes) -> tuple[str | None, bool]:
    if b"\x00" in data[:8192]:
        return None, True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return None, True


def _entry(root: Path, p: Path) -> dict:
    st = p.stat()
    return {"name": p.name, "path": p.relative_to(root).as_posix(), "type": "dir" if p.is_dir() else "file",
            "size": st.st_size, "mtime": st.st_mtime}


def _storage_key(chat_id: str, path: str) -> str:
    rel = path.lstrip("/")
    if ".." in rel.split("/"):
        raise bad_request("invalid path")
    return f"chats/{chat_id}/{rel}"


@router.get("/chats/{chat_id}/files")
async def get_files(path: str = "", chat: m.Chat = Depends(chat_guard("read", auth=False))):
    if path.lstrip("/").startswith(".storage/"):
        data = await get_storage().get_bytes(_storage_key(chat.id, path))
        if data is None:
            raise not_found("File not found")
        text, binary = _decode(data)
        return ok({"type": "file", "path": path, "content": text, "size": len(data), "binary": binary})
    p = safe_path(chat.id, path)
    if not p.exists():
        raise not_found("File not found")
    root = workspace(chat.id).resolve()
    if p.is_dir():
        entries = sorted((_entry(root, c) for c in p.iterdir()), key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return ok({"type": "dir", "path": p.relative_to(root).as_posix() if p != root else "", "entries": entries})
    size = p.stat().st_size
    content, binary = (None, True) if size > MAX_INLINE else _decode(await asyncio.to_thread(p.read_bytes))
    if content is not None and p.name.startswith(".env"):
        content, _ = mask(content, await secret_values(chat.id))
    return ok({"type": "file", "path": p.relative_to(root).as_posix(), "content": content, "size": size, "binary": binary})


@router.get("/chats/{chat_id}/files/raw")
async def raw_file(path: str, chat: m.Chat = Depends(chat_guard("read", auth=False))):
    if path.lstrip("/").startswith(".storage/"):
        data = await get_storage().get_bytes(_storage_key(chat.id, path))
    else:
        p = safe_path(chat.id, path)
        data = await asyncio.to_thread(p.read_bytes) if p.is_file() else None
    if data is None:
        raise not_found("File not found")
    return Response(data, media_type=mimetypes.guess_type(path)[0] or "application/octet-stream")


def _tree(root: Path, d: Path, depth: int = 0) -> list[dict]:
    out = []
    try:
        children = sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
    except OSError:
        return out
    for c in children:
        if c.name in TREE_SKIP or c.is_symlink():
            continue
        node = {"name": c.name, "path": c.relative_to(root).as_posix(), "type": "dir" if c.is_dir() else "file"}
        if c.is_dir():
            node["children"] = _tree(root, c, depth + 1) if depth < 12 else []
        out.append(node)
    return out


@router.get("/chats/{chat_id}/files/tree")
async def tree(chat: m.Chat = Depends(chat_guard("read", auth=False))):
    root = workspace(chat.id)
    if not root.exists():
        return ok([])
    return ok(await asyncio.to_thread(_tree, root.resolve(), root.resolve()))


class WriteBody(BaseModel):
    path: str
    content: str


@router.put("/chats/{chat_id}/files")
async def write_file(body: WriteBody, chat: m.Chat = Depends(chat_guard("write"))):
    p = safe_path(chat.id, body.path)
    if p == workspace(chat.id).resolve() or p.is_dir():
        raise bad_request("path is a directory")
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(p.write_text, body.content)
    return ok({"path": p.relative_to(workspace(chat.id).resolve()).as_posix(), "size": p.stat().st_size})


@router.delete("/chats/{chat_id}/files")
async def delete_file(path: str = Query(min_length=1), chat: m.Chat = Depends(chat_guard("write"))):
    p = safe_path(chat.id, path)
    if p == workspace(chat.id).resolve():
        raise bad_request("cannot delete workspace root")
    if not p.exists():
        raise not_found("File not found")
    if p.is_dir():
        await asyncio.to_thread(shutil.rmtree, p)
    else:
        p.unlink()
    return ok(None)


@router.post("/chats/{chat_id}/files/upload")
async def upload(file: UploadFile = File(...), dir: str = Form("uploads"), chat: m.Chat = Depends(chat_guard("write"))):
    name = os.path.basename(file.filename or "upload.bin") or "upload.bin"
    target = safe_path(chat.id, f"{dir.strip('/')}/{name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with target.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                target.unlink(missing_ok=True)
                from oa.core.envelope import ApiError

                raise ApiError(4400, "File exceeds 100MB", status=413)
            fh.write(chunk)
    rel = target.relative_to(workspace(chat.id).resolve()).as_posix()
    return ok({"path": rel, "name": name, "size": size, "mime": file.content_type,
               "url": f"/api/v1/chats/{chat.id}/files/raw?path={rel}"})


def _zip(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in TREE_SKIP]
            for f in files:
                p = Path(dirpath) / f
                if p.name.startswith(".env"):
                    continue
                z.write(p, p.relative_to(root).as_posix())
    return buf.getvalue()


@router.get("/chats/{chat_id}/files/export")
@router.get("/chats/{chat_id}/files/project-export")
async def export(chat: m.Chat = Depends(chat_guard("read"))):
    root = workspace(chat.id)
    if not root.exists():
        raise not_found("Workspace is empty")
    data = await asyncio.to_thread(_zip, root)
    fname = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in chat.title)[:60] or chat.id
    return StreamingResponse(io.BytesIO(data), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}.zip"'})
