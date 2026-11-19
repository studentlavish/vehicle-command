"""HTTP MJPEG camera reader that uses Python's own networking.

Why this exists
---------------
On macOS 15+ the Local Network privacy check can block connections made by
OpenCV's bundled FFmpeg ("No route to host") even when Python itself — and
curl — reach the camera fine. Android "IP Webcam" and most cheap IP cameras
serve plain MJPEG over HTTP (a stream of JPEG images), which is easy to read
directly: download the stream with urllib and cut out each JPEG.

`open_video(source)` returns an object with the cv2.VideoCapture methods the
app uses (isOpened, read, set, release):
  * http(s):// MJPEG stream  → MjpegCapture (this module)
  * http(s):// single JPEG   → MjpegCapture in snapshot mode (re-fetches per read)
  * anything else (RTSP, USB index, HLS…) → cv2.VideoCapture as before

Set CAMERA_HTTP_READER=ffmpeg to force the old OpenCV/FFmpeg path.
Shared by backend/camera_manager.py and local_agent/camera.py — keep the two
copies identical.
"""
from __future__ import annotations

import logging
import os
import threading
import urllib.request
from typing import Optional, Tuple, Union

import cv2
import numpy as np

log = logging.getLogger("mjpeg_reader")

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_MAX_BUFFER = 8 * 1024 * 1024
# Talk to LAN cameras directly — never through an HTTP proxy from the environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MjpegCapture:
    """Minimal cv2.VideoCapture-compatible reader for HTTP MJPEG / JPEG URLs."""

    def __init__(self, url: str, timeout: float = 6.0) -> None:
        self.url = url
        self.timeout = timeout
        self._lock = threading.Lock()
        self._new = threading.Condition(self._lock)
        self._jpeg: Optional[bytes] = None
        self._seq = 0
        self._read_seq = 0
        self._alive = False
        self._snapshot = False
        self._resp = None
        self._thread: Optional[threading.Thread] = None
        self._open()

    # --- cv2.VideoCapture API -------------------------------------------
    def isOpened(self) -> bool:  # noqa: N802 (cv2 naming)
        return self._alive

    def set(self, *_args) -> bool:
        return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._alive:
            return False, None
        if self._snapshot:
            jpg = self._fetch_snapshot()
        else:
            with self._new:
                if self._seq == self._read_seq:
                    self._new.wait(timeout=self.timeout)
                if self._seq == self._read_seq or self._jpeg is None:
                    return False, None
                jpg, self._read_seq = self._jpeg, self._seq
        if not jpg:
            return False, None
        frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        return (frame is not None), frame

    def release(self) -> None:
        self._alive = False
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass
        with self._new:
            self._new.notify_all()

    # --- internals ------------------------------------------------------
    def _open(self) -> None:
        req = urllib.request.Request(self.url, headers={"User-Agent": "VashuCamera/1.0"})
        resp = _OPENER.open(req, timeout=self.timeout)  # raises on connection errors
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "multipart" in ctype:
            self._resp = resp
            self._alive = True
            self._thread = threading.Thread(target=self._pump, name="mjpeg-reader", daemon=True)
            self._thread.start()
            # Wait briefly for the first frame so isOpened() means "frames are flowing".
            with self._new:
                if self._seq == 0:
                    self._new.wait(timeout=self.timeout)
            if self._seq == 0:
                self.release()
                raise IOError("connected, but no JPEG frames arrived")
        elif "image/jpeg" in ctype or "image/jpg" in ctype:
            resp.close()
            self._snapshot = True
            self._alive = True
        else:
            resp.close()
            raise ValueError(f"not an MJPEG/JPEG stream (Content-Type: {ctype or 'unknown'})")

    def _pump(self) -> None:
        buf = b""
        try:
            while self._alive:
                chunk = self._resp.read(32 * 1024)
                if not chunk:
                    break
                buf += chunk
                latest = None
                while True:
                    a = buf.find(_SOI)
                    if a < 0:
                        buf = buf[-1:]
                        break
                    b = buf.find(_EOI, a + 2)
                    if b < 0:
                        buf = buf[a:]
                        break
                    latest = buf[a:b + 2]
                    buf = buf[b + 2:]
                if len(buf) > _MAX_BUFFER:
                    buf = b""
                if latest:
                    with self._new:
                        self._jpeg = latest
                        self._seq += 1
                        self._new.notify_all()
        except Exception as e:
            if self._alive:
                log.warning("[camera] MJPEG stream ended: %s", e)
        finally:
            self._alive = False
            with self._new:
                self._new.notify_all()

    def _fetch_snapshot(self) -> Optional[bytes]:
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "VashuCamera/1.0"})
            with _OPENER.open(req, timeout=self.timeout) as r:
                return r.read()
        except Exception as e:
            log.warning("[camera] snapshot fetch failed: %s", e)
            self._alive = False
            return None


def open_video(source: Union[str, int], timeout: float = 6.0):
    """Open a camera source. Returns an opened capture object, or None on failure."""
    use_python_http = os.environ.get("CAMERA_HTTP_READER", "python").strip().lower() != "ffmpeg"
    if isinstance(source, str) and source.lower().startswith(("http://", "https://")) and use_python_http:
        try:
            return MjpegCapture(source, timeout=timeout)
        except ValueError as e:
            log.info("[camera] %s — falling back to FFmpeg", e)
        except Exception as e:
            log.warning("[camera] cannot open %s: %s", _redact(source), e)
            return None
    backend = cv2.CAP_ANY
    if isinstance(source, str) and source.lower().startswith(("rtsp://", "http://", "https://")):
        backend = cv2.CAP_FFMPEG
    cap = cv2.VideoCapture(source, backend)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def _redact(url: str) -> str:
    """Hide credentials in URLs for logs."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url
