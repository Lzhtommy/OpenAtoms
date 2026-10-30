"""Object storage + SQLite helper for scripts/backup.sh / restore.sh (uses the platform's storage abstraction,
so it works for both OA_STORAGE=local and OA_STORAGE=s3). Run with the server venv:

  services/server/.venv/bin/python scripts/backup_storage.py dump <out.tar>
  services/server/.venv/bin/python scripts/backup_storage.py load <in.tar>
  services/server/.venv/bin/python scripts/backup_storage.py sqlite-backup <out.db>
  services/server/.venv/bin/python scripts/backup_storage.py sqlite-restore <in.db>
  services/server/.venv/bin/python scripts/backup_storage.py summary
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/server"))

from oa.config import settings  # noqa: E402
from oa.core.storage import get_storage  # noqa: E402

PREFIXES = ("chats/", "deploys/", "covers/", "uploads/", "apps/")


async def dump(out: str) -> int:
    st = get_storage()
    n = 0
    with tarfile.open(out, "w:gz") as tar:
        for prefix in PREFIXES:
            for key in await st.list_prefix(prefix):
                data = await st.get_bytes(key)
                if data is None:
                    continue
                info = tarfile.TarInfo(key)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                n += 1
    return n


async def load(src: str) -> int:
    st = get_storage()
    n = 0
    with tarfile.open(src, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            await st.put_bytes(m.name, f.read())
            n += 1
    return n


def _sqlite_path() -> Path:
    url = settings.db_url
    if not url.startswith("sqlite"):
        raise SystemExit("not a sqlite deployment")
    return Path(url.split("///", 1)[1])


def sqlite_backup(out: str) -> None:
    src = sqlite3.connect(_sqlite_path())
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)  # online, consistent copy (WAL-safe)
    src.close()
    dst.close()


def sqlite_restore(src: str) -> None:
    target = _sqlite_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    s = sqlite3.connect(src)
    d = sqlite3.connect(target)
    with d:
        s.backup(d)
    s.close()
    d.close()


async def summary() -> dict:
    from sqlalchemy import func, select

    from oa.db import models as m
    from oa.db.session import SessionLocal

    out: dict = {}
    async with SessionLocal() as s:
        for name, model in (("users", m.User), ("chats", m.Chat), ("messages", m.Message), ("versions", m.Version),
                            ("deployments", m.Deployment), ("block_timelines", m.BlockTimeline)):
            out[name] = int((await s.execute(select(func.count()).select_from(model))).scalar() or 0)
    st = get_storage()
    out["objects"] = sum([len(await st.list_prefix(p)) for p in PREFIXES])
    return out


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "dump":
        print(asyncio.run(dump(sys.argv[2])))
    elif cmd == "load":
        print(asyncio.run(load(sys.argv[2])))
    elif cmd == "sqlite-backup":
        sqlite_backup(sys.argv[2])
    elif cmd == "sqlite-restore":
        sqlite_restore(sys.argv[2])
    elif cmd == "summary":
        print(json.dumps(asyncio.run(summary())))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
