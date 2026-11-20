"""Health checks, snapshots and storage stats."""
import os
import json
import asyncio
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Response, Depends

import plate_pipeline
import storage as storage_mod


from core import ROLE_ADMIN_MANAGER, ROLE_ALL, db, logger, require_roles

router = APIRouter(prefix="/api")


@router.get("/health")
async def health():
    """Liveness: the process is up and serving (no dependencies checked)."""
    return {"ok": True}


@router.get("/ready")
async def ready():
    """Readiness: the database answers and snapshot storage works. 503 otherwise (for uptime monitors)."""
    checks: Dict[str, Any] = {}
    try:
        await asyncio.wait_for(db.command("ping"), timeout=3)
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    try:
        store = storage_mod.get()
        if store.name == "local":
            disk = plate_pipeline.snapshot_disk_stats()
            checks["storage"] = "ok" if disk.get("healthy") else "low disk"
        else:
            await asyncio.wait_for(store.list("health"), timeout=5)
            checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return Response(content=json.dumps({"ok": ok, "checks": checks}), media_type="application/json",
                    status_code=200 if ok else 503)


@router.get("/")
async def root():
    return {"service": "RDX Car Showroom API", "ok": True}


# ---------- Auto-detection snapshots ----------
from fastapi.responses import FileResponse
from plate_pipeline import SNAPSHOTS_DIR as _SNAPSHOTS_DIR
from plate_pipeline import snapshot_disk_stats as _snapshot_disk_stats


@router.get("/snapshots/{filename}")
async def get_snapshot(filename: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    safe = os.path.basename(filename)  # path-traversal guard
    fp = os.path.join(_SNAPSHOTS_DIR, safe)
    if os.path.isfile(fp):  # local disk — fast path
        return FileResponse(fp, media_type="image/jpeg")
    try:
        data = await plate_pipeline.read_snapshot(safe)
    except Exception as e:
        logger.warning("[snapshot] storage fetch failed for %s: %s", safe, e)
        raise HTTPException(status_code=502, detail="Snapshot storage unavailable")
    if data is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/storage/snapshots")
async def get_storage_stats(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Real (not hardcoded) disk-usage stats for the snapshot volume."""
    return _snapshot_disk_stats()


