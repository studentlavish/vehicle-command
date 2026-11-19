"""Number-plate detection for the showroom camera agent.

Pipeline (per camera, every DETECTION_INTERVAL_SECONDS):

    frame ─► plate finder (open-image-models YOLOv9, free/MIT) ─► plate crop
          ─► plate reader (Awiros Indian-plate OCR, cross-checked by
             fast-plate-ocr; EasyOCR only as a last resort)       ─► text
          ─► Indian-format check + look-alike fixes (plate_rules)  ─► read
          ─► reads collected per vehicle, character-level vote     ─► plate
          ─► sent to the backend with confidence high / medium / low
             (low and uncertain reads land in the Plate Review queue)

Every component is free and runs offline. See plate_ocr.py for the engines
and install_ocr.sh to install them.

Optional (legacy) components, used only if configured:
    PLATE_MODEL_PATH   a custom Ultralytics YOLO .pt trained on plates — used
                       instead of the open-image-models finder.
    VEHICLE_GATE=true  require an Ultralytics COCO vehicle detection before
                       looking for plates (off by default; needs ultralytics,
                       which is AGPL-licensed).

Voting config (env):
    VOTE_MIN_READS          reads needed before a plate is emitted (default 3)
    VOTE_AGREEMENT          share of reads that must agree for "medium" (0.6)
    CONFIRM_WINDOW_SECONDS  max seconds collecting reads for one vehicle (15)
    VEHICLE_GONE_FRAMES     frames without a plate before finalising (2)
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

import plate_rules

log = logging.getLogger("vashu_agent.detector")

VOTE_MIN_READS = int(os.environ.get("VOTE_MIN_READS", os.environ.get("CONFIRM_FRAMES", "3")))
VOTE_AGREEMENT = float(os.environ.get("VOTE_AGREEMENT", "0.6"))
CONFIRM_WINDOW_SECONDS = float(os.environ.get("CONFIRM_WINDOW_SECONDS", "15"))
VEHICLE_GONE_FRAMES = int(os.environ.get("VEHICLE_GONE_FRAMES", "2"))

_HERE = os.path.dirname(__file__)
_default_plate_path = os.path.join(_HERE, "best.pt")
PLATE_MODEL_PATH = os.environ.get(
    "PLATE_MODEL_PATH", _default_plate_path if os.path.isfile(_default_plate_path) else "",
).strip()
VEHICLE_GATE = os.environ.get("VEHICLE_GATE", "false").strip().lower() in ("1", "true", "yes", "on")
VEHICLE_MODEL_PATH = os.environ.get("VEHICLE_MODEL_PATH", os.path.join(_HERE, "yolov8n.pt"))
VEHICLE_CLASS_IDS = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
_PLATE_CLASS_RE = re.compile(r"(plate|license|\blp\b)", re.I)


def _normalize_plate(raw: str) -> Optional[str]:
    """Return a valid Indian plate (with look-alike characters fixed) or None."""
    plate, _ = plate_rules.correct(raw)
    return plate


@dataclass
class LocalDetection:
    plate: str
    confidence: str
    crop_jpeg: bytes
    plate_jpeg: bytes
    vehicle_class: str
    vehicle_conf: float
    reads: int = 1
    support: float = 1.0
    candidates: Optional[List[str]] = None


@dataclass
class _Read:
    plate: str
    conf: float
    crop: bytes
    plate_crop: bytes
    vehicle_class: str
    vehicle_conf: float


@dataclass
class _VoteState:
    reads: List[_Read] = field(default_factory=list)
    first_seen: float = 0.0
    missing: int = 0

    def reset(self) -> None:
        self.reads = []
        self.first_seen = 0.0
        self.missing = 0


def vote(reads: List[_Read]) -> Tuple[Optional[str], float, List[str]]:
    """Character-level, confidence-weighted majority vote.

    Returns (winner, support, distinct_candidates). `support` is the share of
    total confidence held by reads that equal the winner (or differ from it by
    a single character, which is the usual OCR slip).
    """
    if not reads:
        return None, 0.0, []
    # Vote only among reads of the most common length (weighted).
    by_len: Dict[int, float] = defaultdict(float)
    for r in reads:
        by_len[len(r.plate)] += r.conf
    L = max(by_len, key=by_len.get)
    same = [r for r in reads if len(r.plate) == L]
    chars = []
    for i in range(L):
        tally: Dict[str, float] = defaultdict(float)
        for r in same:
            tally[r.plate[i]] += r.conf
        chars.append(max(tally, key=tally.get))
    winner, _ = plate_rules.correct("".join(chars))
    if not winner:
        # Fall back to the single most-confident read.
        winner = max(reads, key=lambda r: r.conf).plate
    total = sum(r.conf for r in reads) or 1.0
    agree = sum(r.conf for r in reads if plate_rules.distance(r.plate, winner) <= 1)
    exact = sum(r.conf for r in reads if r.plate == winner)
    support = round(min(agree, exact * 1.25) / total, 3)
    distinct = sorted({r.plate for r in reads}, key=lambda p: -sum(r.conf for r in reads if r.plate == p))
    return winner, support, distinct[:5]


class LocalPlateDetector:
    """Finds and reads number plates; emits one voted detection per vehicle."""

    def __init__(self) -> None:
        from plate_ocr import PlateFinder, PlateReader

        # 1. Plate finder — custom Ultralytics model if configured, else the free ONNX finder.
        self._ul_plate = None
        self._ul_plate_ids: List[int] = []
        self._finder = None
        if PLATE_MODEL_PATH and os.path.isfile(PLATE_MODEL_PATH):
            try:
                from ultralytics import YOLO
                m = YOLO(PLATE_MODEL_PATH)
                ids = [i for i, n in (m.names or {}).items() if _PLATE_CLASS_RE.search(str(n))]
                if ids:
                    self._ul_plate, self._ul_plate_ids = m, ids
                    log.info("[DETECTOR] custom plate model: %s", PLATE_MODEL_PATH)
                else:
                    log.warning("[DETECTOR] %s has no plate class — ignoring it", PLATE_MODEL_PATH)
            except Exception as e:
                log.warning("[DETECTOR] could not load PLATE_MODEL_PATH=%s: %s", PLATE_MODEL_PATH, e)
        if self._ul_plate is None:
            self._finder = PlateFinder()

        # 2. Optional vehicle gate (Ultralytics COCO model).
        self._vehicle_model = None
        if VEHICLE_GATE:
            try:
                from ultralytics import YOLO
                self._vehicle_model = YOLO(VEHICLE_MODEL_PATH)
                log.info("[DETECTOR] vehicle gate on: %s", VEHICLE_MODEL_PATH)
            except Exception as e:
                log.warning("[DETECTOR] vehicle gate unavailable (%s) — continuing without it", e)

        # 3. Plate reader(s).
        self._reader = PlateReader()
        self._votes: Dict[str, _VoteState] = defaultdict(_VoteState)
        log.info("[DETECTOR] ready: finder=%s reader=%s vote(min_reads=%d agreement=%.2f window=%.0fs)",
                 "custom-yolo" if self._ul_plate else self._finder.model, self._reader.description,
                 VOTE_MIN_READS, VOTE_AGREEMENT, CONFIRM_WINDOW_SECONDS)

    # ---------- introspection ----------
    def status(self) -> Dict[str, object]:
        return {
            "plate_finder": PLATE_MODEL_PATH if self._ul_plate else (self._finder.model if self._finder else None),
            "plate_reader": self._reader.description,
            "vehicle_gate": self._vehicle_model is not None,
            "vote_min_reads": VOTE_MIN_READS,
            "vote_agreement": VOTE_AGREEMENT,
            "confirm_window_seconds": CONFIRM_WINDOW_SECONDS,
        }

    # ---------- helpers ----------
    @staticmethod
    def encode_jpeg(img: np.ndarray, quality: int = 82) -> Optional[bytes]:
        if img is None or img.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return bytes(buf) if ok else None

    @staticmethod
    def _context_crop(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        """Area around the plate (roughly the vehicle front/back) for the snapshot."""
        H, W = frame.shape[:2]
        w, h = x2 - x1, y2 - y1
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        hw, hh = max(w * 3, 200), max(h * 6, 160)
        return frame[max(0, cy - hh * 2 // 3):min(H, cy + hh // 3 + h), max(0, cx - hw):min(W, cx + hw)]

    def _has_vehicle(self, frame: np.ndarray) -> Tuple[bool, str, float]:
        if self._vehicle_model is None:
            return True, "vehicle", 1.0
        r = self._vehicle_model.predict(frame, verbose=False, imgsz=640, conf=0.35)
        for b in (r[0].boxes if r and r[0].boxes is not None else []):
            cid = int(b.cls.item())
            if cid in VEHICLE_CLASS_IDS:
                return True, VEHICLE_CLASS_IDS[cid], float(b.conf.item())
        return False, "", 0.0

    def _find_plate(self, frame: np.ndarray) -> Optional[Tuple[np.ndarray, Tuple[int, int, int, int], float]]:
        """Return (plate_crop, (x1,y1,x2,y2), confidence) for the best plate in frame."""
        if self._finder is not None:
            boxes = self._finder.find(frame)
            if not boxes:
                return None
            b = boxes[0]
            return self._finder.crop(frame, b), (b.x1, b.y1, b.x2, b.y2), b.conf
        res = self._ul_plate.predict(frame, verbose=False, imgsz=640, conf=0.30)
        best = None
        for bx in (res[0].boxes if res and res[0].boxes is not None else []):
            if int(bx.cls.item()) not in self._ul_plate_ids:
                continue
            c = float(bx.conf.item())
            if best is None or c > best[1]:
                best = ([int(v) for v in bx.xyxy[0].tolist()], c)
        if not best:
            return None
        (x1, y1, x2, y2), c = best
        H, W = frame.shape[:2]
        px, py = int((x2 - x1) * 0.06), int((y2 - y1) * 0.12)
        crop = frame[max(0, y1 - py):min(H, y2 + py), max(0, x1 - px):min(W, x2 + px)].copy()
        return crop, (x1, y1, x2, y2), c

    def read_plate(self, plate_bgr: np.ndarray) -> Optional[Tuple[str, float]]:
        """Read one plate crop → (valid_plate, confidence) or None. Used by self-tests."""
        r = self._reader.read(plate_bgr)
        return (r[0], r[1]) if r else None

    # ---------- Frame pipeline ----------
    def _finalise(self, camera_id: str, reason: str) -> Optional[LocalDetection]:
        """Vote over the collected reads for this camera and emit a detection."""
        st = self._votes[camera_id]
        reads = list(st.reads)
        st.reset()
        if not reads:
            return None
        winner, support, distinct = vote(reads)
        if not winner:
            return None
        n = len(reads)
        mean_conf = sum(r.conf for r in reads) / n
        if n >= VOTE_MIN_READS and support >= 0.8 and mean_conf >= 0.5:
            confidence = "high"
        elif n >= VOTE_MIN_READS and support >= VOTE_AGREEMENT:
            confidence = "medium"
        else:
            confidence = "low"  # → Review queue on the backend
        best = max((r for r in reads if r.plate == winner), key=lambda r: r.conf, default=max(reads, key=lambda r: r.conf))
        log.info("[DETECTOR] %s plate=%s conf=%s reads=%d support=%.2f mean_ocr=%.2f (%s) candidates=%s",
                 camera_id, winner, confidence, n, support, mean_conf, reason, distinct)
        return LocalDetection(
            plate=winner,
            confidence=confidence,
            crop_jpeg=best.crop,
            plate_jpeg=best.plate_crop,
            vehicle_class=best.vehicle_class,
            vehicle_conf=best.vehicle_conf,
            reads=n,
            support=support,
            candidates=distinct,
        )

    async def process_jpeg(self, jpeg: bytes, camera_id: str = "CAM-01") -> Optional[LocalDetection]:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None
        st = self._votes[camera_id]
        now = time.time()

        # Window expired → finalise whatever we have (sent as low/medium).
        if st.reads and (now - st.first_seen) > CONFIRM_WINDOW_SECONDS:
            return self._finalise(camera_id, "window expired")

        has_vehicle, vclass, vconf = self._has_vehicle(frame)
        found = self._find_plate(frame) if has_vehicle else None
        if not found:
            if st.reads:
                st.missing += 1
                if st.missing >= VEHICLE_GONE_FRAMES:
                    return self._finalise(camera_id, "vehicle left")
            return None
        st.missing = 0
        plate_crop, (x1, y1, x2, y2), box_conf = found

        read = self._reader.read(plate_crop)
        if not read:
            return None
        plate, ocr_conf, note = read

        st.reads.append(_Read(
            plate=plate,
            conf=ocr_conf * (0.6 + 0.4 * box_conf),
            crop=self.encode_jpeg(self._context_crop(frame, x1, y1, x2, y2), quality=78) or jpeg,
            plate_crop=self.encode_jpeg(plate_crop, quality=88) or b"",
            vehicle_class=vclass,
            vehicle_conf=vconf,
        ))
        if len(st.reads) == 1:
            st.first_seen = now
        log.debug("[DETECTOR] cam=%s read=%s ocr=%.2f box=%.2f via %s (n=%d)",
                  camera_id, plate, ocr_conf, box_conf, note, len(st.reads))

        # Enough agreeing reads → emit now instead of waiting for the window.
        if len(st.reads) >= VOTE_MIN_READS:
            winner, support, _ = vote(st.reads)
            if winner and support >= 0.8:
                return self._finalise(camera_id, "votes agree")
            if len(st.reads) >= VOTE_MIN_READS * 3:
                return self._finalise(camera_id, "max reads")
        return None


_detector: Optional[LocalPlateDetector] = None


def get_detector() -> Optional[LocalPlateDetector]:
    """Lazily construct the singleton — returns None if no plate finder / reader can load."""
    global _detector
    if _detector is None:
        try:
            _detector = LocalPlateDetector()
        except Exception as e:
            log.warning("[DETECTOR] disabled — could not initialise: %s", e)
            return None
    return _detector
