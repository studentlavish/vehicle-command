"""Camera capture wrapper — USB / HTTP MJPEG / RTSP."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Union

import cv2

log = logging.getLogger("vashu_agent.camera")


def _parse(source: str) -> Union[str, int]:
    s = str(source).strip()
    if s.lower().startswith("usb:"):
        s = s.split(":", 1)[1]
    if s.isdigit():
        return int(s)
    return s


class CameraCapture:
    def __init__(self, source: str, width: int = 1280, height: int = 720, target_fps: int = 10) -> None:
        self.source = _parse(source)
        self.width = width
        self.height = height
        self.target_fps = target_fps
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._last_open_attempt = 0.0

    def _open(self) -> bool:
        try:
            backend = cv2.CAP_ANY
            if isinstance(self.source, str) and (self.source.startswith("rtsp://") or self.source.startswith("http")):
                backend = cv2.CAP_FFMPEG
            cap = cv2.VideoCapture(self.source, backend)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            except Exception:
                pass
            if not cap.isOpened():
                cap.release()
                return False
            self._cap = cap
            log.info("[CAMERA] opened source=%s", self.source if isinstance(self.source, int) else "***")
            return True
        except Exception as e:
            log.warning("[CAMERA] open error: %s", e)
            return False

    def probe(self) -> bool:
        with self._lock:
            if self._cap is None:
                self._open()
            if self._cap is None:
                return False
            ok, _ = self._cap.read()
            return bool(ok)

    def grab_jpeg(self, quality: int = 70) -> Optional[bytes]:
        with self._lock:
            if self._cap is None:
                now = time.time()
                if now - self._last_open_attempt < 3.0:
                    return None
                self._last_open_attempt = now
                if not self._open():
                    return None
            ok, frame = self._cap.read()
            if not ok or frame is None:
                log.warning("[CAMERA] read fail — closing and will retry")
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
                return None
            if self.width and self.height:
                h, w = frame.shape[:2]
                if (w, h) != (self.width, self.height):
                    frame = cv2.resize(frame, (self.width, self.height))
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return bytes(buf) if ok else None

    def release(self) -> None:
        with self._lock:
            try:
                if self._cap is not None:
                    self._cap.release()
            except Exception:
                pass
            self._cap = None
