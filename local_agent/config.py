"""Environment configuration for the local agent.

The agent's cameras are NOT configured here. They are added on the portal's
Cameras page; a camera with "Use for records" switched on (and assigned to this
agent) is sent to the agent over its WebSocket while it is online there — see
CameraSupervisor in agent.py.

FRAME_WIDTH / FRAME_HEIGHT / AGENT_FPS / JPEG_QUALITY stay optional capture
defaults for those cameras.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class CameraSpec:
    id: str
    name: str
    source: str
    width: int
    height: int
    fps: int
    jpeg_quality: int
    # Part of the picture to search for plates, as fractions (x1, y1, x2, y2) of
    # width/height — e.g. (0.1, 0.4, 0.9, 1.0) = the lane, not the road behind it.
    roi: Optional[Tuple[float, float, float, float]] = None


@dataclass
class AgentConfig:
    ws_url: str
    agent_id: str
    agent_secret: str
    # Local YOLO+OCR detection (runs on the showroom PC, off by default)
    enable_local_detection: bool = False
    detection_fps: float = 5.0
    outbox_path: str = ""
    # Capture defaults for cameras assigned from the portal
    frame_width: int = 960
    frame_height: int = 540
    fps: int = 8
    jpeg_quality: int = 55

    def camera_spec(self, item: dict) -> CameraSpec:
        """A camera assignment from the server → CameraSpec (bad roi = whole picture)."""
        try:
            roi = parse_roi(item.get("roi"))
        except Exception:
            roi = None
        cid = str(item["camera_id"]).strip().upper()
        return CameraSpec(id=cid, name=str(item.get("name") or cid), source=str(item["source"]),
                          width=self.frame_width, height=self.frame_height, fps=self.fps,
                          jpeg_quality=self.jpeg_quality, roi=roi)

    @classmethod
    def from_env(cls) -> "AgentConfig":
        def req(name: str) -> str:
            v = os.environ.get(name, "").strip()
            if not v:
                raise RuntimeError(f"Missing required env var: {name}")
            return v

        return cls(
            ws_url=req("VASHU_AGENT_WS_URL"),
            agent_id=req("VASHU_AGENT_ID"),
            agent_secret=req("VASHU_AGENT_SECRET"),
            enable_local_detection=os.environ.get("ENABLE_LOCAL_DETECTION", "false").strip().lower()
                in ("1", "true", "yes", "on"),
            detection_fps=_detection_fps(),
            outbox_path=os.environ.get("OUTBOX_PATH", "").strip()
                or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outbox.sqlite3"),
            frame_width=int(os.environ.get("FRAME_WIDTH", "960")),
            frame_height=int(os.environ.get("FRAME_HEIGHT", "540")),
            fps=int(os.environ.get("AGENT_FPS", "8")),
            jpeg_quality=int(os.environ.get("JPEG_QUALITY", "55")),
        )


def parse_roi(value) -> Optional[Tuple[float, float, float, float]]:
    """'0.1,0.4,0.9,1' or [0.1, 0.4, 0.9, 1] → (x1, y1, x2, y2) fractions, or None for the whole picture."""
    if value in (None, "", []):
        return None
    parts = [float(v) for v in (value.split(",") if isinstance(value, str) else value)]
    if len(parts) != 4:
        raise RuntimeError(f"roi must be four numbers x1,y1,x2,y2 (fractions 0–1), got {value!r}")
    x1, y1, x2, y2 = parts
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise RuntimeError(f"roi must satisfy 0 ≤ x1 < x2 ≤ 1 and 0 ≤ y1 < y2 ≤ 1, got {value!r}")
    return (x1, y1, x2, y2)


def _detection_fps() -> float:
    """DETECTION_FPS (default 5). The older DETECTION_INTERVAL_SECONDS is still honoured."""
    fps = os.environ.get("DETECTION_FPS", "").strip()
    if fps:
        return max(0.2, float(fps))
    interval = os.environ.get("DETECTION_INTERVAL_SECONDS", "").strip()
    if interval:
        return max(0.2, 1.0 / max(float(interval), 0.01))
    return 5.0
