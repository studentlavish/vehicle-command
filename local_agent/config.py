"""Environment configuration for the local agent.

Two configuration modes are supported:

1. Multi-camera JSON (preferred):
     AGENT_CAMERAS_JSON='[{"id":"CAM-01","name":"Entrance","source":"0"},
                          {"id":"CAM-02","name":"Exit","source":"rtsp://..."}]'

   or the same JSON in a file pointed to by AGENT_CAMERAS_FILE.

   Each entry may override: width, height, fps, jpeg_quality. Missing keys
   inherit the top-level defaults (FRAME_WIDTH / FRAME_HEIGHT / AGENT_FPS /
   JPEG_QUALITY).

2. Legacy single-camera (kept for backward compat):
     CAMERA_ID / CAMERA_NAME / CAMERA_SOURCE
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

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


@dataclass
class AgentConfig:
    ws_url: str
    agent_id: str
    agent_secret: str
    cameras: List[CameraSpec] = field(default_factory=list)
    # Local YOLO+OCR detection (runs on the showroom PC, off by default)
    enable_local_detection: bool = False
    detection_interval_seconds: float = 3.0

    # Retained for backward compat (first camera's values)
    @property
    def camera_id(self) -> str:
        return self.cameras[0].id if self.cameras else ""

    @property
    def camera_name(self) -> str:
        return self.cameras[0].name if self.cameras else ""

    @property
    def camera_source(self) -> str:
        return self.cameras[0].source if self.cameras else ""

    @property
    def fps(self) -> int:
        return self.cameras[0].fps if self.cameras else 8

    @property
    def jpeg_quality(self) -> int:
        return self.cameras[0].jpeg_quality if self.cameras else 55

    @property
    def frame_width(self) -> int:
        return self.cameras[0].width if self.cameras else 960

    @property
    def frame_height(self) -> int:
        return self.cameras[0].height if self.cameras else 540

    @classmethod
    def from_env(cls) -> "AgentConfig":
        def req(name: str) -> str:
            v = os.environ.get(name, "").strip()
            if not v:
                raise RuntimeError(f"Missing required env var: {name}")
            return v

        default_width = int(os.environ.get("FRAME_WIDTH", "960"))
        default_height = int(os.environ.get("FRAME_HEIGHT", "540"))
        default_fps = int(os.environ.get("AGENT_FPS", "8"))
        default_quality = int(os.environ.get("JPEG_QUALITY", "55"))

        # ---- Mode 1: multi-camera JSON ----
        cams_raw: Optional[str] = None
        cams_json = os.environ.get("AGENT_CAMERAS_JSON", "").strip()
        cams_file = os.environ.get("AGENT_CAMERAS_FILE", "").strip()
        if cams_json:
            cams_raw = cams_json
        elif cams_file and os.path.isfile(cams_file):
            with open(cams_file, "r", encoding="utf-8") as f:
                cams_raw = f.read()

        cameras: List[CameraSpec] = []
        if cams_raw:
            try:
                items = json.loads(cams_raw)
            except Exception as e:
                raise RuntimeError(f"AGENT_CAMERAS_JSON is not valid JSON: {e}")
            if not isinstance(items, list) or not items:
                raise RuntimeError("AGENT_CAMERAS_JSON must be a non-empty list")
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    raise RuntimeError(f"AGENT_CAMERAS_JSON[{i}] must be an object")
                cam_id = str(it.get("id") or f"CAM-{i+1:02d}")
                cameras.append(CameraSpec(
                    id=cam_id,
                    name=str(it.get("name") or cam_id),
                    source=str(it["source"]) if "source" in it else req("CAMERA_SOURCE"),
                    width=int(it.get("width", default_width)),
                    height=int(it.get("height", default_height)),
                    fps=int(it.get("fps", default_fps)),
                    jpeg_quality=int(it.get("jpeg_quality", default_quality)),
                ))
        else:
            # ---- Mode 2: legacy single-camera ----
            cameras.append(CameraSpec(
                id=os.environ.get("CAMERA_ID", "CAM-01"),
                name=os.environ.get("CAMERA_NAME", "Phone Camera"),
                source=req("CAMERA_SOURCE"),
                width=default_width,
                height=default_height,
                fps=default_fps,
                jpeg_quality=default_quality,
            ))

        return cls(
            ws_url=req("VASHU_AGENT_WS_URL"),
            agent_id=req("VASHU_AGENT_ID"),
            agent_secret=req("VASHU_AGENT_SECRET"),
            cameras=cameras,
            enable_local_detection=os.environ.get("ENABLE_LOCAL_DETECTION", "false").strip().lower()
                in ("1", "true", "yes", "on"),
            detection_interval_seconds=float(os.environ.get("DETECTION_INTERVAL_SECONDS", "3.0")),
        )
