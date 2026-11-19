"""MongoDB backup and restore — mongodump → gzip archive → storage.

  - `mongodump --archive --gzip` (MongoDB Database Tools must be installed;
    the Docker image includes them).
  - The archive is staged in a private temp folder and deleted right after
    the upload, success or failure.
  - Destination: the configured storage (see storage.py) under
    `backups/mongodb/<name>` — a bucket in production.
  - Metadata is recorded in the `backups` collection.
  - Retention: the latest BACKUP_KEEP successful backups are kept; older
    archives are deleted from storage and marked expired.
  - The Mongo URI is never logged; tool output is sanitized before logging.

Restore (tested monthly, see docs/runbook.md):
    python maintenance.py restore-backup <key> --into <database> --apply
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import storage

logger = logging.getLogger("rdx.backup")

BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
BACKUP_PREFIX = "backups/mongodb"


def _sanitize(text: str, secret: str) -> str:
    """Strip the Mongo URI (or any secret) from tool output before logging."""
    if not text:
        return ""
    return text.replace(secret, "***")


async def _run(args, secret: str) -> None:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} exited {proc.returncode}: {_sanitize(stderr.decode(errors='replace'), secret)[:400]}")


async def run_backup(db, mongo_url: str, db_name: str, run_id: str) -> Dict[str, Any]:
    """Run one full backup. Raises on failure; caller records/logs it."""
    if not shutil.which("mongodump"):
        raise RuntimeError("mongodump not found — install MongoDB Database Tools")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"mongodb_{db_name}_{ts}.archive.gz"
    stage = tempfile.mkdtemp(prefix="vc_backup_")
    tmp_path = os.path.join(stage, name)
    try:
        await _run(["mongodump", f"--uri={mongo_url}", f"--db={db_name}", f"--archive={tmp_path}", "--gzip"], mongo_url)
        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise RuntimeError("mongodump produced an empty archive")
        if data[:2] != b"\x1f\x8b":
            raise RuntimeError("archive failed gzip validation")

        key = f"{BACKUP_PREFIX}/{name}"
        store = storage.get()
        await store.put(key, data, "application/gzip")
        record = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "kind": "mongodb",
            "storage": store.name,
            "storage_path": key,
            "size_bytes": len(data),
            "status": "success",
            "expired": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.backups.insert_one(record)
        record.pop("_id", None)
        logger.info("[backup %s] success — %s (%d bytes) → %s", run_id, name, len(data), store.name)
        await _apply_retention(db, run_id)
        return record
    finally:
        shutil.rmtree(stage, ignore_errors=True)


async def _apply_retention(db, run_id: str) -> None:
    """Keep the newest BACKUP_KEEP successful backups; delete the rest from storage."""
    successful = await db.backups.find(
        {"kind": "mongodb", "status": "success", "expired": False}
    ).sort("created_at", -1).to_list(1000)
    store = storage.get()
    for old in successful[BACKUP_KEEP:]:
        path = old.get("storage_path", "")
        deleted = False
        if path.startswith(BACKUP_PREFIX + "/"):
            try:
                deleted = await store.delete(path)
            except Exception as e:
                logger.warning("[backup %s] could not delete old backup %s: %s", run_id, path, e)
        await db.backups.update_one({"id": old["id"]}, {"$set": {"expired": True, "deleted": deleted}})
        logger.info("[backup %s] expired old backup %s (deleted=%s)", run_id, path, deleted)


async def restore_backup(key: str, mongo_url: str, source_db: str, target_db: str) -> None:
    """Restore archive `key` from storage into `target_db` (renaming source_db.* → target_db.*).

    Restores into a *different* database by default so a test restore never
    touches live data. --drop replaces the target's collections.
    """
    if not shutil.which("mongorestore"):
        raise RuntimeError("mongorestore not found — install MongoDB Database Tools")
    data = await storage.get().get(key)
    if not data:
        raise RuntimeError(f"backup {key} not found in storage")
    stage = tempfile.mkdtemp(prefix="vc_restore_")
    path = os.path.join(stage, "restore.archive.gz")
    try:
        with open(path, "wb") as f:
            f.write(data)
        await _run(["mongorestore", f"--uri={mongo_url}", f"--archive={path}", "--gzip", "--drop",
                    f"--nsFrom={source_db}.*", f"--nsTo={target_db}.*"], mongo_url)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
