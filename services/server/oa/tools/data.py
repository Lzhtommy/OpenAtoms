"""Notebook (dataanalyst_write_and_exec_code), Backend (backendmanager_*) and deployer tools."""
from __future__ import annotations

import json
from typing import Any

from oa.tools.base import ToolContext, ToolResult, tool, truncate_for_llm

MAX_IMAGE_B64 = 1_500_000


@tool(
    "dataanalyst_write_and_exec_code",
    "Execute Python code in a persistent Jupyter-like kernel inside the sandbox (state is kept between calls). "
    "print() results; matplotlib figures are captured automatically.",
    {"code": {"type": "string"}},
    block_type="Notebook",
)
async def dataanalyst_write_and_exec_code(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    code = str(args["code"])
    cells: list[dict[str, Any]] = ctx.rc.state.setdefault("notebook_cells", [])
    cell: dict[str, Any] = {"source": code, "outputs": []}
    async with ctx.record("dataanalyst_write_and_exec_code", "Notebook") as blk:
        await blk.replace(json.dumps({"cells": [*cells, cell]}, ensure_ascii=False))
        sb = await ctx.rc.sandbox()
        res = await sb.kernel_execute(code, timeout_ms=180_000)
        texts: list[str] = []
        for o in res.get("outputs", []):
            t = o.get("type")
            if t == "stream":
                cell["outputs"].append({"type": "stream", "text": o.get("text", "")})
                texts.append(o.get("text", ""))
            elif t == "error":
                tb = o.get("traceback")
                tb = "\n".join(tb) if isinstance(tb, list) else (tb or "")
                txt = f"{o.get('ename', 'Error')}: {o.get('evalue', '')}\n{tb}".strip()
                cell["outputs"].append({"type": "error", "text": txt})
                texts.append(txt)
            elif t == "image":
                data = o.get("data", "")
                cell["outputs"].append({"type": "image", "mime": o.get("mime", "image/png"),
                                        "data": data if len(data) <= MAX_IMAGE_B64 else ""})
                texts.append("[image output]")
        ok = bool(res.get("ok", True)) and not any(o["type"] == "error" for o in cell["outputs"])
        cells.append(cell)
        doc = json.dumps({"cells": cells}, ensure_ascii=False)
        await blk.replace(doc)
        await blk.complete(status="success" if ok else "failed", snapshots={"code.ipynb": doc})
    out = "".join(texts).strip() or "(no output)"
    masked, _ = ctx.rc.mask(out)
    return ToolResult(truncate_for_llm(masked, 6000), ok=ok)


async def _mark_cloud_enabled(chat_id: str) -> None:
    """The agent provisioning tables is the same as the user pressing "Enable Cloud": record it on the project."""
    from oa.core.events import bus
    from oa.db.models import Chat
    from oa.db.session import session_scope

    async with session_scope() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None or (chat.config or {}).get("cloud_enabled"):
            return
        chat.config = {**(chat.config or {}), "cloud_enabled": True, "db_mode": "double"}
        cfg = dict(chat.config)
    await bus.emit(chat_id, "chat:update", {"chat": {"config": cfg}})


async def _backend_block(ctx: ToolContext, command: str, action: str, payload: dict[str, Any], call) -> ToolResult:
    async with ctx.record(command, "Backend", extra={"action": action}) as blk:
        try:
            from oa.domain import cloud

            await cloud.ensure_cloud(ctx.chat_id)
            await _mark_cloud_enabled(ctx.chat_id)
            result = await call(cloud)
            ok = True
        except Exception as e:
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        doc = json.dumps({"action": action, **payload, "result": result}, ensure_ascii=False, default=str)
        await blk.replace(doc)
        await blk.complete(status="success" if ok else "failed", snapshots={"backend.json": doc})
    return ToolResult(truncate_for_llm(json.dumps(result, ensure_ascii=False, default=str), 6000), ok=ok)


@tool(
    "backendmanager_create_table",
    "Create (or extend) a table in the app's OpenAtoms Cloud database (dev environment). The app reads/writes it "
    "with `oa.entities.<Table>` from src/lib/oa.ts.",
    {
        "table_name": {"type": "string", "description": "PascalCase entity name, e.g. Post"},
        "columns": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "description": "text|integer|number|boolean|json|timestamp"}},
            "required": ["name"]}},
    },
    block_type="Backend",
)
async def backendmanager_create_table(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    name, cols = str(args["table_name"]), list(args.get("columns") or [])
    return await _backend_block(ctx, "backendmanager_create_table", "create_table", {"tables": [name], "columns": cols},
                                lambda c: c.create_table(ctx.chat_id, "dev", name, cols))


@tool("backendmanager_get_schemas", "List the tables and columns of the app's Cloud database (dev).", {},
      block_type="Backend")
async def backendmanager_get_schemas(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return await _backend_block(ctx, "backendmanager_get_schemas", "get_schemas", {},
                                lambda c: c.get_schemas(ctx.chat_id, "dev"))


@tool(
    "backendmanager_insert_table_data",
    "Insert seed rows into a Cloud table (dev).",
    {"table_name": {"type": "string"}, "rows": {"type": "array", "items": {"type": "object"}}},
    block_type="Backend",
)
async def backendmanager_insert_table_data(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    name, rows = str(args["table_name"]), list(args.get("rows") or [])

    async def call(c):
        return {"inserted": await c.insert_rows(ctx.chat_id, "dev", name, rows)}

    return await _backend_block(ctx, "backendmanager_insert_table_data", "insert", {"tables": [name]}, call)


@tool("deployer_deploy_to_public",
      "Publish the current project: create a version (build) and point the public URL at it (always latest).", {})
async def deployer_deploy_to_public(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from oa.config import settings
    from oa.core.events import bus
    from oa.db import models as m
    from oa.db.session import session_scope
    from oa.domain import serializers as ser
    from oa.domain.deployments import deploy_url
    from oa.domain.versions import create_version

    v = await create_version(ctx.chat_id, ctx.rc.state.get("summary") or "Published by Alex", ctx.rc.run_id)
    if v.status != "ready":
        return ToolResult(f"Build for {v.version} failed; fix the build before deploying.", ok=False)
    ctx.rc.state["version_created"] = v.version
    async with session_scope() as s:
        chat = await s.get(m.Chat, ctx.chat_id)
        dep = await s.get(m.Deployment, ctx.chat_id)
        if dep is None:
            dep = m.Deployment(chat_id=ctx.chat_id, version=v.version, seq=1)
            s.add(dep)
        elif dep.status == "deleted":
            dep.seq = (dep.seq or 0) + 1
        dep.version, dep.is_always_latest, dep.kind, dep.status = v.version, True, "static", "success"
        dep.url = deploy_url(chat, dep)
        if chat.access_mode == "private":
            chat.access_mode = "public"
        out, url = ser.deployment(dep), dep.url
        mode = chat.access_mode
    await bus.emit(ctx.chat_id, "deploy:update", {"deployment": out})
    await bus.emit(ctx.chat_id, "chat:update", {"chat": {"uris": {"dev": settings.preview_url(ctx.chat_id), "prod": url},
                                                         "access_mode": mode}})
    return ToolResult(f"Published {v.version} at {url}")
