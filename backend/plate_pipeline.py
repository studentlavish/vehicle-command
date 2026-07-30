"""
Automatic license-plate pipeline:

    Live Camera → YOLO Vehicle Detection → Crop Vehicle → Gemini Vision OCR → Plate Number

Runs entirely inside the backend. The current frame stored on each `CameraState`
by `CameraManager` is periodically pulled here, YOLO extracts the largest
vehicle bounding box, the crop is base64-encoded and sent to Gemini for plate
OCR, and the result is written back on the CameraState so the websocket layer
can broadcast it.

Environment:
    EMERGENT_LLM_KEY   (required — used for the vision OCR call)

Model:
    yolov8n.pt (~6 MB) — auto-downloaded to /app/backend/ on first import.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("rdx.plate_pipeline")
logger.setLevel(logging.INFO)

# Where automatic entry snapshots are written on disk. Served via /api/snapshots/<file>.
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# Minimum seconds between two entries for the same plate. Anything shorter is
# treated as a duplicate detection and is NOT written to MongoDB.
ENTRY_DEDUP_SECONDS = 30

# COCO vehicle classes (car, motorcycle, bus, truck)
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
PLATE_RE = re.compile(r"[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}")


def _extract_plate(raw: str) -> Optional[str]:
    if not raw:
        return None
    txt = raw.upper().replace("-", "").replace(".", " ")
    m = PLATE_RE.search(txt)
    if m:
        return "".join(m.group(0).split())
    # Fallback: pick first alphanumeric token of 6..12 chars mixing letters+digits
    for line in txt.splitlines():
        cleaned = "".join(c for c in line if c.isalnum())
        if 6 <= len(cleaned) <= 12 and any(c.isdigit() for c in cleaned) and any(c.isalpha() for c in cleaned):
            return cleaned
    return None


@dataclass
class PlateDetection:
    plate: str
    confidence: str  # "high" | "medium" | "low"
    bbox: Tuple[int, int, int, int]  # x, y, w, h of vehicle in frame
    vehicle_class: str
    vehicle_conf: float
    detected_at: str
    frame_no: int


class PlatePipeline:
    """Loads YOLOv8n once and runs detection + OCR on demand."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        # Lazy import so importing this module doesn't drag in torch until needed
        from ultralytics import YOLO
        default_path = os.path.join(os.path.dirname(__file__), "yolov8n.pt")
        self._model = YOLO(model_path or default_path)
        logger.info("YOLO loaded: %s", model_path or default_path)

    # ---------- YOLO ----------
    def detect_vehicles(self, frame_bgr: np.ndarray, min_conf: float = 0.35) -> List[Dict[str, Any]]:
        """Return list of {bbox, cls, cls_name, conf} for vehicle detections, sorted by area desc."""
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        # Downscale for speed on CPU
        h, w = frame_bgr.shape[:2]
        scale = 1.0
        max_side = 640
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            small = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)))
        else:
            small = frame_bgr

        results = self._model.predict(small, verbose=False, imgsz=640, conf=min_conf)
        detections: List[Dict[str, Any]] = []
        if not results:
            return detections
        r = results[0]
        if r.boxes is None:
            return detections
        boxes = r.boxes
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            if cls_id not in VEHICLE_CLASS_IDS:
                continue
            conf = float(boxes.conf[i].item())
            x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[i].tolist()]
            # Scale back to original frame coords
            if scale != 1.0:
                x1 /= scale; y1 /= scale; x2 /= scale; y2 /= scale
            x1 = max(0, int(x1)); y1 = max(0, int(y1))
            x2 = min(w - 1, int(x2)); y2 = min(h - 1, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "cls": cls_id,
                "cls_name": VEHICLE_CLASS_IDS[cls_id],
                "conf": conf,
                "area": (x2 - x1) * (y2 - y1),
            })
        detections.sort(key=lambda d: d["area"], reverse=True)
        return detections

    @staticmethod
    def crop(frame_bgr: np.ndarray, bbox: Tuple[int, int, int, int], pad: float = 0.05) -> np.ndarray:
        """Crop the vehicle with a small padding around its bbox."""
        x, y, w, h = bbox
        H, W = frame_bgr.shape[:2]
        px = int(w * pad); py = int(h * pad)
        x1 = max(0, x - px); y1 = max(0, y - py)
        x2 = min(W, x + w + px); y2 = min(H, y + h + py)
        return frame_bgr[y1:y2, x1:x2].copy()

    @staticmethod
    def encode_jpeg_b64(img: np.ndarray, quality: int = 82) -> Optional[str]:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return base64.b64encode(bytes(buf)).decode("ascii")

    # ---------- OCR via Gemini Vision ----------
    async def read_plate(self, crop_bgr: np.ndarray) -> Optional[Tuple[str, str, str]]:
        """Return (plate, confidence, raw) or None if not readable."""
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not key:
            logger.warning("EMERGENT_LLM_KEY not set — OCR skipped")
            return None
        b64 = self.encode_jpeg_b64(crop_bgr)
        if not b64:
            return None
        prompt = (
            "Read the license plate visible in this image of a vehicle. "
            "Return ONLY the plate as continuous alphanumeric characters, no spaces, no dashes. "
            "If no plate is visible or unclear, respond with UNKNOWN. Example: UP21AB1234"
        )
        chat = LlmChat(
            api_key=key,
            session_id=f"auto-plate-{int(time.time()*1000)}",
            system_message="You are a precise OCR engine. Output only the requested text.",
        ).with_model("gemini", "gemini-3-flash-preview")
        try:
            resp = await chat.send_message(UserMessage(text=prompt, file_contents=[ImageContent(image_base64=b64)]))
            raw = resp if isinstance(resp, str) else str(resp)
        except Exception as e:
            logger.warning("OCR call failed: %s", e)
            return None
        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN" or not cleaned:
            return None
        plate = _extract_plate(cleaned) or "".join(c for c in cleaned if c.isalnum())
        if not plate or len(plate) < 5:
            return None
        conf = "high" if PLATE_RE.search(cleaned) else "medium"
        return plate, conf, raw

    # ---------- Full pipeline for a single frame ----------
    async def process_frame(self, frame_bgr: np.ndarray, frame_no: int = 0) -> Optional[PlateDetection]:
        detections = self.detect_vehicles(frame_bgr)
        if not detections:
            return None
        top = detections[0]
        crop = self.crop(frame_bgr, top["bbox"])
        read = await self.read_plate(crop)
        if not read:
            return None
        plate, conf, _raw = read
        return PlateDetection(
            plate=plate,
            confidence=conf,
            bbox=top["bbox"],
            vehicle_class=top["cls_name"],
            vehicle_conf=top["conf"],
            detected_at=datetime.now(timezone.utc).isoformat(),
            frame_no=frame_no,
        )


# Module-level singleton — lazily instantiated to avoid loading torch at import time
_pipeline: Optional[PlatePipeline] = None


def get_pipeline() -> Optional[PlatePipeline]:
    global _pipeline
    if _pipeline is None:
        try:
            _pipeline = PlatePipeline()
        except Exception as e:
            logger.exception("Failed to initialise plate pipeline: %s", e)
            return None
    return _pipeline


async def _persist_entry(db, camera_id: str, plate: str, jpeg_frame: bytes) -> Dict[str, Any]:
    """
    Look up the vehicle master, dedup within ENTRY_DEDUP_SECONDS, and if it's a
    fresh detection write a new visit_session with the snapshot.

    Returns a dict describing what happened, always including `owner` (may be null
    if the vehicle is new) and either a `session` block (persisted) or a `duplicate`
    flag.
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

    # 2. Dedup: has this plate been recorded in the last ENTRY_DEDUP_SECONDS?
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

    # 3. Auto-create master if this is a brand-new plate
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
            # unique-index race: another worker inserted first
            new_master = await db.vehicle_masters.find_one({"vehicle_number": plate}) or new_master
        result["owner"] = {
            "owner_name": new_master.get("owner_name", "Unknown Owner"),
            "phone_number": new_master.get("phone_number", ""),
            "customer_id": new_master.get("customer_id", ""),
            "vehicle_model": new_master.get("vehicle_model", ""),
            "vehicle_image": new_master.get("vehicle_image", ""),
        }

    # 4. Save snapshot JPEG to disk (served by /api/snapshots/<file>)
    snap_name = f"{uuid.uuid4().hex}.jpg"
    snap_path = os.path.join(SNAPSHOTS_DIR, snap_name)
    try:
        with open(snap_path, "wb") as f:
            f.write(jpeg_frame)
        snapshot_url = f"/api/snapshots/{snap_name}"
    except Exception as e:
        logger.warning("Could not persist snapshot for %s: %s", plate, e)
        snapshot_url = ""

    # 5. Insert the visit session
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
        "detected_by": "yolo_auto",
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
        "detected_by": "yolo_auto",
    }
    return result


async def start_auto_detection_loop(camera_manager, db=None, interval_seconds: float = 3.0) -> None:
    """Background task: for every camera that is online, run the pipeline every N seconds
    and stash the result on `state.latest_plate` so the websocket layer can broadcast it.

    If `db` (an AsyncIOMotorDatabase) is provided, each fresh detection is also written
    to the visit_sessions collection (with owner enrichment + 30-second dedup).
    """
    pipeline = get_pipeline()
    if pipeline is None:
        logger.error("Auto-detection loop cannot start — pipeline unavailable")
        return
    logger.info(
        "Auto-detection loop started (interval=%.1fs, dedup=%ds, persistence=%s)",
        interval_seconds, ENTRY_DEDUP_SECONDS, "on" if db is not None else "off",
    )

    last_run: Dict[str, float] = {}
    while True:
        try:
            snapshot = camera_manager.list()
            for cam_id, info in snapshot.items():
                if info.get("status") != "online":
                    continue
                if time.time() - last_run.get(cam_id, 0) < interval_seconds:
                    continue
                state = camera_manager.get(cam_id)
                if state is None or state.last_frame_jpeg is None:
                    continue
                jpeg = state.last_frame_jpeg
                frame_no = state.frames_captured
                last_run[cam_id] = time.time()

                def _decode(buf: bytes) -> Optional[np.ndarray]:
                    arr = np.frombuffer(buf, dtype=np.uint8)
                    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

                frame = await asyncio.to_thread(_decode, jpeg)
                if frame is None:
                    continue
                detections = await asyncio.to_thread(pipeline.detect_vehicles, frame)
                if not detections:
                    continue
                top = detections[0]
                crop = pipeline.crop(frame, top["bbox"])
                read = await pipeline.read_plate(crop)
                if not read:
                    continue
                plate, conf, _raw = read

                latest: Dict[str, Any] = {
                    "plate": plate,
                    "confidence": conf,
                    "bbox": list(top["bbox"]),
                    "vehicle_class": top["cls_name"],
                    "vehicle_conf": round(top["conf"], 3),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "frame_no": frame_no,
                    "camera_id": cam_id,
                }

                # Persist entry + owner enrichment
                if db is not None:
                    try:
                        persistence = await _persist_entry(db, cam_id, plate, jpeg)
                        latest["owner"] = persistence.get("owner")
                        latest["owner_status"] = persistence.get("owner_status")
                        latest["duplicate"] = persistence.get("duplicate", False)
                        if persistence.get("duplicate"):
                            latest["duplicate_reason"] = persistence.get("duplicate_reason")
                            latest["last_session_id"] = persistence.get("last_session_id")
                        if persistence.get("session"):
                            latest["session"] = persistence["session"]
                    except Exception:
                        logger.exception("Persistence step failed for plate=%s", plate)

                prev = getattr(state, "latest_plate", None)
                is_new = (
                    prev is None
                    or prev.get("plate") != plate
                    or (
                        datetime.fromisoformat(latest["detected_at"]).timestamp()
                        - datetime.fromisoformat(prev["detected_at"]).timestamp()
                    ) > 30
                )
                state.latest_plate = latest
                state.latest_plate_is_new = is_new
                logger.info(
                    "[camera %s] AUTO plate=%s conf=%s vehicle=%s(%.2f) owner=%s dup=%s session=%s",
                    cam_id, plate, conf, top["cls_name"], top["conf"],
                    (latest.get("owner") or {}).get("owner_name", "—"),
                    latest.get("duplicate", False),
                    (latest.get("session") or {}).get("id", "—")[:8] if latest.get("session") else "—",
                )
        except Exception as e:
            logger.exception("Auto-detection loop error: %s", e)
        await asyncio.sleep(1.0)
