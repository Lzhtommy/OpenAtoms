"""OpenAtoms Cloud: per-app document tables, dev/prod (ARCHITECTURE §11, db_mode=double).

lite: one SQLite file per app env at data/chats/{chat}/cloud_{env}.db
full: one Postgres schema per app env, app_{chat}_{env}, on the main database.

Each entity is a table `t_{name}(id, data JSON-as-text, owner_id, created_at, updated_at)`; declared columns live
in `_oa_tables`. Rows are exposed as {id, **data, owner_id, created_at, updated_at}.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from oa.config import settings

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
ENVS = ("dev", "prod")
RESERVED = {"id", "owner_id", "created_at", "updated_at"}

_engines: dict[str, AsyncEngine] = {}


class CloudError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise CloudError(f"invalid table name: {name!r}")
    return name


def check_env(env: str) -> str:
    if env not in ENVS:
        raise CloudError(f"invalid env: {env!r}")
    return env


class CloudDB:
    def __init__(self, chat_id: str, env: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", chat_id):
            raise CloudError("invalid chat id")
        self.chat_id, self.env = chat_id, check_env(env)
        self.pg = not settings.db_url.startswith("sqlite")
        self.schema = f"app_{chat_id}_{env}"

    @property
    def engine(self) -> AsyncEngine:
        if self.pg:
            from oa.db.session import engine

            return engine
        key = f"{self.chat_id}:{self.env}"
        if key not in _engines:
            path = settings.chat_dir(self.chat_id)
            path.mkdir(parents=True, exist_ok=True)
            _engines[key] = create_async_engine(f"sqlite+aiosqlite:///{path / f'cloud_{self.env}.db'}")
        return _engines[key]

    def t(self, name: str) -> str:
        n = f't_{check_name(name)}'
        return f'"{self.schema}"."{n}"' if self.pg else f'"{n}"'

    @property
    def meta(self) -> str:
        return f'"{self.schema}"."_oa_tables"' if self.pg else '"_oa_tables"'

    async def ensure(self) -> None:
        async with self.engine.begin() as c:
            if self.pg:
                await c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
            await c.execute(text(f"CREATE TABLE IF NOT EXISTS {self.meta} (name TEXT PRIMARY KEY, columns TEXT NOT NULL)"))

    async def exists(self) -> bool:
        if not self.pg:
            return (settings.chat_dir(self.chat_id) / f"cloud_{self.env}.db").exists()
        async with self.engine.connect() as c:
            r = await c.execute(text("SELECT 1 FROM information_schema.schemata WHERE schema_name=:s"), {"s": self.schema})
            return r.first() is not None

    async def create_table(self, name: str, columns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        await self.ensure()
        cols = [{"name": c["name"], "type": c.get("type", "text")} for c in (columns or []) if c.get("name") not in RESERVED]
        pk = "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        async with self.engine.begin() as c:
            await c.execute(text(
                f"CREATE TABLE IF NOT EXISTS {self.t(name)} (id {pk}, data TEXT NOT NULL, owner_id BIGINT, "
                "created_at TEXT, updated_at TEXT)"
            ))
            existing = (await c.execute(text(f"SELECT columns FROM {self.meta} WHERE name=:n"), {"n": name})).first()
            if existing:
                merged = json.loads(existing[0])
                known = {x["name"] for x in merged}
                merged += [x for x in cols if x["name"] not in known]
                await c.execute(text(f"UPDATE {self.meta} SET columns=:c WHERE name=:n"), {"c": json.dumps(merged), "n": name})
                cols = merged
            else:
                await c.execute(text(f"INSERT INTO {self.meta} (name, columns) VALUES (:n, :c)"), {"n": name, "c": json.dumps(cols)})
        return {"name": name, "columns": cols}

    async def table_names(self) -> list[str]:
        if not await self.exists():
            return []
        await self.ensure()
        async with self.engine.connect() as c:
            return [r[0] for r in (await c.execute(text(f"SELECT name FROM {self.meta} ORDER BY name"))).all()]

    async def has_table(self, name: str) -> bool:
        return check_name(name) in await self.table_names()

    async def schemas(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not await self.exists():
            return out
        await self.ensure()
        async with self.engine.connect() as c:
            for name, cols in (await c.execute(text(f"SELECT name, columns FROM {self.meta} ORDER BY name"))).all():
                count = (await c.execute(text(f"SELECT COUNT(*) FROM {self.t(name)}"))).scalar() or 0
                columns = [{"name": "id", "type": "integer"}, *json.loads(cols)]
                known = {x["name"] for x in columns}
                sample = (await c.execute(text(f"SELECT data FROM {self.t(name)} ORDER BY id DESC LIMIT 20"))).all()
                for (d,) in sample:
                    for k, v in json.loads(d).items():
                        if k not in known:
                            known.add(k)
                            columns.append({"name": k, "type": _infer(v)})
                columns += [{"name": "owner_id", "type": "integer"}, {"name": "created_at", "type": "timestamp"},
                            {"name": "updated_at", "type": "timestamp"}]
                out.append({"name": name, "columns": columns, "row_count": int(count)})
        return out

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        data = json.loads(r.data)
        return {"id": r.id, **data, "owner_id": r.owner_id, "created_at": r.created_at, "updated_at": r.updated_at}

    async def all_rows(self, table: str) -> list[dict[str, Any]]:
        if not await self.has_table(table):
            return []
        async with self.engine.connect() as c:
            res = await c.execute(text(f"SELECT id, data, owner_id, created_at, updated_at FROM {self.t(table)} ORDER BY id"))
            return [self._row(r) for r in res.all()]

    async def query(self, table: str, query: dict[str, Any] | None = None, sort: str | None = None,
                    limit: int | None = None, skip: int = 0) -> list[dict[str, Any]]:
        rows = [r for r in await self.all_rows(table) if matches(r, query or {})]
        if sort:
            for key in reversed([k.strip() for k in sort.split(",") if k.strip()]):
                desc = key.startswith("-")
                k = key.lstrip("-+")
                rows.sort(key=lambda r: (r.get(k) is None, _sortable(r.get(k))), reverse=desc)
        rows = rows[skip:]
        return rows[:limit] if limit else rows

    async def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        if not await self.has_table(table):
            return None
        async with self.engine.connect() as c:
            r = (await c.execute(text(f"SELECT id, data, owner_id, created_at, updated_at FROM {self.t(table)} WHERE id=:i"),
                                 {"i": int(row_id)})).first()
            return self._row(r) if r else None

    async def insert(self, table: str, rows: list[dict[str, Any]], owner_id: int | None = None) -> list[dict[str, Any]]:
        if not await self.has_table(table):
            await self.create_table(table)
        out = []
        async with self.engine.begin() as c:
            for row in rows:
                data = {k: v for k, v in row.items() if k not in RESERVED}
                now = _now()
                params = {"d": json.dumps(data, default=str), "o": owner_id, "c": now, "u": now}
                sql = f"INSERT INTO {self.t(table)} (data, owner_id, created_at, updated_at) VALUES (:d, :o, :c, :u)"
                if self.pg:
                    rid = (await c.execute(text(sql + " RETURNING id"), params)).scalar()
                else:
                    rid = (await c.execute(text(sql), params)).lastrowid
                out.append({"id": rid, **data, "owner_id": owner_id, "created_at": now, "updated_at": now})
        return out

    async def update(self, table: str, row_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
        cur = await self.get(table, row_id)
        if cur is None:
            return None
        data = {k: v for k, v in cur.items() if k not in RESERVED}
        data.update({k: v for k, v in patch.items() if k not in RESERVED})
        now = _now()
        async with self.engine.begin() as c:
            await c.execute(text(f"UPDATE {self.t(table)} SET data=:d, updated_at=:u WHERE id=:i"),
                            {"d": json.dumps(data, default=str), "u": now, "i": int(row_id)})
        return {**cur, **data, "updated_at": now}

    async def delete(self, table: str, row_id: int) -> bool:
        if not await self.has_table(table):
            return False
        async with self.engine.begin() as c:
            res = await c.execute(text(f"DELETE FROM {self.t(table)} WHERE id=:i"), {"i": int(row_id)})
            return (res.rowcount or 0) > 0


def _infer(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, (dict, list)):
        return "json"
    return "text"


def _sortable(v: Any) -> Any:
    return v if isinstance(v, (int, float)) else str(v)


def _op(op: str, v: Any, arg: Any) -> bool:
    try:
        if op == "$eq":
            return v == arg
        if op == "$ne":
            return v != arg
        if op == "$in":
            return v in arg
        if op == "$nin":
            return v not in arg
        if op == "$contains":
            return v is not None and str(arg).lower() in str(v).lower()
        if v is None:
            return False
        if op == "$gt":
            return v > arg
        if op == "$gte":
            return v >= arg
        if op == "$lt":
            return v < arg
        if op == "$lte":
            return v <= arg
    except TypeError:
        return False
    return False


def matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    """Subset of Mongo-style operators: equality, $eq $ne $gt $gte $lt $lte $in $nin $contains."""
    for k, cond in query.items():
        v = row.get(k)
        if isinstance(cond, dict) and any(str(x).startswith("$") for x in cond):
            if not all(_op(op, v, arg) for op, arg in cond.items()):
                return False
        elif v != cond:
            return False
    return True


# ------------------------------------------------------------------ contract functions (§10.3)
async def ensure_cloud(chat_id: str) -> None:
    for env in ENVS:
        await CloudDB(chat_id, env).ensure()


async def create_table(chat_id: str, env: str, name: str, columns: list[dict]) -> dict:
    return await CloudDB(chat_id, env).create_table(name, columns)


async def get_schemas(chat_id: str, env: str) -> list[dict]:
    return await CloudDB(chat_id, env).schemas()


async def insert_rows(chat_id: str, env: str, table: str, rows: list[dict]) -> int:
    return len(await CloudDB(chat_id, env).insert(table, rows))
