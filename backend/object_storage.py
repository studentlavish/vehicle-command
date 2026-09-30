"""Emergent Object Storage adapter.

Thin async wrapper over the platform-managed object storage API. Used to keep
vehicle/plate snapshots off the small application volume. All paths are
prefixed with the app name (`rdx-vashu/`) to avoid bucket collisions.

Credentials come from the environment only:
  - INTEGRATION_PROXY_URL (set by the platform; falls back to the default host)
  - EMERGENT_LLM_KEY (backend/.env)
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import httpx

logger = logging.getLogger("rdx.objstore")

STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

APP_PREFIX = "rdx-vashu"

# Session-scoped storage key — init once, reuse globally (per platform playbook).
_storage_key: Optional[str] = None


class ObjectStorageUnavailable(RuntimeError):
    """Raised when object storage cannot be used (missing creds / unreachable)."""


async def init_storage(force: bool = False) -> str:
    """Mint (or reuse) the session-scoped storage key."""
    global _storage_key
    if _storage_key and not force:
        return _storage_key
    if not EMERGENT_KEY:
        raise ObjectStorageUnavailable("EMERGENT_LLM_KEY is not configured")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY})
        resp.raise_for_status()
        _storage_key = resp.json()["storage_key"]
    return _storage_key


async def put_object(path: str, data: bytes, content_type: str) -> dict:
    """Upload bytes to object storage. One forced re-init retry on stale key."""
    key = await init_storage()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.put(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            data=data,
        )
        if resp.status_code in (403, 404):
            # Possibly a dead session key — force re-init and retry once.
            key = await init_storage(force=True)
            resp = await client.put(
                f"{STORAGE_URL}/objects/{path}",
                headers={"X-Storage-Key": key, "Content-Type": content_type},
                data=data,
            )
        resp.raise_for_status()
        return resp.json()


async def get_object_or_none(path: str) -> Tuple[Optional[bytes], str]:
    """Download bytes from object storage. Returns (None, "") when the object
    does not exist. Raises on other failures (auth/quota/network)."""
    key = await init_storage()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
        if resp.status_code == 404:
            # Could be unknown key vs missing object — force re-init and retry once.
            key = await init_storage(force=True)
            resp = await client.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key})
            if resp.status_code == 404:
                return None, ""
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "application/octet-stream")
