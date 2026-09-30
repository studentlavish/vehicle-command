"""Plate persistence helper for the Vashu backend.

The YOLO + OCR pipeline runs on the showroom PC's `local_agent`
(see `/app/local_agent/plate_detector.py`).  When it sends a `plate_detected`
message on `/api/agent/ws` we:

    1. Look up the plate in `vehicle_masters` (owner enrichment).
    2. Decide whether this is an ENTRY or an EXIT based on the vehicle's
       most recent visit_session (`status == "active"` ⇒ EXIT, else ENTRY).
    3. Cooldown-dedup so a car sitting in front of the camera does NOT
       spam records (same event type + same plate within COOLDOWN_SECONDS
       is treated as a duplicate).
    4. Auto-create a `vehicle_masters` document for brand-new plates.
    5. Save the vehicle-crop snapshot and the plate-crop snapshot to disk.
    6. Insert a new visit_session (entry) or close the active one (exit).
    7. Broadcast on the event bus so the live dashboard updates instantly.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from event_bus import event_bus
import object_storage

logger = logging.getLogger("rdx.plate_pipeline")
logger.setLevel(logging.INFO)

# Where automatic snapshots are written on disk. Served via /api/snapshots/<file>.
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# Minimum seconds between two events (of the same type) for the same plate.
# Prevents the same car sitting in view from creating multiple sessions.
COOLDOWN_SECONDS = 30

# Safety guardrail: refuse to write new snapshots when the underlying disk
# has less than this many MB free. Prevents the pipeline from filling the
# volume to 100% (which would kill Mongo / uvicorn). Configurable via env.
SNAPSHOT_MIN_FREE_MB = int(os.environ.get("SNAPSHOT_MIN_FREE_MB", "500"))
_last_low_disk_log_at: float = 0.0


def snapshot_disk_stats() -> Dict[str, Any]:
    """Return REAL disk usage stats for the snapshots volume (no faked capacity)."""
    try:
        total, used, free = shutil.disk_usage(SNAPSHOTS_DIR)
    except Exception as e:
        return {"error": str(e)}
    snap_bytes = 0
    snap_files = 0
    try:
        with os.scandir(SNAPSHOTS_DIR) as it:
            for entry in it:
                if entry.is_file():
                    snap_bytes += entry.stat().st_size
                    snap_files += 1
    except Exception:
        pass
    return {
        "path": SNAPSHOTS_DIR,
        "disk_total_bytes": total,
        "disk_used_bytes": used,
        "disk_free_bytes": free,
        "snapshots_bytes": snap_bytes,
        "snapshots_count": snap_files,
        "min_free_mb_threshold": SNAPSHOT_MIN_FREE_MB,
        "healthy": free >= SNAPSHOT_MIN_FREE_MB * 1024 * 1024,
    }


async def _write_snapshot(kind: str, plate: str, jpeg: bytes) -> str:
    """Persist a JPEG — Emergent Object Storage first, local disk as fallback.

    `kind` is one of `entry|exit|plate_entry|plate_exit` and is used only
    for the file name so the folder stays flat and predictable while still
    being self-describing.

    Returns the stable `/api/snapshots/...` URL regardless of where the bytes
    are stored, so DB records, the frontend, and existing records never change.

    Local-disk writes still respect the 500 MB free-space guardrail — the
    pipeline never fills the volume to 100%.
    """
    if not jpeg:
        return ""
    safe = "".join(c for c in (plate or "UNKNOWN") if c.isalnum())[:16] or "UNKNOWN"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{safe}_{kind}_{uuid.uuid4().hex[:8]}.jpg"

    # Primary: Emergent Object Storage (off the small application volume).
    try:
        await object_storage.put_object(f"{object_storage.APP_PREFIX}/snapshots/{name}", jpeg, "image/jpeg")
        return f"/api/snapshots/{name}"
    except Exception as e:
        logger.warning(
            "[snapshot] object storage upload failed (%s: %s) — falling back to local disk for %s",
            type(e).__name__, e, name,
        )

    # Fallback: local disk with the free-space guardrail.
    try:
        free = shutil.disk_usage(SNAPSHOTS_DIR).free
        if free < SNAPSHOT_MIN_FREE_MB * 1024 * 1024:
            # Log at most once per minute to avoid log-spam under sustained pressure.
            import time as _t
            global _last_low_disk_log_at
            now = _t.time()
            if now - _last_low_disk_log_at > 60:
                _last_low_disk_log_at = now
                logger.warning(
                    "[snapshot] LOW DISK — free=%.0fMB threshold=%dMB — skipping write kind=%s plate=%s",
                    free / 1024 / 1024, SNAPSHOT_MIN_FREE_MB, kind, plate,
                )
            return ""
    except Exception:
        pass  # if we cannot stat the volume, still attempt the write
    path = os.path.join(SNAPSHOTS_DIR, name)
    try:
        with open(path, "wb") as f:
            f.write(jpeg)
    except Exception as e:
        logger.warning("[snapshot] write failed for %s (%s): %s", plate, kind, e)
        return ""
    return f"/api/snapshots/{name}"


def _master_public(master: dict) -> dict:
    return {
        "owner_name": master.get("owner_name") or "Unknown Owner",
        "phone_number": master.get("phone_number", ""),
        "customer_id": master.get("customer_id", ""),
        "vehicle_model": master.get("vehicle_model", ""),
        "vehicle_image": master.get("vehicle_image", ""),
    }


async def _persist_entry(
    db,
    camera_id: str,
    plate: str,
    jpeg_frame: bytes,
    plate_jpeg: Optional[bytes] = None,
    confidence: str = "medium",
) -> Dict[str, Any]:
    """Persist a plate detection sent from the local agent.

    Decides ENTRY vs EXIT based on the vehicle's active visit_session and
    returns a dict describing what happened. Backwards-compatible with the
    existing agent WS handler.
    """
    now = datetime.now(timezone.utc)
    plate = (plate or "").strip().upper()
    result: Dict[str, Any] = {"plate": plate, "camera_id": camera_id, "confidence": confidence}

    # 1. Master lookup / auto-create for new plates
    master = await db.vehicle_masters.find_one({"vehicle_number": plate})
    if master:
        result["owner_status"] = "existing"
    else:
        result["owner_status"] = "new"
        master = {
            "id": str(uuid.uuid4()),
            "vehicle_number": plate,
            "owner_name": "Unknown Owner",
            "phone_number": "",
            "vehicle_model": "",
            "vehicle_image": "",
            "customer_id": f"CUS-{secrets.token_hex(3).upper()}",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        try:
            await db.vehicle_masters.insert_one(master)
        except Exception:
            master = await db.vehicle_masters.find_one({"vehicle_number": plate}) or master
    result["owner"] = _master_public(master)

    # 2. Decide entry vs exit — is there an active session for this plate?
    active = await db.visit_sessions.find_one(
        {"vehicle_number": plate, "status": "active"},
        sort=[("entry_time", -1)],
    )
    is_exit = active is not None
    event_kind = "exit" if is_exit else "entry"

    # 3. Cooldown-dedup — same event type within COOLDOWN_SECONDS is treated
    #    as a duplicate detection (car still sitting in view).
    if is_exit:
        # If the active session was just opened, ignore instant exits.
        try:
            open_dt = datetime.fromisoformat(active["entry_time"])
            if (now - open_dt).total_seconds() < COOLDOWN_SECONDS:
                result.update({
                    "duplicate": True,
                    "event": "entry",
                    "duplicate_reason": f"active session opened within {COOLDOWN_SECONDS}s",
                    "session_id": active.get("id"),
                })
                return result
        except Exception:
            pass
    else:
        last = await db.visit_sessions.find_one(
            {"vehicle_number": plate},
            sort=[("entry_time", -1)],
        )
        if last and last.get("entry_time"):
            try:
                last_dt = datetime.fromisoformat(last["entry_time"])
                if (now - last_dt).total_seconds() < COOLDOWN_SECONDS:
                    result.update({
                        "duplicate": True,
                        "event": "entry",
                        "duplicate_reason": f"already logged within {COOLDOWN_SECONDS}s",
                        "session_id": last.get("id"),
                    })
                    return result
            except Exception:
                pass

    # 4. Write snapshots
    vehicle_snap = await _write_snapshot(f"{event_kind}", plate, jpeg_frame) if jpeg_frame else ""
    plate_snap = await _write_snapshot(f"plate_{event_kind}", plate, plate_jpeg) if plate_jpeg else ""

    if is_exit:
        # 5a. Close the active session as EXIT
        try:
            entry_dt = datetime.fromisoformat(active["entry_time"])
            duration = int((now - entry_dt).total_seconds())
        except Exception:
            duration = None
        update_doc = {
            "exit_time": now.isoformat(),
            "exit_camera": camera_id,
            "exit_image": vehicle_snap or active.get("exit_image", ""),
            "exit_plate_image": plate_snap or active.get("exit_plate_image", ""),
            "exit_confidence": confidence,
            "duration_seconds": duration,
            "status": "completed",
        }
        await db.visit_sessions.update_one({"id": active["id"]}, {"$set": update_doc})
        session_doc = {**active, **update_doc}
        session_doc.pop("_id", None)
        result.update({
            "duplicate": False,
            "event": "exit",
            "session": {
                "id": session_doc["id"],
                "vehicle_number": plate,
                "entry_time": session_doc.get("entry_time"),
                "exit_time": session_doc["exit_time"],
                "entry_camera": session_doc.get("entry_camera", ""),
                "exit_camera": session_doc["exit_camera"],
                "entry_image": session_doc.get("entry_image", ""),
                "exit_image": session_doc["exit_image"],
                "entry_plate_image": session_doc.get("entry_plate_image", ""),
                "exit_plate_image": session_doc["exit_plate_image"],
                "status": "completed",
                "duration_seconds": duration,
                "visit_date": session_doc.get("visit_date"),
                "detected_by": session_doc.get("detected_by", "agent_yolo"),
            },
        })
        event_type = "exit.recorded"
    else:
        # 5b. Insert a new ENTRY visit_session
        session_doc = {
            "id": str(uuid.uuid4()),
            "vehicle_number": plate,
            "entry_time": now.isoformat(),
            "exit_time": None,
            "visit_date": now.strftime("%Y-%m-%d"),
            "duration_seconds": None,
            "entry_camera": camera_id,
            "exit_camera": "",
            "entry_image": vehicle_snap,
            "exit_image": "",
            "entry_plate_image": plate_snap,
            "exit_plate_image": "",
            "entry_confidence": confidence,
            "status": "active",
            "notes": "",
            "created_at": now.isoformat(),
            "detected_by": "agent_yolo",
        }
        await db.visit_sessions.insert_one(session_doc)
        session_doc.pop("_id", None)
        result.update({
            "duplicate": False,
            "event": "entry",
            "session": {
                "id": session_doc["id"],
                "vehicle_number": plate,
                "entry_time": session_doc["entry_time"],
                "exit_time": None,
                "entry_camera": camera_id,
                "exit_camera": "",
                "entry_image": vehicle_snap,
                "exit_image": "",
                "entry_plate_image": plate_snap,
                "status": "active",
                "visit_date": session_doc["visit_date"],
                "detected_by": session_doc["detected_by"],
            },
        })
        event_type = "entry.recorded"

    # 6. Broadcast on the event bus (dashboard + LiveMonitoring listen here).
    event_bus.publish({
        "type": event_type,
        "camera_id": camera_id,
        "vehicle_number": plate,
        "owner_status": result["owner_status"],
        "owner": result["owner"],
        "session": result["session"],
        "recorded_at": now.isoformat(),
    })

    logger.info(
        "[EVENT] %s plate=%s camera=%s owner=%s duration=%s conf=%s",
        event_type,
        plate,
        camera_id,
        result["owner"]["owner_name"],
        result["session"].get("duration_seconds"),
        confidence,
    )
    return result
