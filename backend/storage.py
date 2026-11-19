"""File storage for snapshots and backups.

STORAGE_BACKEND
    local (default)  files on this server's disk — snapshots in SNAPSHOTS_DIR,
                     everything else (backups) under STORAGE_DIR
    s3               any S3-compatible bucket: AWS S3, Cloudflare R2, MinIO …
                     S3_BUCKET, S3_REGION, S3_ENDPOINT_URL (R2/MinIO),
                     S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_PREFIX

Keys look like "snapshots/<file>.jpg" or "backups/mongodb/<file>.archive.gz".
Unlike the old Emergent store, both backends can delete, so retention really
frees space.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("rdx.storage")

_HERE = Path(__file__).resolve().parent


class LocalStorage:
    name = "local"

    def __init__(self, root: str, snapshots_dir: str) -> None:
        self.root = Path(root)
        self.snapshots_dir = Path(snapshots_dir)

    def _path(self, key: str) -> Path:
        key = key.strip("/")
        if ".." in Path(key).parts:
            raise ValueError("invalid storage key")
        if key.startswith("snapshots/"):
            return self.snapshots_dir / os.path.basename(key)
        return self.root / key

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    async def get(self, key: str) -> Optional[bytes]:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    async def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.is_file():
            p.unlink()
            return True
        return False

    async def list(self, prefix: str) -> List[str]:
        base = self._path(prefix.rstrip("/") + "/x").parent
        if not base.is_dir():
            return []
        rel = prefix.rstrip("/")
        return sorted(f"{rel}/{f.name}" for f in base.iterdir() if f.is_file())


class S3Storage:
    name = "s3"

    def __init__(self, client, bucket: str, prefix: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def _key(self, key: str) -> str:
        key = key.strip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=self._key(key), Body=data,
                                ContentType=content_type)

    async def get(self, key: str) -> Optional[bytes]:
        def _get():
            try:
                return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
            except self.client.exceptions.NoSuchKey:
                return None
        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> bool:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))
        return True

    async def list(self, prefix: str) -> List[str]:
        def _list():
            out, token = [], None
            while True:
                kw = {"Bucket": self.bucket, "Prefix": self._key(prefix.rstrip("/") + "/")}
                if token:
                    kw["ContinuationToken"] = token
                r = self.client.list_objects_v2(**kw)
                strip = len(self.prefix) + 1 if self.prefix else 0
                out += [o["Key"][strip:] for o in r.get("Contents", [])]
                if not r.get("IsTruncated"):
                    return sorted(out)
                token = r.get("NextContinuationToken")
        return await asyncio.to_thread(_list)


def from_env():
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower() or "local"
    snapshots_dir = os.environ.get("SNAPSHOTS_DIR", "").strip() or str(_HERE / "snapshots")
    if backend == "local":
        return LocalStorage(os.environ.get("STORAGE_DIR", "").strip() or str(_HERE / "storage"), snapshots_dir)
    if backend == "s3":
        import boto3
        client = boto3.client(
            "s3",
            region_name=os.environ.get("S3_REGION") or None,
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY") or None,
        )
        return S3Storage(client, os.environ["S3_BUCKET"], os.environ.get("S3_PREFIX", "vehicle-command"))
    raise RuntimeError(f"Unknown STORAGE_BACKEND={backend!r} (use local or s3)")


_storage = None


def get():
    """The configured storage (created on first use)."""
    global _storage
    if _storage is None:
        _storage = from_env()
        logger.info("[storage] backend=%s", _storage.name)
    return _storage


def use(storage) -> None:
    """Replace the storage in use (e.g. after changing configuration)."""
    global _storage
    _storage = storage
