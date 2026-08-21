"""Vashu Local Camera Agent — Windows edition.

Runs on the showroom PC. Reads local cameras (USB, HTTP MJPEG, RTSP) and
pushes JPEG frames outbound over WSS to the Vashu cloud backend.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import platform
import socket
import sys
import time
from typing import Optional

from config import AgentConfig
from camera import CameraCapture
from connection import CloudConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
    handlers=[
        logging.FileHandler("logs/agent.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("vashu_agent")


async def run(cfg: AgentConfig, test_mode: bool = False) -> int:
    log.info("[AGENT] starting version=1.0.0 agent_id=%s host=%s", cfg.agent_id, socket.gethostname())
    log.info("[AGENT] target=%s camera=%s source=%s", cfg.ws_url, cfg.camera_id, _redact(cfg.camera_source))

    cam = CameraCapture(cfg.camera_source, width=cfg.frame_width, height=cfg.frame_height, target_fps=cfg.fps)
    conn = CloudConnection(cfg.ws_url, cfg.agent_id, cfg.agent_secret)

    if test_mode:
        return await _test_mode(cfg, cam, conn)

    stop_evt = asyncio.Event()

    async def stream_loop():
        interval = 1.0 / max(1, cfg.fps)
        seq = 0
        last_status: Optional[str] = None
        last_heartbeat = 0.0
        while not stop_evt.is_set():
            t0 = time.time()
            frame_jpeg = None
            try:
                frame_jpeg = await asyncio.to_thread(cam.grab_jpeg, cfg.jpeg_quality)
            except Exception as e:
                log.warning("[CAMERA] %s grab error: %s", cfg.camera_id, e)

            now = time.time()
            if frame_jpeg is not None:
                if last_status != "online":
                    log.info("[CAMERA] %s online", cfg.camera_id)
                    await conn.safe_send({
                        "type": "camera_status",
                        "camera_id": cfg.camera_id,
                        "name": cfg.camera_name,
                        "status": "online",
                    })
                    last_status = "online"
                seq += 1
                b64 = base64.b64encode(frame_jpeg).decode("ascii")
                await conn.safe_send({
                    "type": "frame",
                    "camera_id": cfg.camera_id,
                    "name": cfg.camera_name,
                    "seq": seq,
                    "ts": now,
                    "jpeg_b64": b64,
                })
                if seq % (cfg.fps * 5 or 1) == 0:
                    log.info("[STREAM] %s frames=%d", cfg.camera_id, seq)
            else:
                if last_status != "offline":
                    log.warning("[CAMERA] %s offline", cfg.camera_id)
                    await conn.safe_send({
                        "type": "camera_status",
                        "camera_id": cfg.camera_id,
                        "name": cfg.camera_name,
                        "status": "offline",
                        "error": "capture failed",
                    })
                    last_status = "offline"
                await asyncio.sleep(1.0)

            if now - last_heartbeat >= 10:
                await conn.safe_send({"type": "heartbeat", "agent_id": cfg.agent_id})
                last_heartbeat = now
                log.debug("[HEARTBEAT] OK")

            delay = interval - (time.time() - t0)
            if delay > 0:
                await asyncio.sleep(delay)

    async def connection_loop():
        while not stop_evt.is_set():
            try:
                await conn.connect_and_run(hello_payload={
                    "type": "agent_register",
                    "agent_id": cfg.agent_id,
                    "hostname": socket.gethostname(),
                    "platform": platform.system().lower(),
                    "version": "1.0.0",
                })
            except Exception as e:
                log.error("[ERROR] Connection failure: %s", e)
            if stop_evt.is_set():
                break
            wait = conn.next_backoff()
            log.info("[AGENT] reconnecting in %ds", wait)
            await asyncio.sleep(wait)

    try:
        await asyncio.gather(connection_loop(), stream_loop())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop_evt.set()
        cam.release()
        await conn.close()
        log.info("[AGENT] stopped")
    return 0


async def _test_mode(cfg: AgentConfig, cam: CameraCapture, conn: CloudConnection) -> int:
    ok = True

    print("[TEST] 1. Camera connectivity ...", end=" ")
    if cam.probe():
        print("PASS")
    else:
        print("FAIL"); ok = False

    print("[TEST] 2. Frame capture ...", end=" ")
    f = cam.grab_jpeg(cfg.jpeg_quality)
    if f:
        print(f"PASS ({len(f)} bytes)")
    else:
        print("FAIL"); ok = False

    print("[TEST] 3. JPEG encoding ...", end=" ")
    print("PASS" if f and f[:3] == b"\xff\xd8\xff" else "FAIL")
    if not (f and f[:3] == b"\xff\xd8\xff"):
        ok = False

    print("[TEST] 4-6. Cloud WSS + auth + heartbeat ...", end=" ")
    try:
        await conn.probe(agent_id=cfg.agent_id)
        print("PASS")
    except Exception as e:
        print(f"FAIL ({e})"); ok = False

    print("[TEST] 7. Frame transmission ...", end=" ")
    try:
        await conn.probe_send_frame(cfg.camera_id, cfg.camera_name, f or b"\xff\xd8\xff\x00")
        print("PASS")
    except Exception as e:
        print(f"FAIL ({e})"); ok = False

    cam.release()
    await conn.close()
    print("\nRESULT:", "ALL PASS" if ok else "FAILED — see logs above")
    return 0 if ok else 1


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        head, tail = url.split("://", 1)
        if "@" in tail:
            _, rest = tail.split("@", 1)
            return f"{head}://***@{rest}"
    return url


def main() -> int:
    parser = argparse.ArgumentParser("vashu-agent")
    parser.add_argument("--test", action="store_true", help="Run self-tests and exit")
    args = parser.parse_args()
    cfg = AgentConfig.from_env()
    return asyncio.run(run(cfg, test_mode=args.test))


if __name__ == "__main__":
    sys.exit(main())
