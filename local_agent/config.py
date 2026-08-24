"""Environment configuration for the local agent."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass
class AgentConfig:
    ws_url: str
    agent_id: str
    agent_secret: str
    camera_id: str
    camera_name: str
    camera_source: str
    fps: int
    jpeg_quality: int
    frame_width: int
    frame_height: int

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
            camera_id=os.environ.get("CAMERA_ID", "CAM-01"),
            camera_name=os.environ.get("CAMERA_NAME", "Phone Camera"),
            camera_source=req("CAMERA_SOURCE"),
            fps=int(os.environ.get("AGENT_FPS", "8")),
            jpeg_quality=int(os.environ.get("JPEG_QUALITY", "55")),
            frame_width=int(os.environ.get("FRAME_WIDTH", "960")),
            frame_height=int(os.environ.get("FRAME_HEIGHT", "540")),
        )
