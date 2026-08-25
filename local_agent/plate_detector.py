"""Local vehicle-plate detector: YOLOv8n + (EasyOCR ⇒ Gemini fallback).

Runs entirely on the showroom PC:
    JPEG frame  →  YOLOv8n  →  vehicle crop  →  plate crop  →  OCR  →  plate

The plate is confirmed only after CONFIRM_FRAMES consecutive matches (default 3)
to protect against noisy single-frame OCR errors. Only then does `process_jpeg`
return a `LocalDetection`; the agent then ships a single `plate_detected`
message per confirmation window.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("vashu_agent.detector")

# COCO vehicle classes (car, motorcycle, bus, truck)
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
PLATE_RE = re.compile(r"[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}")

# Multi-frame confirmation config
CONFIRM_FRAMES = int(os.environ.get("CONFIRM_FRAMES", "3"))
CONFIRM_WINDOW_SECONDS = float(os.environ.get("CONFIRM_WINDOW_SECONDS", "15"))
OCR_ENGINE = os.environ.get("OCR_ENGINE", "easyocr").strip().lower()  # easyocr | gemini


def _normalize_plate(raw: str) -> Optional[str]:
    if not raw:
        return None
    txt = raw.upper().replace("-", "").replace(".", " ")
    # Common OCR mistakes we can normalize safely — only where the position
    # unambiguously calls for it (letters-then-digits Indian format).
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
    plate_jpeg: bytes
    vehicle_class: str
    vehicle_conf: float


@dataclass
class _CandidateState:
    """Rolling multi-frame confirmation buffer keyed by camera_id."""
    plate: str = ""
    count: int = 0
    first_seen: float = 0.0
    best_crop: bytes = b""
    best_plate_crop: bytes = b""
    best_conf: float = 0.0
    best_vehicle_class: str = ""


class LocalPlateDetector:
    """Loads YOLOv8n once and runs detection + OCR on demand.

    Torch/ultralytics/easyocr/emergentintegrations are all lazy-imported so
    simply importing this module does not pull those deps in.
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        from ultralytics import YOLO
        default = os.path.join(os.path.dirname(__file__), "yolov8n.pt")
        self._model = YOLO(model_path or default)
        log.info("[DETECTOR] YOLO loaded: %s", model_path or default)
        self._easyocr = None  # lazy
        self._confirmation: Dict[str, _CandidateState] = defaultdict(_CandidateState)
        log.info("[DETECTOR] OCR engine=%s, confirm_frames=%d, confirm_window=%.1fs",
                 OCR_ENGINE, CONFIRM_FRAMES, CONFIRM_WINDOW_SECONDS)

    # ---------- YOLO ----------
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

    # ---------- Plate ROI (heuristic) ----------
    @staticmethod
    def find_plate_roi(vehicle_crop_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Return a rough plate-region crop from a vehicle crop, or None.

        This is a lightweight heuristic — the lower-middle third of the
        vehicle where most plates sit — good enough for OCR while remaining
        fast on the local PC. If it fails we just OCR the whole vehicle crop.
        """
        if vehicle_crop_bgr is None or vehicle_crop_bgr.size == 0:
            return None
        h, w = vehicle_crop_bgr.shape[:2]
        if h < 20 or w < 40:
            return None
        # bottom 55% vertically, central 80% horizontally
        y1 = int(h * 0.45); y2 = h
        x1 = int(w * 0.10); x2 = int(w * 0.90)
        return vehicle_crop_bgr[y1:y2, x1:x2].copy()

    # ---------- OCR ----------
    def _get_easyocr(self):
        if self._easyocr is None:
            try:
                import easyocr  # lazy
                self._easyocr = easyocr.Reader(["en"], gpu=False, verbose=False)
                log.info("[DETECTOR] EasyOCR reader ready (en, cpu)")
            except Exception as e:
                log.warning("[DETECTOR] EasyOCR unavailable — falling back to Gemini: %s", e)
                self._easyocr = False
        return self._easyocr or None

    def _ocr_easyocr(self, image_bgr: np.ndarray) -> Optional[Tuple[str, float]]:
        reader = self._get_easyocr()
        if reader is None:
            return None
        try:
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        except Exception:
            gray = image_bgr
        try:
            rows = reader.readtext(gray, detail=1, paragraph=False)
        except Exception as e:
            log.warning("[DETECTOR] easyocr.readtext failed: %s", e)
            return None
        best_txt, best_conf = "", 0.0
        for row in rows or []:
            try:
                _bbox, text, conf = row
            except Exception:
                continue
            cand = _normalize_plate(text)
            if cand and float(conf) > best_conf and len(cand) >= 5:
                best_txt, best_conf = cand, float(conf)
        if not best_txt or best_conf < 0.35:
            return None
        return best_txt, best_conf

    async def _ocr_gemini(self, image_bgr: np.ndarray) -> Optional[Tuple[str, float]]:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
        except Exception as e:
            log.warning("[DETECTOR] emergentintegrations import failed: %s", e)
            return None
        key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not key:
            return None
        jpeg = self.encode_jpeg(image_bgr)
        if not jpeg:
            return None
        b64 = base64.b64encode(jpeg).decode("ascii")
        chat = LlmChat(
            api_key=key,
            session_id=f"agent-plate-{int(time.time()*1000)}",
            system_message="You are a precise OCR engine. Output only the requested text.",
        ).with_model("gemini", "gemini-3-flash-preview")
        try:
            resp = await chat.send_message(UserMessage(
                text=("Read the license plate. Return ONLY the plate as continuous "
                      "alphanumeric characters (no spaces). If unclear reply UNKNOWN."),
                file_contents=[ImageContent(image_base64=b64)],
            ))
            raw = resp if isinstance(resp, str) else str(resp)
        except Exception as e:
            log.warning("[DETECTOR] gemini OCR failed: %s", e)
            return None
        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN":
            return None
        plate = _normalize_plate(cleaned) or "".join(c for c in cleaned if c.isalnum())
        if not plate or len(plate) < 5:
            return None
        # Gemini has no numeric confidence — treat matched-regex reads as high.
        return plate, 0.85 if PLATE_RE.search(cleaned) else 0.55

    async def read_plate(self, image_bgr: np.ndarray) -> Optional[Tuple[str, float]]:
        if OCR_ENGINE == "gemini":
            return await self._ocr_gemini(image_bgr)
        r = self._ocr_easyocr(image_bgr)
        if r is not None:
            return r
        # EasyOCR failed → Gemini fallback
        return await self._ocr_gemini(image_bgr)

    # ---------- Public frame pipeline ----------
    async def process_jpeg(self, jpeg: bytes, camera_id: str = "CAM-01") -> Optional[LocalDetection]:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None

        detections = self.detect_vehicles(frame)
        if not detections:
            self._decay(camera_id)
            return None

        top = detections[0]
        vehicle_crop = self.crop(frame, top["bbox"])
        # Try plate ROI first, fall back to whole vehicle crop
        plate_roi = self.find_plate_roi(vehicle_crop)
        ocr_target = plate_roi if plate_roi is not None else vehicle_crop
        read = await self.read_plate(ocr_target)
        if not read:
            return None
        plate, ocr_conf = read

        # Multi-frame confirmation state per camera
        state = self._confirmation[camera_id]
        now = time.time()
        if state.plate != plate or (now - state.first_seen) > CONFIRM_WINDOW_SECONDS:
            state.plate = plate
            state.count = 0
            state.first_seen = now
            state.best_crop = b""
            state.best_plate_crop = b""
            state.best_conf = 0.0
            state.best_vehicle_class = top["cls_name"]

        state.count += 1
        # Keep the best-confidence crop across the window
        if ocr_conf > state.best_conf:
            state.best_conf = ocr_conf
            state.best_crop = self.encode_jpeg(vehicle_crop, quality=78) or jpeg
            state.best_plate_crop = self.encode_jpeg(ocr_target, quality=82) or b""
            state.best_vehicle_class = top["cls_name"]

        log.debug("[DETECTOR] cam=%s plate=%s count=%d conf=%.2f", camera_id, plate, state.count, ocr_conf)
        if state.count < CONFIRM_FRAMES:
            return None

        # Confirmed! Reset the buffer so the next unique plate starts fresh.
        confirmed = LocalDetection(
            plate=state.plate,
            confidence="high" if state.best_conf >= 0.7 else "medium",
            crop_jpeg=state.best_crop or (self.encode_jpeg(vehicle_crop, quality=78) or jpeg),
            plate_jpeg=state.best_plate_crop,
            vehicle_class=state.best_vehicle_class or top["cls_name"],
            vehicle_conf=top["conf"],
        )
        # Clear so a subsequent detection of the same plate needs to re-accumulate
        # after CONFIRM_WINDOW_SECONDS elapses (backend also enforces cooldown).
        state.plate = ""
        state.count = 0
        state.first_seen = 0.0
        state.best_crop = b""
        state.best_plate_crop = b""
        state.best_conf = 0.0
        log.info("[DETECTOR] confirmed plate=%s (%d frames, conf=%.2f) cam=%s",
                 confirmed.plate, CONFIRM_FRAMES, confirmed.confidence == "high" and 1.0 or 0.55, camera_id)
        return confirmed

    def _decay(self, camera_id: str) -> None:
        """Drop the candidate if no vehicle was seen this frame — this keeps
        confirmation from carrying across empty gaps."""
        state = self._confirmation.get(camera_id)
        if state and state.count > 0:
            # gentle decay: forget after a short empty streak
            state.count = max(0, state.count - 1)
            if state.count == 0:
                state.plate = ""


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
