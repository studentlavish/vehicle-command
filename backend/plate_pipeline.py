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

import asyncio
import logging
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from event_bus import event_bus
import object_storage
import plate_rules

logger = logging.getLogger("rdx.plate_pipeline")
logger.setLevel(logging.INFO)

# Where automatic snapshots are written on disk. Served via /api/snapshots/<file>.
SNAPSHOTS_DIR = os.environ.get("SNAPSHOTS_DIR", "").strip() or os.path.join(os.path.dirname(__file__), "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# Minimum seconds between two events (of the same type) for the same plate.
# Prevents the same car sitting in view from creating multiple sessions.
COOLDOWN_SECONDS = 30

# A car must have been inside at least this long before a detection of the
# same plate counts as an EXIT. Stops a car parked in camera view from being
# flipped entry → exit → entry. Set per showroom via MIN_VISIT_SECONDS.
MIN_VISIT_SECONDS = int(os.environ.get("MIN_VISIT_SECONDS", "120"))

# Which automatic reads are saved without a human check:
#   strict (default) — "high" confidence, or "medium" for a vehicle we already know
#   relaxed          — "high" and "medium"
#   off              — everything that passes format validation (old behaviour)
REVIEW_MODE = os.environ.get("REVIEW_MODE", "strict").strip().lower()

APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))


def local_date(dt: datetime) -> str:
    """Calendar date in the showroom's timezone (YYYY-MM-DD)."""
    return dt.astimezone(APP_TZ).strftime("%Y-%m-%d")

# Process-wide lock serializing the read-check → write sequence in
# _persist_entry so simultaneous detections (e.g. multiple agent WS
# connections) can never create duplicate active sessions for one plate.
# (Single uvicorn worker deployment — an in-process lock is sufficient.)
_persist_lock = asyncio.Lock()

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

    # Primary: Emergent Object Storage (off the small application volume) —
    # skipped entirely when no key is configured (e.g. running locally).
    try:
        if not getattr(object_storage, "EMERGENT_KEY", ""):
            raise RuntimeError("object storage not configured")
        await object_storage.put_object(f"{object_storage.APP_PREFIX}/snapshots/{name}", jpeg, "image/jpeg")
        return f"/api/snapshots/{name}"
    except RuntimeError:
        pass
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
    at: Optional[datetime] = None,
    source: str = "agent_yolo",
) -> Dict[str, Any]:
    """Race-safe wrapper: serializes persistence under a process-wide lock so
    concurrent detections can never double-create an active session.

    `at` is when the vehicle was actually seen (used when a reviewer confirms
    a read later, so entry/exit times stay correct)."""
    async with _persist_lock:
        return await _persist_entry_locked(db, camera_id, plate, jpeg_frame, plate_jpeg, confidence, at, source)


async def _persist_entry_locked(
    db,
    camera_id: str,
    plate: str,
    jpeg_frame: bytes,
    plate_jpeg: Optional[bytes] = None,
    confidence: str = "medium",
    at: Optional[datetime] = None,
    source: str = "agent_yolo",
) -> Dict[str, Any]:
    """Persist a plate detection sent from the local agent.

    Decides ENTRY vs EXIT based on the vehicle's active visit_session and
    returns a dict describing what happened. Backwards-compatible with the
    existing agent WS handler.
    """
    now = at or datetime.now(timezone.utc)
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

    # 2b. Minimum visit time — a re-detection soon after ENTRY is the same car
    #     still in view, not an EXIT.
    if is_exit and MIN_VISIT_SECONDS > 0:
        try:
            inside_for = (now - datetime.fromisoformat(active["entry_time"])).total_seconds()
        except Exception:
            inside_for = MIN_VISIT_SECONDS
        if inside_for < MIN_VISIT_SECONDS:
            result.update({
                "duplicate": True,
                "event": "entry",
                "duplicate_reason": f"inside for {int(inside_for)}s (< {MIN_VISIT_SECONDS}s minimum visit)",
                "session_id": active.get("id"),
            })
            logger.info("[EVENT] ignored re-detection plate=%s camera=%s (%s)", plate, camera_id, result["duplicate_reason"])
            return result

    # 3. Cooldown-dedup. ROOT RULE — ACTIVE SESSION WINS: when an active
    #    session exists, the detection is ALWAYS an EXIT of that same session;
    #    the cooldown must never convert it into a duplicate/'entry' outcome.
    #    The cooldown only suppresses a brand-new ENTRY right after a completed EXIT.
    if not is_exit:
        last = await db.visit_sessions.find_one(
            {"vehicle_number": plate},
            sort=[("entry_time", -1)],
        )
        if last:
            try:
                # Duplicate suppression uses the most recent activity — exit_time
                # when the last session already closed, else entry_time — so a car
                # still sitting in view right after an EXIT cannot spawn a
                # duplicate ENTRY within the cooldown window.
                ref = last.get("exit_time") or last.get("entry_time")
                if ref:
                    ref_dt = datetime.fromisoformat(ref)
                    if (now - ref_dt).total_seconds() < COOLDOWN_SECONDS:
                        result.update({
                            "duplicate": True,
                            "event": "entry",
                            "duplicate_reason": f"last activity within {COOLDOWN_SECONDS}s",
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
            "visit_date": local_date(now),
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
            "detected_by": source,
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


# ===================== Detection routing + Review queue =====================

async def _known_plate(db, plate: str) -> bool:
    if await db.visit_sessions.find_one({"vehicle_number": plate, "status": "active"}, {"_id": 1}):
        return True
    m = await db.vehicle_masters.find_one({"vehicle_number": plate}, {"owner_name": 1})
    return bool(m and (m.get("owner_name") or "Unknown Owner") != "Unknown Owner")


async def _near_active_plates(db, plate: str, max_dist: int = 1) -> list:
    """Active sessions whose plate is within `max_dist` edits of `plate`."""
    out = []
    async for s in db.visit_sessions.find({"status": "active"}, {"vehicle_number": 1}):
        vn = s.get("vehicle_number", "")
        if vn != plate and plate_rules.distance(vn, plate) <= max_dist:
            out.append(vn)
    return sorted(set(out))


async def create_review(
    db, *, camera_id: str, plate: Optional[str], raw: str, confidence: str, reason: str,
    candidates: Optional[list] = None, suggestions: Optional[list] = None,
    jpeg_frame: bytes = b"", plate_jpeg: bytes = b"", reads: int = 0, support: float = 0.0,
    seen_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Queue an uncertain read for a person to confirm, correct or reject."""
    seen_at = seen_at or datetime.now(timezone.utc)
    label = plate or plate_rules.clean(raw) or "UNKNOWN"
    doc = {
        "id": str(uuid.uuid4()),
        "status": "pending",
        "camera_id": camera_id,
        "plate": plate,
        "raw": raw,
        "confidence": confidence,
        "reason": reason,
        "candidates": [c for c in (candidates or []) if c][:5],
        "suggestions": suggestions or [],
        "reads": reads,
        "support": support,
        "image": await _write_snapshot("review", label, jpeg_frame) if jpeg_frame else "",
        "plate_image": await _write_snapshot("plate_review", label, plate_jpeg) if plate_jpeg else "",
        "seen_at": seen_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.plate_reviews.insert_one(doc)
    doc.pop("_id", None)
    event_bus.publish({"type": "review.created", "review": doc})
    logger.info("[REVIEW] queued plate=%s raw=%s cam=%s conf=%s reason=%s", plate, raw, camera_id, confidence, reason)
    return doc


async def route_detection(
    db, *, camera_id: str, raw_plate: str, confidence: str = "medium",
    jpeg_frame: bytes = b"", plate_jpeg: bytes = b"",
    candidates: Optional[list] = None, reads: int = 0, support: float = 0.0,
) -> Dict[str, Any]:
    """Single entry point for automatic reads: validate, then save or queue for review."""
    seen_at = datetime.now(timezone.utc)
    plate, corrected = plate_rules.correct(raw_plate)
    common = dict(camera_id=camera_id, raw=raw_plate, confidence=confidence, candidates=candidates,
                  jpeg_frame=jpeg_frame, plate_jpeg=plate_jpeg, reads=reads, support=support, seen_at=seen_at)

    if not plate:
        review = await create_review(db, plate=None, reason="Doesn't match an Indian number-plate format", **common)
        return {"queued_for_review": True, "review_id": review["id"]}

    if REVIEW_MODE != "off":
        known = await _known_plate(db, plate)
        near = [] if known else await _near_active_plates(db, plate)
        auto = confidence == "high" or (confidence == "medium" and (known or REVIEW_MODE == "relaxed"))
        if near and confidence != "high":
            auto = False
        if not auto:
            if near:
                reason = f"One character away from {', '.join(near)} (currently inside)"
            elif confidence == "low":
                reason = "Too few matching reads to be sure"
            else:
                reason = "New vehicle read with medium confidence"
            review = await create_review(db, plate=plate, reason=reason, suggestions=near, **common)
            return {"queued_for_review": True, "review_id": review["id"]}

    result = await _persist_entry(db, camera_id, plate, jpeg_frame, plate_jpeg=plate_jpeg,
                                  confidence=confidence, at=seen_at)
    result["corrected"] = corrected
    return result
