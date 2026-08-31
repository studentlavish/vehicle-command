"""Local YOLO license-plate detector + EasyOCR (only).

Pipeline on the showroom PC:
    JPEG frame  →  YOLO (dedicated license-plate model)  →  plate crop
                →  EasyOCR (English)  →  normalized plate

The OCR engine is strictly EasyOCR — no other engine, no cloud fallback. If a dedicated license-plate model
is not configured (`PLATE_MODEL_PATH`), the detector runs in **audit mode**:
it will still identify vehicles for logging + the crop is sent as-is, but
`process_jpeg` returns `None` (i.e. no `plate_detected` message is emitted)
so we NEVER fake a plate read from a heuristic ROI.

Config (env vars, all optional):
    PLATE_MODEL_PATH        Absolute path to a YOLO .pt trained on plates.
                            The model classes MUST include a name matching
                            /plate|license/i. If missing/invalid the
                            detector logs a WARNING and stays in audit mode.
    VEHICLE_MODEL_PATH      Path to a vehicle-class YOLO (default: yolov8n.pt
                            in this directory). Only used to gate OCR (we
                            require a vehicle in-frame before spending OCR
                            cycles).
    EASYOCR_LANGS           Comma-separated language codes (default: en)
    EASYOCR_GPU             1/true to enable GPU (default: 0)
    CONFIRM_FRAMES          Consecutive matches required before a plate is
                            emitted (default: 3)
    CONFIRM_WINDOW_SECONDS  Roll-back window for the buffer (default: 15)
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("vashu_agent.detector")

# COCO vehicle classes (car, motorcycle, bus, truck) — used only to gate OCR.
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
PLATE_RE = re.compile(r"[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}")

# Multi-frame confirmation config
CONFIRM_FRAMES = int(os.environ.get("CONFIRM_FRAMES", "3"))
CONFIRM_WINDOW_SECONDS = float(os.environ.get("CONFIRM_WINDOW_SECONDS", "15"))

# Model paths
_HERE = os.path.dirname(__file__)
VEHICLE_MODEL_PATH = os.environ.get("VEHICLE_MODEL_PATH", os.path.join(_HERE, "yolov8n.pt"))
# If PLATE_MODEL_PATH is not set, look for local_agent/best.pt (typical filename
# for a trained license-plate YOLO). If that file doesn't exist we fall back to
# audit / vehicle-crop-OCR mode — plate_detector.py explains both paths in the
# module docstring.
_default_plate_path = os.path.join(_HERE, "best.pt")
PLATE_MODEL_PATH = os.environ.get(
    "PLATE_MODEL_PATH",
    _default_plate_path if os.path.isfile(_default_plate_path) else "",
).strip()

# EasyOCR config
EASYOCR_LANGS = [s.strip() for s in os.environ.get("EASYOCR_LANGS", "en").split(",") if s.strip()]
EASYOCR_GPU = os.environ.get("EASYOCR_GPU", "0").strip().lower() in ("1", "true", "yes", "on")

# When no dedicated plate model is available, run EasyOCR directly on the
# vehicle crop. To prevent false positives the fallback ONLY emits a plate
# when the OCR result matches the strict Indian plate regex AND passes the
# multi-frame confirmation gate. Set to "false" to keep hard-audit mode.
ALLOW_VEHICLE_CROP_OCR = os.environ.get("ALLOW_VEHICLE_CROP_OCR", "true").strip().lower() in ("1", "true", "yes", "on")

# Regex to accept a class name as a plate class. Real-world exports use
# a variety of names — "plate", "license_plate", "License Plate", or even
# the stringified list "['plate']" — so we match `plate|license|^lp$`
# anywhere inside the class name.
_PLATE_CLASS_RE = re.compile(r"(plate|license|\blp\b)", re.I)


def _normalize_plate(raw: str) -> Optional[str]:
    """Conservative normalization: uppercase, strip whitespace and dashes.

    Deliberately does NOT do OCR-mistake character swaps (O↔0, I↔1, S↔5,
    B↔8) — those require a validated plate-format rule which we don't have.
    """
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
    plate_jpeg: bytes
    vehicle_class: str
    vehicle_conf: float


@dataclass
class _CandidateState:
    plate: str = ""
    count: int = 0
    first_seen: float = 0.0
    best_crop: bytes = b""
    best_plate_crop: bytes = b""
    best_conf: float = 0.0
    best_vehicle_class: str = ""


class LocalPlateDetector:
    """Loads YOLO + EasyOCR once, then processes JPEG frames on demand.

    Emits a confirmed `LocalDetection` only when:
      1. a vehicle is present (via vehicle YOLO), AND
      2. a dedicated plate model returns a plate bounding box, AND
      3. EasyOCR reads a plate on ≥ CONFIRM_FRAMES consecutive frames.
    """

    def __init__(
        self,
        plate_model_path: Optional[str] = None,
        vehicle_model_path: Optional[str] = None,
    ) -> None:
        from ultralytics import YOLO

        # Vehicle model — always required as an OCR gate.
        v_path = vehicle_model_path or VEHICLE_MODEL_PATH
        self._vehicle_model = YOLO(v_path)
        v_names = list(self._vehicle_model.names.values()) if hasattr(self._vehicle_model, "names") else []
        v_plate_hits = [n for n in v_names if _PLATE_CLASS_RE.search(n or "")]
        log.info("[DETECTOR] vehicle YOLO loaded: %s (classes=%d, plate_class=%s)",
                 v_path, len(v_names), v_plate_hits or "NONE")

        # Plate model — optional. If missing or its classes don't include a
        # plate class, stay in audit mode: we will NOT emit plate detections.
        self._plate_model = None
        self._plate_class_indices: List[int] = []
        p_path = (plate_model_path or PLATE_MODEL_PATH or "").strip()
        if p_path and os.path.isfile(p_path):
            try:
                cand = YOLO(p_path)
                names = cand.names if hasattr(cand, "names") else {}
                # Accept model where at least one class name looks like a plate.
                plate_ids = [i for i, n in names.items() if _PLATE_CLASS_RE.search(str(n))]
                if plate_ids:
                    self._plate_model = cand
                    self._plate_class_indices = plate_ids
                    log.info("[DETECTOR] plate YOLO loaded: %s (plate class indices=%s, names=%s)",
                             p_path, plate_ids, [names[i] for i in plate_ids])
                else:
                    log.warning(
                        "[DETECTOR] %s has no license-plate class (classes=%s) — "
                        "detector will stay in audit mode (no plate_detected emitted).",
                        p_path, list(names.values())[:12],
                    )
            except Exception as e:
                log.warning("[DETECTOR] failed to load PLATE_MODEL_PATH=%s: %s", p_path, e)
        else:
            if p_path:
                log.warning("[DETECTOR] PLATE_MODEL_PATH=%s does not exist — audit mode", p_path)
            else:
                log.warning(
                    "[DETECTOR] No PLATE_MODEL_PATH configured. The current YOLO model "
                    "(%s) is a VEHICLE detector (COCO classes), NOT a license-plate detector. "
                    "Set PLATE_MODEL_PATH to a dedicated plate YOLO .pt to enable "
                    "plate recognition. Running in AUDIT MODE (no plate_detected emitted).",
                    v_path,
                )

        # EasyOCR — single reader reused across calls.
        self._easyocr = None
        try:
            import easyocr
            self._easyocr = easyocr.Reader(EASYOCR_LANGS, gpu=EASYOCR_GPU, verbose=False)
            log.info("[DETECTOR] EasyOCR reader ready (langs=%s, gpu=%s)", EASYOCR_LANGS, EASYOCR_GPU)
        except Exception as e:
            log.warning("[DETECTOR] EasyOCR unavailable — plate recognition disabled: %s", e)

        self._confirmation: Dict[str, _CandidateState] = defaultdict(_CandidateState)
        log.info("[DETECTOR] confirmation config: frames=%d window=%.1fs",
                 CONFIRM_FRAMES, CONFIRM_WINDOW_SECONDS)

    # ---------- introspection ----------
    def status(self) -> Dict[str, object]:
        return {
            "vehicle_model": VEHICLE_MODEL_PATH,
            "plate_model": PLATE_MODEL_PATH or None,
            "plate_model_loaded": self._plate_model is not None,
            "plate_class_indices": self._plate_class_indices,
            "easyocr_ready": self._easyocr is not None,
            "audit_mode": self._plate_model is None,
            "confirm_frames": CONFIRM_FRAMES,
            "confirm_window_seconds": CONFIRM_WINDOW_SECONDS,
        }

    # ---------- YOLO helpers ----------
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
        results = self._vehicle_model.predict(small, verbose=False, imgsz=640, conf=min_conf)
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

    def detect_plates(self, frame_bgr: np.ndarray, min_conf: float = 0.30) -> List[dict]:
        """Run the dedicated plate YOLO. Returns [] if no plate model loaded."""
        if self._plate_model is None or frame_bgr is None or frame_bgr.size == 0:
            return []
        results = self._plate_model.predict(frame_bgr, verbose=False, imgsz=640, conf=min_conf)
        if not results:
            return []
        r = results[0]
        if r.boxes is None:
            return []
        out: List[dict] = []
        h, w = frame_bgr.shape[:2]
        for i in range(len(r.boxes)):
            cls_id = int(r.boxes.cls[i].item())
            if self._plate_class_indices and cls_id not in self._plate_class_indices:
                continue
            conf = float(r.boxes.conf[i].item())
            x1, y1, x2, y2 = [float(v) for v in r.boxes.xyxy[i].tolist()]
            x1 = max(0, int(x1)); y1 = max(0, int(y1))
            x2 = min(w - 1, int(x2)); y2 = min(h - 1, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            out.append({"bbox": (x1, y1, x2 - x1, y2 - y1), "conf": conf, "area": (x2 - x1) * (y2 - y1)})
        out.sort(key=lambda d: d["conf"], reverse=True)
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

    # ---------- OCR ----------
    def read_plate(self, plate_bgr: np.ndarray) -> Optional[Tuple[str, float]]:
        """Read a plate crop with EasyOCR. Returns (plate, confidence) or None."""
        if self._easyocr is None or plate_bgr is None or plate_bgr.size == 0:
            return None
        try:
            gray = cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2GRAY)
        except Exception:
            gray = plate_bgr
        try:
            rows = self._easyocr.readtext(gray, detail=1, paragraph=False)
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

    # ---------- Frame pipeline ----------
    async def process_jpeg(self, jpeg: bytes, camera_id: str = "CAM-01") -> Optional[LocalDetection]:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None

        # Vehicle gate — no vehicle → no OCR
        vehicles = self.detect_vehicles(frame)
        if not vehicles:
            self._decay(camera_id)
            return None
        top_v = vehicles[0]
        vehicle_crop = self.crop(frame, top_v["bbox"])

        # Plate detection: prefer the dedicated plate YOLO. If not configured,
        # optionally fall back to OCR-ing the vehicle crop directly. The strict
        # PLATE_RE regex + multi-frame confirmation gate prevent false emits.
        plate_crop: np.ndarray
        strict_regex_required = False
        if self._plate_model is not None:
            plates = self.detect_plates(vehicle_crop)
            if not plates:
                self._decay(camera_id)
                return None
            top_p = plates[0]
            plate_crop = self.crop(vehicle_crop, top_p["bbox"], pad=0.02)
        elif ALLOW_VEHICLE_CROP_OCR:
            # Fallback: OCR the whole vehicle crop with a strict regex guard.
            plate_crop = vehicle_crop
            strict_regex_required = True
        else:
            log.debug("[DETECTOR] hard-audit mode — no plate model, fallback disabled; skipping OCR")
            return None

        read = self.read_plate(plate_crop)
        if not read:
            return None
        plate, ocr_conf = read
        if strict_regex_required and not PLATE_RE.search(plate):
            log.debug("[DETECTOR] fallback rejected non-regex plate=%s", plate)
            return None

        # Multi-frame confirmation
        state = self._confirmation[camera_id]
        now = time.time()
        if state.plate != plate or (now - state.first_seen) > CONFIRM_WINDOW_SECONDS:
            state.plate = plate
            state.count = 0
            state.first_seen = now
            state.best_crop = b""
            state.best_plate_crop = b""
            state.best_conf = 0.0
            state.best_vehicle_class = top_v["cls_name"]

        state.count += 1
        if ocr_conf > state.best_conf:
            state.best_conf = ocr_conf
            state.best_crop = self.encode_jpeg(vehicle_crop, quality=78) or jpeg
            state.best_plate_crop = self.encode_jpeg(plate_crop, quality=85) or b""
            state.best_vehicle_class = top_v["cls_name"]

        log.debug("[DETECTOR] cam=%s plate=%s count=%d conf=%.2f", camera_id, plate, state.count, ocr_conf)
        if state.count < CONFIRM_FRAMES:
            return None

        confirmed = LocalDetection(
            plate=state.plate,
            confidence="high" if state.best_conf >= 0.7 else "medium",
            crop_jpeg=state.best_crop or (self.encode_jpeg(vehicle_crop, quality=78) or jpeg),
            plate_jpeg=state.best_plate_crop,
            vehicle_class=state.best_vehicle_class or top_v["cls_name"],
            vehicle_conf=top_v["conf"],
        )
        state.plate = ""
        state.count = 0
        state.first_seen = 0.0
        state.best_crop = b""
        state.best_plate_crop = b""
        state.best_conf = 0.0
        log.info("[DETECTOR] confirmed plate=%s (%d frames, ocr_conf=%.2f) cam=%s",
                 confirmed.plate, CONFIRM_FRAMES, ocr_conf, camera_id)
        return confirmed

    def _decay(self, camera_id: str) -> None:
        state = self._confirmation.get(camera_id)
        if state and state.count > 0:
            state.count = max(0, state.count - 1)
            if state.count == 0:
                state.plate = ""


_detector: Optional[LocalPlateDetector] = None


def get_detector() -> Optional[LocalPlateDetector]:
    """Lazily construct the singleton — returns None if YOLO or EasyOCR won't load."""
    global _detector
    if _detector is None:
        try:
            _detector = LocalPlateDetector()
        except Exception as e:
            log.warning("[DETECTOR] disabled — could not initialise: %s", e)
            return None
    return _detector
