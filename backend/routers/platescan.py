"""One-off plate reading from an uploaded photo."""
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from plate_pipeline import _persist_lock, local_date
import plate_rules
import local_ocr


from core import ROLE_ALL, db, logger, now_utc, require_roles, serialize_session
from routers.vehicles import normalize_plate, upsert_master

router = APIRouter(prefix="/api")

# ===================== AI Number Plate Recognition =====================
import re as _re

_PLATE_RE = _re.compile(r"[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}")


class PlateScanIn(BaseModel):
    image_base64: str  # data URL or plain base64
    auto_action: Optional[Literal["none", "entry", "exit"]] = "none"
    camera: Optional[str] = None


def _strip_data_url(b64: str) -> str:
    if "," in b64 and b64.strip().startswith("data:"):
        return b64.split(",", 1)[1]
    return b64


def _extract_plate(text: str) -> Optional[str]:
    if not text:
        return None
    txt = text.upper().replace("-", "").replace(".", " ")
    m = _PLATE_RE.search(txt)
    if m:
        return "".join(m.group(0).split())
    # Fallback: pick first token of >=6 alphanumerics
    for line in txt.splitlines():
        cleaned = "".join(c for c in line if c.isalnum())
        if 6 <= len(cleaned) <= 12 and any(c.isdigit() for c in cleaned) and any(c.isalpha() for c in cleaned):
            return cleaned
    return None


@router.post("/vehicles/scan-plate")
async def scan_plate(payload: PlateScanIn, user: dict = Depends(require_roles(*ROLE_ALL))):
    # Free offline readers on this server (see local_ocr.py).
    b64 = _strip_data_url(payload.image_base64).strip()
    if not b64:
        raise HTTPException(status_code=400, detail="image_base64 is required")

    try:
        try:
            local = await asyncio.to_thread(local_ocr.read_base64, b64)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        engine_note = local["engine"]
        if not local["plate"]:
            return {"plate": None, "confidence": "low", "raw": "", "action": None, "engine": engine_note,
                    "reason": "Found a plate but couldn't read a valid Indian number" if local["plate_found"]
                              else "No number plate found in the photo — try a closer, straighter shot"}
        raw = local["plate"]

        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN" or not cleaned:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None,
                    "reason": "No plate visible in the photo"}

        # Only accept text that is (or can be corrected into) a valid Indian plate.
        plate, corrected = plate_rules.extract(cleaned)
        if not plate:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None,
                    "reason": f"Read “{cleaned[:24]}”, which isn't a valid Indian number plate"}

        # Optional auto entry/exit
        action_result: Optional[Dict[str, Any]] = None
        action = payload.auto_action or "none"
        cam = payload.camera or "AI-CAM"
        if action == "entry":
            vn_entry = normalize_plate(plate)
            async with _persist_lock:
                # ACTIVE SESSION WINS — never insert a second active session.
                active = await db.visit_sessions.find_one({"vehicle_number": vn_entry, "status": "active"})
                if active:
                    action_result = {"type": "entry", "duplicate": True,
                                     "error": "Vehicle already has an active session",
                                     "session_id": active.get("id")}
                else:
                    await upsert_master(vehicle_number=plate)
                    now = now_utc()
                    session = {
                        "id": str(uuid.uuid4()),
                        "vehicle_number": vn_entry,
                        "entry_time": now.isoformat(),
                        "exit_time": None,
                        "visit_date": local_date(now),
                        "duration_seconds": None,
                        "entry_camera": cam,
                        "exit_camera": "",
                        "entry_image": "",
                        "exit_image": "",
                        "status": "active",
                        "created_at": now.isoformat(),
                    }
                    await db.visit_sessions.insert_one(session)
                    session.pop("_id", None)
                    action_result = {"type": "entry", "session": serialize_session(session)}
        elif action == "exit":
            vn = normalize_plate(plate)
            sess = await db.visit_sessions.find_one({"vehicle_number": vn, "status": "active"}, sort=[("entry_time", -1)])
            if sess:
                now = now_utc()
                entry_dt = datetime.fromisoformat(sess["entry_time"])
                duration = int((now - entry_dt).total_seconds())
                updates = {
                    "exit_time": now.isoformat(),
                    "duration_seconds": duration,
                    "exit_camera": cam,
                    "status": "completed",
                }
                await db.visit_sessions.update_one({"_id": sess["_id"]}, {"$set": updates})
                sess.update(updates)
                sess.pop("_id", None)
                action_result = {"type": "exit", "session": serialize_session(sess)}
            else:
                action_result = {"type": "exit", "error": "No active session to close for this plate"}

        # A single AI read of one photo is never "high" — corrected reads are flagged.
        # One photo is never "high". Readers disagreeing or characters corrected → "low".
        conf_label = "low" if (corrected or "≠" in engine_note) else "medium"
        return {"plate": plate, "confidence": conf_label, "corrected": corrected,
                "raw": raw, "action": action_result, "engine": engine_note}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Plate scan failed")
        raise HTTPException(status_code=500, detail=f"AI scan failed: {e}")


