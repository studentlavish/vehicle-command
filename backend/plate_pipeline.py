"""Plate persistence helper for the Vashu backend.

The YOLO + Gemini OCR pipeline has been moved to the showroom PC's
`local_agent` (see `/app/local_agent/plate_detector.py`). The backend keeps
only the ML-free persistence path used by the `/api/agent/ws` WebSocket:
when the local agent sends a `plate_detected` message we upsert the master,
dedup within 30s, write a visit_session with a snapshot, and broadcast the
result on the event bus.
"""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from event_bus import event_bus

logger = logging.getLogger("rdx.plate_pipeline")
logger.setLevel(logging.INFO)

# Where automatic entry snapshots are written on disk. Served via /api/snapshots/<file>.
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# Minimum seconds between two entries for the same plate. Anything shorter is
# treated as a duplicate detection and is NOT written to MongoDB.
ENTRY_DEDUP_SECONDS = 30


async def _persist_entry(db, camera_id: str, plate: str, jpeg_frame: bytes) -> Dict[str, Any]:
    """Persist a plate detection sent from the local agent.

    Steps:
      1. Master lookup (owner enrichment).
      2. Dedup within `ENTRY_DEDUP_SECONDS`.
      3. Auto-create master if brand-new plate.
      4. Save the snapshot JPEG to disk.
      5. Insert the visit_session row.
      6. Broadcast on the event bus so the live dashboard updates instantly.

    Returns a dict describing what happened.
    """
    now = datetime.now(timezone.utc)
    result: Dict[str, Any] = {"plate": plate, "camera_id": camera_id}

    # 1. Master lookup
    master = await db.vehicle_masters.find_one({"vehicle_number": plate})
    if master:
        result["owner_status"] = "existing"
        result["owner"] = {
            "owner_name": master.get("owner_name") or "Unknown Owner",
            "phone_number": master.get("phone_number", ""),
            "customer_id": master.get("customer_id", ""),
            "vehicle_model": master.get("vehicle_model", ""),
            "vehicle_image": master.get("vehicle_image", ""),
        }
    else:
        result["owner_status"] = "new"
        result["owner"] = None

    # 2. Dedup
    last = await db.visit_sessions.find_one(
        {"vehicle_number": plate},
        sort=[("entry_time", -1)],
    )
    if last and last.get("entry_time"):
        try:
            last_dt = datetime.fromisoformat(last["entry_time"])
            if (now - last_dt).total_seconds() < ENTRY_DEDUP_SECONDS:
                result["duplicate"] = True
                result["duplicate_reason"] = f"already logged within {ENTRY_DEDUP_SECONDS}s"
                result["last_session_id"] = last.get("id")
                return result
        except Exception:
            pass

    # 3. Auto-create master
    if master is None:
        new_master = {
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
            await db.vehicle_masters.insert_one(new_master)
        except Exception:
            new_master = await db.vehicle_masters.find_one({"vehicle_number": plate}) or new_master
        result["owner"] = {
            "owner_name": new_master.get("owner_name", "Unknown Owner"),
            "phone_number": new_master.get("phone_number", ""),
            "customer_id": new_master.get("customer_id", ""),
            "vehicle_model": new_master.get("vehicle_model", ""),
            "vehicle_image": new_master.get("vehicle_image", ""),
        }

    # 4. Save snapshot JPEG
    snapshot_url = ""
    if jpeg_frame:
        snap_name = f"{uuid.uuid4().hex}.jpg"
        snap_path = os.path.join(SNAPSHOTS_DIR, snap_name)
        try:
            with open(snap_path, "wb") as f:
                f.write(jpeg_frame)
            snapshot_url = f"/api/snapshots/{snap_name}"
        except Exception as e:
            logger.warning("Could not persist snapshot for %s: %s", plate, e)

    # 5. Insert visit_session
    session_doc = {
        "id": str(uuid.uuid4()),
        "vehicle_number": plate,
        "entry_time": now.isoformat(),
        "exit_time": None,
        "visit_date": now.strftime("%Y-%m-%d"),
        "duration_seconds": None,
        "entry_camera": camera_id,
        "exit_camera": "",
        "entry_image": snapshot_url,
        "exit_image": "",
        "status": "active",
        "notes": "",
        "created_at": now.isoformat(),
        "detected_by": "agent_yolo",
    }
    await db.visit_sessions.insert_one(session_doc)
    session_doc.pop("_id", None)

    result["duplicate"] = False
    result["session"] = {
        "id": session_doc["id"],
        "entry_time": session_doc["entry_time"],
        "entry_camera": session_doc["entry_camera"],
        "entry_image": snapshot_url,
        "status": session_doc["status"],
        "visit_date": session_doc["visit_date"],
        "detected_by": session_doc["detected_by"],
    }

    # 6. Broadcast
    event_bus.publish({
        "type": "entry.recorded",
        "camera_id": camera_id,
        "vehicle_number": plate,
        "owner_status": result["owner_status"],
        "owner": result["owner"],
        "session": {
            "id": session_doc["id"],
            "vehicle_number": plate,
            "entry_time": session_doc["entry_time"],
            "exit_time": None,
            "entry_camera": camera_id,
            "entry_image": snapshot_url,
            "status": "active",
            "visit_date": session_doc["visit_date"],
            "detected_by": session_doc["detected_by"],
        },
        "recorded_at": now.isoformat(),
    })
    return result
