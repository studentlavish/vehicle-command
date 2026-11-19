"""Plate persistence helper for the Vashu backend.

The YOLO + OCR pipeline runs on the showroom PC's `local_agent`
(see `/app/local_agent/plate_detector.py`).  When it sends a `plate_detected`
message on `/api/agent/ws` we:

    1. Look up the plate in `vehicle_masters` (owner enrichment).
    2. Decide ENTRY vs EXIT from the camera's role (entry / exit / both —
       see _persist_entry_locked) and the vehicle's open visit.
    3. Ignore re-detections of a car still standing in view.
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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from zoneinfo import ZoneInfo

from event_bus import event_bus
import object_storage
import plate_rules
import storage

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
    """Persist a JPEG and return its stable `/api/snapshots/<file>` URL.

    `kind` is one of `entry|exit|plate_entry|plate_exit|review|plate_review`
    and is used only for the file name.

    With STORAGE_BACKEND=s3 the image goes to the bucket, falling back to local
    disk if the upload fails. Local-disk writes respect the SNAPSHOT_MIN_FREE_MB
    guardrail, so the pipeline never fills the volume.
    """
    if not jpeg:
        return ""
    safe = "".join(c for c in (plate or "UNKNOWN") if c.isalnum())[:16] or "UNKNOWN"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{safe}_{kind}_{uuid.uuid4().hex[:8]}.jpg"

    store = storage.get()
    if store.name != "local":
        try:
            await store.put(f"snapshots/{name}", jpeg, "image/jpeg")
            return f"/api/snapshots/{name}"
        except Exception as e:
            logger.warning("[snapshot] %s upload failed (%s: %s) — falling back to local disk for %s",
                           store.name, type(e).__name__, e, name)

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


async def read_snapshot(name: str) -> Optional[bytes]:
    """Snapshot bytes: local disk → configured storage → legacy Emergent store (old records)."""
    name = os.path.basename(name or "")
    if not name:
        return None
    fp = os.path.join(SNAPSHOTS_DIR, name)
    if os.path.isfile(fp):
        with open(fp, "rb") as f:
            return f.read()
    store = storage.get()
    if store.name != "local":
        data = await store.get(f"snapshots/{name}")
        if data is not None:
            return data
    if getattr(object_storage, "EMERGENT_KEY", ""):
        data, _ = await object_storage.get_object_or_none(f"{object_storage.APP_PREFIX}/snapshots/{name}")
        return data
    return None


async def delete_snapshot(name: str) -> bool:
    """Remove a snapshot wherever it lives (local disk and/or bucket)."""
    name = os.path.basename(name or "")
    removed = False
    fp = os.path.join(SNAPSHOTS_DIR, name)
    if name and os.path.isfile(fp):
        os.remove(fp)
        removed = True
    store = storage.get()
    if name and store.name != "local":
        removed = await store.delete(f"snapshots/{name}") or removed
    return removed


def _master_public(master: dict) -> dict:
    return {
        "owner_name": master.get("owner_name") or "Unknown Owner",
        "phone_number": master.get("phone_number", ""),
        "customer_id": master.get("customer_id", ""),
        "vehicle_model": master.get("vehicle_model", ""),
        "vehicle_image": master.get("vehicle_image", ""),
    }


async def camera_settings(db, camera_id: str) -> Dict[str, Any]:
    """Role of a camera: entry | exit | both. Unknown cameras act as 'both'."""
    cid = (camera_id or "").strip().upper()
    doc = await db.camera_config.find_one({"camera_id": cid}, {"_id": 0}) if cid else None
    if not doc:
        if cid and cid not in _warned_unknown_cameras:
            _warned_unknown_cameras.add(cid)
            logger.warning("[camera %s] no role configured — treating as entry+exit (set it in Settings → Cameras)", cid)
        return {"camera_id": cid, "role": "both", "enabled": True, "configured": False}
    return {"camera_id": cid, "role": doc.get("role", "both"), "enabled": doc.get("enabled", True), "configured": True}


_warned_unknown_cameras: set = set()


async def _persist_entry(
    db,
    camera_id: str,
    plate: str,
    jpeg_frame: bytes,
    plate_jpeg: Optional[bytes] = None,
    confidence: str = "medium",
    at: Optional[datetime] = None,
    source: str = "agent_yolo",
    confirmed: bool = False,
) -> Dict[str, Any]:
    """Race-safe wrapper: serializes persistence under a process-wide lock.

    `at` is when the vehicle was actually seen (used when a reviewer confirms
    a read later, so entry/exit times stay correct). The partial unique index
    on active sessions (see ensure_session_indexes) also protects against
    duplicates across several server workers."""
    async with _persist_lock:
        return await _persist_entry_locked(db, camera_id, plate, jpeg_frame, plate_jpeg, confidence, at, source,
                                           confirmed)


def _secs_since(now: datetime, iso: Optional[str]) -> Optional[float]:
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() if iso else None
    except Exception:
        return None


async def _decide(db, role: str, plate: str, now: datetime, confirmed: bool) -> Dict[str, Any]:
    """Work out what a sighting means. Pure decision — writes nothing.

    Returns {"action": ..., ...} where action is one of
      entry              open a new visit
      exit               close `session` (matched exactly or by one character)
      exit_missed_entry  close `session` as exit_missed, then open a new visit
      exit_unmatched     record an exit with no known entry (entry_missed)
      duplicate          ignore (same car still in view)
      review             ask a person (reason + suggestions)
    """
    active = await db.visit_sessions.find_one({"vehicle_number": plate, "status": "active"}, sort=[("entry_time", -1)])

    if role == "exit":
        if active:
            return {"action": "exit", "session": active, "matched_by": "exact"}
        near = [] if confirmed else await _near_active_plates(db, plate)
        if len(near) == 1:
            session = await db.visit_sessions.find_one({"vehicle_number": near[0], "status": "active"})
            if session:
                return {"action": "exit", "session": session, "matched_by": "fuzzy"}
        if len(near) > 1:
            return {"action": "review", "reason": f"Exit read is one character away from several cars inside ({', '.join(near)})",
                    "suggestions": near}
        if confirmed:
            return {"action": "exit_unmatched"}
        return {"action": "review", "reason": "Exit seen, but this vehicle has no open entry", "suggestions": []}

    if role == "entry":
        if active:
            inside_for = _secs_since(now, active.get("entry_time"))
            if inside_for is not None and inside_for < MIN_VISIT_SECONDS:
                return {"action": "duplicate", "session": active,
                        "reason": f"entered {int(inside_for)}s ago (< {MIN_VISIT_SECONDS}s) — same car still at the entry camera"}
            return {"action": "exit_missed_entry", "session": active}
        return await _entry_or_cooldown(db, plate, now)

    # role == "both": one camera sees both directions — toggle on the open visit.
    if active:
        inside_for = _secs_since(now, active.get("entry_time"))
        if MIN_VISIT_SECONDS > 0 and inside_for is not None and inside_for < MIN_VISIT_SECONDS:
            return {"action": "duplicate", "session": active,
                    "reason": f"inside for {int(inside_for)}s (< {MIN_VISIT_SECONDS}s minimum visit)"}
        return {"action": "exit", "session": active, "matched_by": "exact"}
    return await _entry_or_cooldown(db, plate, now)


async def _entry_or_cooldown(db, plate: str, now: datetime) -> Dict[str, Any]:
    """A new ENTRY — unless this plate's last visit ended moments ago (still in view)."""
    last = await db.visit_sessions.find_one({"vehicle_number": plate}, sort=[("created_at", -1)])
    if last:
        since = _secs_since(now, last.get("exit_time") or last.get("entry_time"))
        if since is not None and since < COOLDOWN_SECONDS:
            return {"action": "duplicate", "session": last, "reason": f"last activity within {COOLDOWN_SECONDS}s"}
    return {"action": "entry"}


async def _get_or_create_master(db, plate: str, now: datetime) -> Tuple[dict, str]:
    master = await db.vehicle_masters.find_one({"vehicle_number": plate})
    if master:
        return master, "existing"
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
    return master, "new"


def _session_public(doc: dict) -> dict:
    keys = ("id", "vehicle_number", "entry_time", "exit_time", "entry_camera", "exit_camera", "entry_image",
            "exit_image", "entry_plate_image", "exit_plate_image", "status", "duration_seconds", "visit_date",
            "detected_by", "exit_matched_by", "exit_plate_read")
    return {k: doc.get(k) for k in keys if k in doc}


async def _persist_entry_locked(
    db,
    camera_id: str,
    plate: str,
    jpeg_frame: bytes,
    plate_jpeg: Optional[bytes] = None,
    confidence: str = "medium",
    at: Optional[datetime] = None,
    source: str = "agent_yolo",
    confirmed: bool = False,
) -> Dict[str, Any]:
    """Record one plate sighting. ENTRY vs EXIT comes from the camera's role:

    * entry camera — opens a visit. If the car still has an open visit (its
      exit was never seen) that visit is closed as `exit_missed` first.
    * exit camera  — closes the car's open visit, or the single open visit one
      character away (OCR slip; recorded as exit_matched_by="fuzzy"). Never
      opens a visit; an exit with no open visit goes to Plate Review (or, once
      a person confirms it, is stored as `entry_missed`).
      `confirmed=True` (a person typed the plate in Plate Review) means the plate
      is taken exactly as given: no one-character matching.
    * both         — one camera for both directions: toggles on the open visit,
      with a minimum visit time so a car standing in view isn't flipped.
    """
    now = at or datetime.now(timezone.utc)
    plate = (plate or "").strip().upper()
    cam = await camera_settings(db, camera_id)
    result: Dict[str, Any] = {"plate": plate, "camera_id": cam["camera_id"] or camera_id,
                              "confidence": confidence, "camera_role": cam["role"]}
    if not cam["enabled"]:
        result.update({"ignored": True, "duplicate": True, "event": None, "duplicate_reason": "camera disabled"})
        return result

    decision = await _decide(db, cam["role"], plate, now, confirmed)
    action = decision["action"]

    if action == "review":
        result.update({"needs_review": True, "review_reason": decision["reason"],
                       "suggestions": decision.get("suggestions", [])})
        return result
    if action == "duplicate":
        s = decision.get("session") or {}
        result.update({"duplicate": True, "event": "exit" if s.get("status") == "completed" else "entry",
                       "duplicate_reason": decision["reason"], "session_id": s.get("id")})
        logger.info("[EVENT] ignored re-detection plate=%s camera=%s (%s)", plate, camera_id, decision["reason"])
        return result

    target_plate = decision["session"]["vehicle_number"] if action == "exit" else plate
    master, owner_status = await _get_or_create_master(db, target_plate, now)
    result["plate"] = target_plate
    result["owner_status"] = owner_status
    result["owner"] = _master_public(master)

    is_exit = action in ("exit", "exit_unmatched")
    kind = "exit" if is_exit else "entry"
    vehicle_snap = await _write_snapshot(kind, target_plate, jpeg_frame) if jpeg_frame else ""
    plate_snap = await _write_snapshot(f"plate_{kind}", target_plate, plate_jpeg) if plate_jpeg else ""

    if action == "exit":
        active = decision["session"]
        duration = None
        since = _secs_since(now, active.get("entry_time"))
        if since is not None:
            duration = int(since)
        update_doc = {
            "exit_time": now.isoformat(),
            "exit_camera": camera_id,
            "exit_image": vehicle_snap or active.get("exit_image", ""),
            "exit_plate_image": plate_snap or active.get("exit_plate_image", ""),
            "exit_confidence": confidence,
            "exit_matched_by": decision.get("matched_by", "exact"),
            "duration_seconds": duration,
            "status": "completed",
        }
        if decision.get("matched_by") == "fuzzy":
            update_doc["exit_plate_read"] = plate
        r = await db.visit_sessions.update_one({"id": active["id"], "status": "active"}, {"$set": update_doc})
        if r.modified_count == 0:  # closed by someone else in the meantime
            result.update({"duplicate": True, "event": "exit", "duplicate_reason": "visit already closed",
                           "session_id": active["id"]})
            return result
        session_doc = {**active, **update_doc}
        event_type = "exit.recorded"
    elif action == "exit_unmatched":
        session_doc = {
            "id": str(uuid.uuid4()), "vehicle_number": target_plate,
            "entry_time": None, "exit_time": now.isoformat(), "visit_date": local_date(now),
            "duration_seconds": None, "entry_camera": "", "exit_camera": camera_id,
            "entry_image": "", "exit_image": vehicle_snap, "entry_plate_image": "", "exit_plate_image": plate_snap,
            "exit_confidence": confidence, "status": "entry_missed", "notes": "Exit recorded without a matching entry",
            "created_at": now.isoformat(), "detected_by": source,
        }
        await db.visit_sessions.insert_one(session_doc)
        event_type = "exit.recorded"
    else:
        if action == "exit_missed_entry":
            stale = decision["session"]
            await db.visit_sessions.update_one({"id": stale["id"], "status": "active"}, {"$set": {
                "status": "exit_missed", "exit_missed_at": now.isoformat(),
                "notes": ((stale.get("notes") or "") + " Exit not seen — closed when the car entered again.").strip(),
            }})
            result["closed_exit_missed"] = stale["id"]
            logger.warning("[EVENT] exit_missed plate=%s session=%s (seen entering again)", target_plate, stale["id"])
        session_doc = {
            "id": str(uuid.uuid4()),
            "vehicle_number": target_plate,
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
        try:
            await db.visit_sessions.insert_one(session_doc)
        except DuplicateKeyError:
            existing = await db.visit_sessions.find_one({"vehicle_number": target_plate, "status": "active"}, {"id": 1})
            result.update({"duplicate": True, "event": "entry", "duplicate_reason": "already inside",
                           "session_id": (existing or {}).get("id")})
            return result
        event_type = "entry.recorded"

    session_doc.pop("_id", None)
    result.update({"duplicate": False, "event": "exit" if is_exit else "entry", "session": _session_public(session_doc)})
    if decision.get("matched_by") == "fuzzy":
        result["read_plate"] = plate

    event_bus.publish({
        "type": event_type,
        "camera_id": camera_id,
        "vehicle_number": target_plate,
        "owner_status": owner_status,
        "owner": result["owner"],
        "session": result["session"],
        "recorded_at": now.isoformat(),
    })
    logger.info("[EVENT] %s plate=%s camera=%s role=%s owner=%s duration=%s conf=%s%s",
                event_type, target_plate, camera_id, cam["role"], result["owner"]["owner_name"],
                session_doc.get("duration_seconds"), confidence,
                f" (read {plate}, fuzzy match)" if decision.get("matched_by") == "fuzzy" else "")
    return result


async def ensure_session_indexes(db) -> None:
    """At most one open visit per plate, enforced by MongoDB itself."""
    await db.camera_config.create_index("camera_id", unique=True)
    # Agent detections are processed once per event_id (re-sends are answered from here).
    await db.processed_events.create_index("event_id", unique=True)
    await db.processed_events.create_index("at", expireAfterSeconds=7 * 24 * 3600)
    try:
        await db.visit_sessions.create_index(
            "vehicle_number", name="one_active_per_plate", unique=True,
            partialFilterExpression={"status": "active"},
        )
    except Exception as e:
        logger.error("[sessions] could not create the one-active-visit-per-plate index — some plates have several "
                     "open visits. Run `python maintenance.py close-duplicate-active --apply`. (%s)", e)


async def long_open_sessions(db, hours: float) -> list:
    """Visits still open after `hours` (cars possibly inside overnight, or a missed exit)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out = []
    async for s in db.visit_sessions.find({"status": "active", "entry_time": {"$lt": cutoff}},
                                          {"_id": 0}).sort("entry_time", 1):
        out.append(_session_public(s))
    return out


# ===================== Detection routing + Review queue =====================

async def _known_plate(db, plate: str) -> bool:
    """A vehicle we already trust: currently inside, on the known-plates list, or with a named owner."""
    if await db.visit_sessions.find_one({"vehicle_number": plate, "status": "active"}, {"_id": 1}):
        return True
    if await db.known_plates.find_one({"plate": plate}, {"_id": 1}):
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
    seen_at: Optional[datetime] = None, kind: str = "plate",
) -> Dict[str, Any]:
    """Queue an uncertain read for a person to confirm, correct or reject.

    kind: "plate" (an uncertain read) or "no_plate" (a vehicle without a readable plate)."""
    seen_at = seen_at or datetime.now(timezone.utc)
    label = plate or plate_rules.clean(raw) or "UNKNOWN"
    doc = {
        "id": str(uuid.uuid4()),
        "status": "pending",
        "kind": kind,
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
    seen_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Single entry point for automatic reads: validate, then save or queue for review.

    `seen_at` is when the camera saw the car (the agent's timestamp), so a
    detection delivered late — after a network outage — keeps its real time."""
    seen_at = seen_at or datetime.now(timezone.utc)
    common = dict(camera_id=camera_id, raw=raw_plate, confidence=confidence, candidates=candidates,
                  jpeg_frame=jpeg_frame, plate_jpeg=plate_jpeg, reads=reads, support=support, seen_at=seen_at)

    if not plate_rules.clean(raw_plate):
        review = await create_review(db, plate=None, kind="no_plate",
                                     reason="Vehicle seen without a readable number plate (new, unregistered or covered)",
                                     **common)
        return {"queued_for_review": True, "review_id": review["id"]}

    plate, swaps, risky, _ = plate_rules.correct_detail(raw_plate)
    corrected = swaps > 0
    if risky and confidence == "high":
        # Only valid after a risky look-alike swap (A→4, T→7 …) — don't trust it fully.
        confidence = "medium"
        common["confidence"] = confidence
    if not plate:
        # Trade / temporary plates don't follow the standard format; accept them if registered.
        cleaned = plate_rules.clean(raw_plate)
        if await db.known_plates.find_one({"plate": cleaned}, {"_id": 1}):
            plate = cleaned

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
    if result.get("needs_review"):
        review = await create_review(db, plate=plate, reason=result["review_reason"],
                                     suggestions=result.get("suggestions") or [], **common)
        return {"queued_for_review": True, "review_id": review["id"], "plate": plate}
    result["corrected"] = corrected
    return result
