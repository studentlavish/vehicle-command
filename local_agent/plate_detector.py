"""Local YOLO + Gemini plate detector for the Vashu Local Camera Agent.

Runs entirely on the showroom PC:
    JPEG frame  →  YOLOv8n  →  vehicle crop  →  Gemini Vision OCR  →  plate

The detected plate is then shipped to the cloud backend as a
`plate_detected` WebSocket message (with the crop JPEG) and the backend
handles persistence + dashboard broadcast.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("vashu_agent.detector")

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
    for line in txt.splitlines():
        cleaned = "".join(c for c in line if c.isalnum())
        if 6 <= len(cleaned) <= 12 and any(c.isdigit() for c in cleaned) and any(c.isalpha() for c in cleaned):
            return cleaned
    return None


@dataclass
class LocalDetection:
    plate: str
    confidence: str
    crop_jpeg: bytes
    vehicle_class: str
    vehicle_conf: float


class LocalPlateDetector:
    """Loads YOLOv8n once and runs detection + Gemini OCR on demand.

    Torch/ultralytics/emergentintegrations are all lazy-imported so simply
    importing this module does not force those deps to be installed.
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        from ultralytics import YOLO
        default = os.path.join(os.path.dirname(__file__), "yolov8n.pt")
        self._model = YOLO(model_path or default)
        log.info("[DETECTOR] YOLO loaded: %s", model_path or default)

    def detect_vehicles(self, frame_bgr: np.ndarray, min_conf: float = 0.35) -> List[dict]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        h, w = frame_bgr.shape[:2]
        scale = 1.0
        max_side = 640
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            small = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)))
        else:
            small = frame_bgr
        results = self._model.predict(small, verbose=False, imgsz=640, conf=min_conf)
        out: List[dict] = []
        if not results:
            return out
        r = results[0]
        if r.boxes is None:
            return out
        boxes = r.boxes
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            if cls_id not in VEHICLE_CLASS_IDS:
                continue
            conf = float(boxes.conf[i].item())
            x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[i].tolist()]
            if scale != 1.0:
                x1 /= scale; y1 /= scale; x2 /= scale; y2 /= scale
            x1 = max(0, int(x1)); y1 = max(0, int(y1))
            x2 = min(w - 1, int(x2)); y2 = min(h - 1, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            out.append({
                "bbox": (x1, y1, x2 - x1, y2 - y1),
                "cls_name": VEHICLE_CLASS_IDS[cls_id],
                "conf": conf,
                "area": (x2 - x1) * (y2 - y1),
            })
        out.sort(key=lambda d: d["area"], reverse=True)
        return out

    @staticmethod
    def crop(frame_bgr: np.ndarray, bbox: Tuple[int, int, int, int], pad: float = 0.05) -> np.ndarray:
        x, y, w, h = bbox
        H, W = frame_bgr.shape[:2]
        px = int(w * pad); py = int(h * pad)
        x1 = max(0, x - px); y1 = max(0, y - py)
        x2 = min(W, x + w + px); y2 = min(H, y + h + py)
        return frame_bgr[y1:y2, x1:x2].copy()

    @staticmethod
    def encode_jpeg(img: np.ndarray, quality: int = 82) -> Optional[bytes]:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return bytes(buf)

    async def read_plate(self, crop_bgr: np.ndarray) -> Optional[Tuple[str, str]]:
        """Return (plate, confidence) via Gemini Vision, or None."""
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not key:
            log.warning("[DETECTOR] EMERGENT_LLM_KEY not set — OCR skipped")
            return None
        jpeg = self.encode_jpeg(crop_bgr)
        if not jpeg:
            return None
        b64 = base64.b64encode(jpeg).decode("ascii")
        prompt = (
            "Read the license plate visible in this image of a vehicle. "
            "Return ONLY the plate as continuous alphanumeric characters, no spaces, no dashes. "
            "If no plate is visible or unclear, respond with UNKNOWN. Example: UP21AB1234"
        )
        chat = LlmChat(
            api_key=key,
            session_id=f"agent-plate-{int(time.time()*1000)}",
            system_message="You are a precise OCR engine. Output only the requested text.",
        ).with_model("gemini", "gemini-3-flash-preview")
        try:
            resp = await chat.send_message(UserMessage(text=prompt, file_contents=[ImageContent(image_base64=b64)]))
            raw = resp if isinstance(resp, str) else str(resp)
        except Exception as e:
            log.warning("[DETECTOR] OCR call failed: %s", e)
            return None
        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN" or not cleaned:
            return None
        plate = _extract_plate(cleaned) or "".join(c for c in cleaned if c.isalnum())
        if not plate or len(plate) < 5:
            return None
        conf = "high" if PLATE_RE.search(cleaned) else "medium"
        return plate, conf

    async def process_jpeg(self, jpeg: bytes) -> Optional[LocalDetection]:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None
        detections = self.detect_vehicles(frame)
        if not detections:
            return None
        top = detections[0]
        crop = self.crop(frame, top["bbox"])
        read = await self.read_plate(crop)
        if not read:
            return None
        plate, conf = read
        crop_jpeg = self.encode_jpeg(crop, quality=75) or jpeg
        return LocalDetection(
            plate=plate,
            confidence=conf,
            crop_jpeg=crop_jpeg,
            vehicle_class=top["cls_name"],
            vehicle_conf=top["conf"],
        )


_detector: Optional[LocalPlateDetector] = None


def get_detector() -> Optional[LocalPlateDetector]:
    """Lazily construct the singleton — returns None if YOLO/torch can't load."""
    global _detector
    if _detector is None:
        try:
            _detector = LocalPlateDetector()
        except Exception as e:
            log.warning("[DETECTOR] disabled — could not load YOLO: %s", e)
            return None
    return _detector
