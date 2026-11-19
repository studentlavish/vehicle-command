"""Building blocks of the agent's detection path, kept free of I/O setup so
they can be tested on their own:

* FrameBuffer  — latest raw frame per camera, shared by streaming and detection
                 (detection reads the uncompressed frame, never a second capture).
* MotionGate   — skips the expensive models while the gate is empty and still.
* Delivery     — durable hand-off of detections to the server through the
                 outbox, with ack / nack / resend.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

import cv2
import numpy as np

from outbox import Outbox

log = logging.getLogger("vashu_agent.edge")

MOTION_THRESHOLD = float(os.environ.get("MOTION_THRESHOLD", "2.5"))     # mean grey-level change (0–255)
IDLE_CHECK_SECONDS = float(os.environ.get("IDLE_CHECK_SECONDS", "5"))  # run detection at least this often


class FrameBuffer:
    """Thread-safe 'latest frame' slot per camera, with a measured FPS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Dict[str, Tuple[int, np.ndarray, float]] = {}
        self._times: Dict[str, Deque[float]] = {}

    def put(self, camera_id: str, frame: np.ndarray, ts: Optional[float] = None) -> int:
        ts = time.time() if ts is None else ts
        with self._lock:
            seq = self._latest.get(camera_id, (0, None, 0.0))[0] + 1
            self._latest[camera_id] = (seq, frame, ts)
            q = self._times.setdefault(camera_id, deque(maxlen=50))
            q.append(ts)
            return seq

    def get(self, camera_id: str) -> Optional[Tuple[int, np.ndarray, float]]:
        with self._lock:
            return self._latest.get(camera_id)

    def fps(self, camera_id: str, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        with self._lock:
            recent = [t for t in self._times.get(camera_id, ()) if now - t <= 5.0]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        return round((len(recent) - 1) / span, 1) if span > 0 else 0.0


def crop_roi(frame: np.ndarray, roi) -> np.ndarray:
    """The region of interest of a frame (roi = fractions x1, y1, x2, y2), or the frame itself."""
    if not roi:
        return frame
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = roi
    return frame[int(y1 * h):max(int(y2 * h), int(y1 * h) + 1), int(x1 * w):max(int(x2 * w), int(x1 * w) + 1)]


class MotionGate:
    """Decides whether a frame is worth running the plate models on."""

    def __init__(self, threshold: float = MOTION_THRESHOLD, idle_check_seconds: float = IDLE_CHECK_SECONDS) -> None:
        self.threshold = threshold
        self.idle_check_seconds = idle_check_seconds
        self._prev: Optional[np.ndarray] = None
        self._last_run = 0.0

    @staticmethod
    def _small(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        scale = 160.0 / max(w, 1)
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        g = cv2.resize(g, (160, max(1, int(h * scale))))
        return cv2.GaussianBlur(g, (5, 5), 0)

    def should_process(self, frame: np.ndarray, now: float, busy: bool = False) -> bool:
        """True when there is motion, a vehicle is being tracked, or it's been a while."""
        small = self._small(frame)
        moved = self._prev is None or self._prev.shape != small.shape or \
            float(np.mean(cv2.absdiff(small, self._prev))) >= self.threshold
        self._prev = small
        if moved or busy or (now - self._last_run) >= self.idle_check_seconds:
            self._last_run = now
            return True
        return False


def detection_payload(det, camera_id: str, camera_name: str, event_id: str) -> Dict[str, Any]:
    """The plate_detected message for one LocalDetection."""
    return {
        "type": "plate_detected",
        "event_id": event_id,
        "camera_id": camera_id,
        "name": camera_name,
        "plate": det.plate,
        "plate_text": det.plate,
        "confidence": det.confidence,
        "reads": det.reads,
        "support": det.support,
        "candidates": det.candidates or [],
        "vehicle_class": det.vehicle_class,
        "vehicle_conf": round(float(det.vehicle_conf), 3),
        "crop_jpeg_b64": base64.b64encode(det.crop_jpeg).decode("ascii") if det.crop_jpeg else None,
        "plate_jpeg_b64": base64.b64encode(det.plate_jpeg).decode("ascii") if det.plate_jpeg else None,
        "ts": det.seen_at or time.time(),  # when the car was seen — kept through any delay
    }


class Delivery:
    """Outbox-backed sending of detections: nothing is lost, nothing is doubled."""

    def __init__(self, outbox: Outbox) -> None:
        self.outbox = outbox
        self.last_ack_at: Optional[float] = None

    def enqueue(self, det, camera_id: str, camera_name: str) -> str:
        event_id = uuid.uuid4().hex
        self.outbox.add(event_id, detection_payload(det, camera_id, camera_name, event_id))
        return event_id

    async def flush(self, conn, now: Optional[float] = None, limit: int = 20) -> int:
        """Send what is due. Returns how many were sent."""
        sent = 0
        for event_id, payload in self.outbox.due(now=now, limit=limit):
            if not await conn.safe_send(payload):
                break  # connection gone — retry after reconnect
            self.outbox.mark_sent(event_id, now=now)
            sent += 1
        return sent

    def handle(self, msg: Dict[str, Any]) -> None:
        """Process plate_ack / plate_nack from the server."""
        event_id = msg.get("event_id")
        if not event_id:
            return
        if msg.get("type") == "plate_ack":
            self.outbox.ack(event_id)
            self.last_ack_at = time.time()
            log.info("[OUTBOX] delivered event=%s plate=%s event=%s review=%s",
                     event_id[:8], msg.get("plate"), msg.get("event"), msg.get("queued_for_review"))
        elif msg.get("type") == "plate_nack":
            self.outbox.release(event_id)
            log.warning("[OUTBOX] server could not save event=%s (%s) — will retry",
                        event_id[:8], msg.get("message", ""))

    def on_reconnect(self) -> None:
        self.outbox.release_all()

    def health(self) -> Dict[str, Any]:
        age = self.outbox.oldest_age()
        return {"outbox_depth": self.outbox.depth(), "outbox_oldest_seconds": round(age) if age is not None else None}
