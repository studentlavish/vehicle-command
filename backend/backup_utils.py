"""Automated MongoDB backup — mongodump → gzip → Emergent Object Storage.

Design:
  - Uses `mongodump --archive --gzip` (MongoDB Database Tools, already installed).
  - Archive is staged in /tmp (large scratch volume) and deleted immediately
    after a successful upload — backups are NEVER kept on the small /app disk.
  - Destination: Emergent Object Storage at `rdx-vashu/backups/mongodb/<name>`.
  - Metadata is tracked in the `backups` Mongo collection (additive — no
    existing schema touched).
  - Retention: keeps the latest BACKUP_KEEP successful backups; older ones are
    marked `expired=True` (soft-expire — the platform object store has no
    delete API, so bytes are retained but no longer listed as restorable).
  - The Mongo URI is never logged; stderr is sanitized before logging.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import object_storage

logger = logging.getLogger("rdx.backup")

BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))


def _sanitize(text: str, secret: str) -> str:
    """Strip the Mongo URI (or any secret) from tool output before logging."""
    if not text:
        return ""
    return text.replace(secret, "***")


async def run_backup(db, mongo_url: str, db_name: str, run_id: str) -> Dict[str, Any]:
    """Run one full backup. Raises on failure; caller records/logs it."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"mongodb_{db_name}_{ts}.archive.gz"
    tmp_path = f"/tmp/{run_id}_{name}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "mongodump", f"--uri={mongo_url}", f"--db={db_name}",
            f"--archive={tmp_path}", "--gzip",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"mongodump exited {proc.returncode}: {_sanitize(stderr.decode(errors='replace'), mongo_url)[:400]}")
        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise RuntimeError("mongodump produced an empty archive")
        # Sanity: valid gzip stream (magic bytes; full validation is done
        # separately via `mongorestore --dryRun` in the verification tests —
        # decompressing a truncated stream here would always fail).
        if data[:2] != b"\x1f\x8b":
            raise RuntimeError("archive failed gzip validation")

        path = f"{object_storage.APP_PREFIX}/backups/mongodb/{name}"
        result = await object_storage.put_object(path, data, "application/gzip")
        record = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "kind": "mongodb",
            "storage_path": result["path"],
            "size_bytes": result.get("size", len(data)),
            "status": "success",
            "expired": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.backups.insert_one(record)
        logger.info("[backup %s] success — %s (%d bytes) → object storage", run_id, name, record["size_bytes"])

        # Soft-retention: keep latest BACKUP_KEEP successful backups.
        successful = await db.backups.find(
            {"kind": "mongodb", "status": "success", "expired": False}
        ).sort("created_at", -1).to_list(1000)
        for old in successful[BACKUP_KEEP:]:
            await db.backups.update_one({"id": old["id"]}, {"$set": {"expired": True}})
            logger.info("[backup %s] marked old backup %s as expired (soft)", run_id, old.get("storage_path"))
        return record
    finally:
        # Staging file must never persist on disk, success or failure.
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
