"""
Camera Manager — USB / Android IP Webcam / RTSP.

- Source formats:
    * USB device index      → int, e.g. 0, 1 (or the strings "0", "usb:0")
    * Android IP Webcam     → "http://PHONE_IP:8080/video"
    * RTSP camera           → "rtsp://username:password@IP:554/Streaming/Channels/101"

- Runs one blocking OpenCV capture thread per camera and keeps the latest
  encoded JPEG frame in memory. WebSocket handlers poll that frame at ~15 FPS
  and stream it to subscribers.

- If the capture read fails (camera drops), we mark status=offline and try to
  reconnect automatically every 5 seconds until the client disconnects.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import cv2

logger = logging.getLogger("rdx.camera")
logger.setLevel(logging.INFO)


def _parse_source(source: Union[str, int]) -> Union[str, int]:
    """Return an int for USB indices, otherwise the URL string unchanged."""
    if isinstance(source, int):
        return source
    s = str(source).strip()
    if not s:
        raise ValueError("Empty camera source")
    # "usb:0" or "0" → int
    if s.lower().startswith("usb:"):
        s = s.split(":", 1)[1]
    if s.isdigit():
        return int(s)
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CameraState:
    camera_id: str
    source: Union[str, int]
    label: str = ""
    status: str = "connecting"   # connecting | online | offline | reconnecting | stopped
    last_frame_jpeg: Optional[bytes] = None
    last_frame_at: Optional[str] = None
    connected_at: Optional[str] = None
    frames_captured: int = 0
    error: Optional[str] = None
    subscribers: int = 0
    # Automatic plate detection now runs at the edge inside `local_agent/plate_detector.py`.
    # The backend receives detections via /api/agent/ws → _persist_entry.
    latest_plate: Optional[Dict[str, Any]] = None
    latest_plate_is_new: bool = False
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "source": str(self.source),
            "label": self.label,
            "status": self.status,
            "connected_at": self.connected_at,
            "last_frame_at": self.last_frame_at,
            "frames_captured": self.frames_captured,
            "error": self.error,
            "subscribers": self.subscribers,
            "latest_plate": self.latest_plate,
        }


class CameraManager:
    """Thread-safe registry of live camera captures."""

    def __init__(self) -> None:
        self._cams: Dict[str, CameraState] = {}
        self._lock = threading.Lock()

    # ---------- public API ----------
    def list(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {cid: st.public() for cid, st in self._cams.items()}

    def get(self, camera_id: str) -> Optional[CameraState]:
        with self._lock:
            return self._cams.get(camera_id)

    # ---------- agent-fed cameras (no local capture thread) ----------
    def register_agent_camera(self, camera_id: str, label: str = "", agent_id: str = "") -> Dict[str, Any]:
        """Register a camera whose frames are pushed from an external Local Agent.
        No cv2 capture thread is started — frames arrive via push_frame()."""
        with self._lock:
            existing = self._cams.get(camera_id)
            if existing and existing._thread and existing._thread.is_alive():
                logger.info("[camera %s] already has local capture — agent register skipped", camera_id)
                return existing.public()
            state = existing or CameraState(camera_id=camera_id, source=f"agent:{agent_id or 'unknown'}", label=label or camera_id)
            state.source = f"agent:{agent_id or 'unknown'}"
            state.label = label or state.label or camera_id
            state.status = "online"
            state.connected_at = datetime.now(timezone.utc).isoformat()
            state.error = None
            self._cams[camera_id] = state
        logger.info("[camera %s] registered as agent-fed (agent=%s)", camera_id, agent_id or "?")
        return state.public()

    def push_frame(self, camera_id: str, jpeg: bytes) -> bool:
        with self._lock:
            state = self._cams.get(camera_id)
        if state is None:
            return False
        state.last_frame_jpeg = jpeg
        state.last_frame_at = datetime.now(timezone.utc).isoformat()
        state.frames_captured += 1
        if state.status != "online":
            state.status = "online"
        return True

    def mark_agent_offline(self, camera_id: str, reason: str = "") -> None:
        with self._lock:
            state = self._cams.get(camera_id)
        if state is None:
            return
        state.status = "offline"
        state.error = reason or "agent disconnected"
        logger.info("[camera %s] marked offline (%s)", camera_id, reason or "agent disconnected")

    def connect(self, camera_id: str, source: Union[str, int], label: str = "") -> Dict[str, Any]:
        """Start a capture thread for this camera. Idempotent per camera_id."""
        parsed = _parse_source(source)
        with self._lock:
            existing = self._cams.get(camera_id)
            if existing and existing._thread and existing._thread.is_alive():
                # if source changed, reset
                if existing.source == parsed:
                    logger.info("[camera %s] connect() no-op (already running on %s)", camera_id, parsed)
                    return existing.public()
                logger.info("[camera %s] source changed → restarting", camera_id)
                existing._stop.set()
            state = CameraState(camera_id=camera_id, source=parsed, label=label or camera_id)
            self._cams[camera_id] = state

        state._stop.clear()
        state._thread = threading.Thread(
            target=self._capture_loop,
            args=(state,),
            name=f"cam-{camera_id}",
            daemon=True,
        )
        state._thread.start()
        logger.info("[camera %s] launched capture thread source=%s", camera_id, parsed)
        return state.public()

    def disconnect(self, camera_id: str) -> bool:
        with self._lock:
            state = self._cams.get(camera_id)
        if not state:
            return False
        state._stop.set()
        state.status = "stopped"
        logger.info("[camera %s] disconnect requested", camera_id)
        return True

    def latest_frame(self, camera_id: str) -> Optional[bytes]:
        with self._lock:
            state = self._cams.get(camera_id)
        return state.last_frame_jpeg if state else None

    def inc_subscribers(self, camera_id: str, delta: int) -> None:
        with self._lock:
            state = self._cams.get(camera_id)
            if state:
                state.subscribers = max(0, state.subscribers + delta)

    # ---------- internal ----------
    def _open_capture(self, source: Union[str, int]) -> Optional[cv2.VideoCapture]:
        """Open cv2.VideoCapture with sensible backend + timeout settings."""
        # For network streams prefer FFMPEG; for USB use default
        backend = cv2.CAP_ANY
        if isinstance(source, str) and (source.startswith("rtsp://") or source.startswith("http")):
            backend = cv2.CAP_FFMPEG
        cap = cv2.VideoCapture(source, backend)
        # Reduce buffering so we get near-live frames
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _capture_loop(self, state: CameraState) -> None:
        """Blocking capture loop with auto-reconnect every 5s on failure."""
        logger.info("[camera %s] capture loop start", state.camera_id)
        RECONNECT_DELAY = 5.0
        FRAME_INTERVAL = 1.0 / 20  # cap at ~20 FPS

        while not state._stop.is_set():
            state.status = "connecting" if state.status != "reconnecting" else "reconnecting"
            state.error = None
            cap = self._open_capture(state.source)
            if cap is None:
                state.status = "offline"
                state.error = f"Unable to open source {state.source!r}"
                logger.warning("[camera %s] open failed → retry in %.0fs", state.camera_id, RECONNECT_DELAY)
                if state._stop.wait(RECONNECT_DELAY):
                    break
                state.status = "reconnecting"
                continue

            state.status = "online"
            state.connected_at = _now_iso()
            logger.info("[camera %s] ONLINE (%s)", state.camera_id, state.source)

            consecutive_failures = 0
            try:
                while not state._stop.is_set():
                    t0 = time.time()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        consecutive_failures += 1
                        logger.debug("[camera %s] read fail %d", state.camera_id, consecutive_failures)
                        if consecutive_failures >= 10:
                            raise RuntimeError("Repeated read failures — stream lost")
                        time.sleep(0.05)
                        continue
                    consecutive_failures = 0
                    # Encode as JPEG
                    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                    if ok:
                        state.last_frame_jpeg = bytes(buf)
                        state.last_frame_at = _now_iso()
                        state.frames_captured += 1
                    # pace
                    delay = FRAME_INTERVAL - (time.time() - t0)
                    if delay > 0:
                        time.sleep(delay)
            except Exception as e:
                state.status = "offline"
                state.error = str(e)
                logger.warning("[camera %s] stream error: %s → reconnect in %.0fs", state.camera_id, e, RECONNECT_DELAY)
            finally:
                try:
                    cap.release()
                except Exception:
                    pass

            if state._stop.is_set():
                break
            state.status = "reconnecting"
            if state._stop.wait(RECONNECT_DELAY):
                break

        state.status = "stopped"
        logger.info("[camera %s] capture loop stopped", state.camera_id)

    # ---------- websocket streaming helper ----------
    async def stream(self, camera_id: str, send_bytes, is_connected, fps: int = 15) -> None:
        """Poll `latest_frame` and push bytes to a websocket at `fps`.
        send_bytes: async callable(data:bytes) -> None
        is_connected: callable() -> bool  (returns False when the socket has closed)
        """
        interval = 1.0 / max(1, min(30, fps))
        last_sent_at: float = 0.0
        last_frame_id = 0
        self.inc_subscribers(camera_id, +1)
        try:
            while is_connected():
                state = self.get(camera_id)
                if state is None:
                    await asyncio.sleep(0.5)
                    continue
                frame = state.last_frame_jpeg
                # Only push when a new frame is available
                if frame is not None and state.frames_captured != last_frame_id:
                    last_frame_id = state.frames_captured
                    now = time.time()
                    if now - last_sent_at >= interval:
                        await send_bytes(frame)
                        last_sent_at = now
                await asyncio.sleep(0.03)
        finally:
            self.inc_subscribers(camera_id, -1)


# module-level singleton
camera_manager = CameraManager()
