"""Object storage abstraction (local dir in lite mode, S3/MinIO in full mode).

Key layout:
  chats/{chat_id}/.storage/{index}/{hash8}/{name}     block snapshots
  deploys/{chat_id}/{version}/...                      built static artifacts (dist)
  covers/{chat_id}/{version}.png
  uploads/{chat_id}/{uuid}/{name}
  apps/{chat_id}/{env}/{key}                           app-sdk storage
"""
from __future__ import annotations

import asyncio
import mimetypes
import shutil
from pathlib import Path
from typing import Protocol

from oa.config import settings


class Storage(Protocol):
    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    async def get_bytes(self, key: str) -> bytes | None: ...
    async def exists(self, key: str) -> bool: ...
    async def list_prefix(self, prefix: str) -> list[str]: ...
    async def delete_prefix(self, prefix: str) -> None: ...
    async def copy_prefix(self, src: str, dst: str) -> None: ...


def _safe(key: str) -> str:
    key = key.lstrip("/")
    if ".." in Path(key).parts:
        raise ValueError("invalid key")
    return key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        return self.root / _safe(key)

    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)

    async def get_bytes(self, key: str) -> bytes | None:
        p = self.path(key)
        if not p.is_file():
            return None
        return await asyncio.to_thread(p.read_bytes)

    async def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    async def list_prefix(self, prefix: str) -> list[str]:
        base = self.path(prefix)
        if not base.exists():
            return []
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()]

    async def delete_prefix(self, prefix: str) -> None:
        p = self.path(prefix)
        if p.is_dir():
            await asyncio.to_thread(shutil.rmtree, p, True)
        elif p.exists():
            p.unlink()

    async def copy_prefix(self, src: str, dst: str) -> None:
        s, d = self.path(src), self.path(dst)
        if s.is_dir():
            await asyncio.to_thread(shutil.copytree, s, d, dirs_exist_ok=True)


class S3Storage:
    def __init__(self) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        from botocore.config import Config

        self.c = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_key,
            aws_secret_access_key=settings.s3_secret,
            region_name="us-east-1",
            # every agent tool writes a snapshot; with tens of concurrent runs the default pool (10) thrashed
            config=Config(max_pool_connections=64, retries={"max_attempts": 6, "mode": "adaptive"},
                          connect_timeout=5, read_timeout=60),
        )
        try:
            self.c.head_bucket(Bucket=self.bucket)
        except Exception:
            self.c.create_bucket(Bucket=self.bucket)

    async def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        await asyncio.to_thread(self.c.put_object, Bucket=self.bucket, Key=_safe(key), Body=data, ContentType=ct)

    async def get_bytes(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            try:
                return self.c.get_object(Bucket=self.bucket, Key=_safe(key))["Body"].read()
            except self.c.exceptions.NoSuchKey:
                return None
            except Exception:
                return None

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _h() -> bool:
            try:
                self.c.head_object(Bucket=self.bucket, Key=_safe(key))
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_h)

    async def list_prefix(self, prefix: str) -> list[str]:
        def _l() -> list[str]:
            out: list[str] = []
            for page in self.c.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=_safe(prefix)):
                out += [o["Key"] for o in page.get("Contents", [])]
            return out

        return await asyncio.to_thread(_l)

    async def delete_prefix(self, prefix: str) -> None:
        for k in await self.list_prefix(prefix):
            await asyncio.to_thread(self.c.delete_object, Bucket=self.bucket, Key=k)

    async def copy_prefix(self, src: str, dst: str) -> None:
        for k in await self.list_prefix(src):
            nk = _safe(dst).rstrip("/") + "/" + k[len(_safe(src)):].lstrip("/")
            await asyncio.to_thread(
                self.c.copy_object, Bucket=self.bucket, Key=nk, CopySource={"Bucket": self.bucket, "Key": k}
            )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.storage == "s3" else LocalStorage(settings.storage_root)
    return _storage
