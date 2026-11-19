"""Vashu Local Camera Agent — Windows edition.

Runs on the showroom PC. Reads one or many local cameras (USB, HTTP MJPEG,
RTSP), pushes JPEG frames outbound over a single WSS connection to the Vashu
cloud backend and — with ENABLE_LOCAL_DETECTION=true — reads number plates
locally and delivers each detection through a durable outbox (see outbox.py).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Optional

from config import AgentConfig, CameraSpec
from camera import CameraCapture
from connection import CloudConnection, pack_binary_frame
from edge import Delivery, FrameBuffer, MotionGate
from outbox import Outbox

AGENT_VERSION = "1.3.0"
log = logging.getLogger("vashu_agent")


def setup_logging() -> None:
    """Console + rotating file (5 × 5 MB) in local_agent/logs/agent.log."""
    log_dir = Path(os.environ.get("AGENT_LOG_DIR", "") or Path(__file__).resolve().parent / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(log_dir / "agent.log", maxBytes=5 * 1024 * 1024,
                                                 backupCount=5, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


async def _stream_camera(spec: CameraSpec, conn: CloudConnection, stop_evt: asyncio.Event,
                         frames: FrameBuffer) -> None:
    """Capture → shared FrameBuffer (for detection) → JPEG → cloud (live view)."""
    cam = CameraCapture(spec.source, width=spec.width, height=spec.height, target_fps=spec.fps)
    interval = 1.0 / max(1, spec.fps)
    seq = 0
    last_status: Optional[str] = None
    try:
        while not stop_evt.is_set():
            t0 = time.time()
            frame = None
            try:
                frame = await asyncio.to_thread(cam.grab_frame)
            except Exception as e:
                log.warning("[CAMERA] %s grab error: %s", spec.id, e)

            now = time.time()
            if frame is not None:
                frames.put(spec.id, frame, now)
                if last_status != "online":
                    log.info("[CAMERA] %s online", spec.id)
                    await conn.safe_send({"type": "camera_status", "camera_id": spec.id,
                                          "name": spec.name, "status": "online"})
                    last_status = "online"
                if conn.connected:
                    jpeg = await asyncio.to_thread(CameraCapture.encode, frame, spec.jpeg_quality)
                    if jpeg:
                        seq += 1
                        header = {"type": "frame", "camera_id": spec.id, "name": spec.name, "seq": seq, "ts": now}
                        await conn.safe_send_binary(pack_binary_frame(header, jpeg))
                        if seq % (spec.fps * 30 or 1) == 0:
                            log.info("[STREAM] %s frames=%d bytes=%d fps=%.1f", spec.id, seq, len(jpeg), frames.fps(spec.id))
            else:
                if last_status != "offline":
                    log.warning("[CAMERA] %s offline", spec.id)
                    await conn.safe_send({"type": "camera_status", "camera_id": spec.id, "name": spec.name,
                                          "status": "offline", "error": "capture failed"})
                    last_status = "offline"
                await asyncio.sleep(1.0)

            delay = interval - (time.time() - t0)
            if delay > 0:
                await asyncio.sleep(delay)
    finally:
        cam.release()


async def _detect_camera(spec: CameraSpec, frames: FrameBuffer, detector, delivery: Delivery,
                         stop_evt: asyncio.Event, fps: float, health: dict) -> None:
    """Run plate detection on the latest shared frame of `spec`, up to `fps` times a second.

    The models run in a worker thread so streaming and heartbeats never stall.
    Each final detection goes into the outbox first, then to the server.
    """
    interval = 1.0 / max(fps, 0.1)
    gate = MotionGate()
    # Same plate on the same camera within this window is not re-sent — a car
    # parked in view must not be reported again (the backend also enforces a
    # minimum visit time before an EXIT).
    repeat_window = float(os.environ.get("REPEAT_SUPPRESS_SECONDS", "120"))
    recent: dict = {}
    last_seq = 0
    cam_health = health.setdefault("cameras", {}).setdefault(spec.id, {})
    while not stop_evt.is_set():
        t0 = time.time()
        try:
            latest = frames.get(spec.id)
            if latest and latest[0] != last_seq:
                last_seq, frame, ts = latest
                busy = detector.active_tracks(spec.id) > 0
                if gate.should_process(frame, ts, busy=busy):
                    dets = await asyncio.to_thread(detector.process_frame, frame, spec.id, ts)
                    cam_health["last_detect_ms"] = int((time.time() - t0) * 1000)
                    for det in dets:
                        now = time.time()
                        last = recent.get(det.plate)
                        if last and (now - last) < repeat_window:
                            log.info("[DETECTOR] %s suppressed repeat plate=%s (%.0fs ago)", spec.id, det.plate, now - last)
                            continue
                        event_id = delivery.enqueue(det, spec.id, spec.name)  # durable before anything else
                        recent[det.plate] = now
                        cam_health["last_plate_at"] = now
                        log.info("[PLATE] QUEUED event=%s plate=%s cam=%s conf=%s reads=%d support=%.2f",
                                 event_id[:8], det.plate, spec.id, det.confidence, det.reads, det.support)
                    for k in [k for k, t in recent.items() if time.time() - t > repeat_window]:
                        recent.pop(k, None)
            cam_health["fps"] = frames.fps(spec.id)
        except Exception as e:
            log.warning("[DETECTOR] %s error: %s", spec.id, e)
        delay = interval - (time.time() - t0)
        await asyncio.sleep(delay if delay > 0 else 0.01)  # CPU-bound → the rate drops on its own


async def _deliver_loop(conn: CloudConnection, delivery: Delivery, stop_evt: asyncio.Event) -> None:
    while not stop_evt.is_set():
        try:
            if conn.connected:
                await delivery.flush(conn)
        except Exception as e:
            log.warning("[OUTBOX] flush error: %s", e)
        await asyncio.sleep(1.0)


async def _heartbeat_loop(conn: CloudConnection, stop_evt: asyncio.Event, health: dict,
                          delivery: Optional[Delivery], detector) -> None:
    while not stop_evt.is_set():
        if conn.connected:
            health["detector"] = "ready" if detector is not None else "off"
            if delivery is not None:
                health.update(delivery.health())
            health["version"] = AGENT_VERSION
            await conn.safe_send({"type": "heartbeat", "agent_id": conn.agent_id, "health": health})
            await conn.safe_send({"type": "ping", "ts": time.time()})
        await asyncio.sleep(10)


async def run(cfg: AgentConfig, test_mode: bool = False) -> int:
    log.info("[AGENT] starting version=%s agent_id=%s host=%s cameras=%d local_detection=%s",
             AGENT_VERSION, cfg.agent_id, socket.gethostname(), len(cfg.cameras),
             "ON" if cfg.enable_local_detection else "off")
    for c in cfg.cameras:
        log.info("[AGENT]  · %s (%s) source=%s @ %dx%d/%dfps q%d",
                 c.id, c.name, _redact(c.source), c.width, c.height, c.fps, c.jpeg_quality)

    conn = CloudConnection(cfg.ws_url, cfg.agent_id, cfg.agent_secret)

    if test_mode:
        # Test with first camera only
        first = cfg.cameras[0]
        cam = CameraCapture(first.source, width=first.width, height=first.height, target_fps=first.fps)
        return await _test_mode(cfg, first, cam, conn)

    stop_evt = asyncio.Event()
    frames = FrameBuffer()
    health: dict = {}

    detector = None
    delivery: Optional[Delivery] = None
    if cfg.enable_local_detection:
        try:
            from plate_detector import get_detector
            detector = get_detector()
        except Exception as e:
            log.warning("[DETECTOR] disabled — import failed: %s", e)
        if detector is not None:
            delivery = Delivery(Outbox(cfg.outbox_path))
            conn.on_message = delivery.handle
            conn.on_connect = delivery.on_reconnect
            pending = delivery.outbox.depth()
            log.info("[DETECTOR] local detection ENABLED on %d camera(s) at up to %.1f fps; outbox=%s (%d pending)",
                     len(cfg.cameras), cfg.detection_fps, cfg.outbox_path, pending)

    async def connection_loop():
        while not stop_evt.is_set():
            try:
                await conn.connect_and_run(hello_payload={
                    "type": "agent_register",
                    "agent_id": cfg.agent_id,
                    "hostname": socket.gethostname(),
                    "platform": platform.system().lower(),
                    "version": AGENT_VERSION,
                })
            except Exception as e:
                log.error("[ERROR] Connection failure: %s", e)
            if stop_evt.is_set():
                break
            wait = conn.next_backoff()
            log.info("[AGENT] reconnecting in %ds", wait)
            await asyncio.sleep(wait)

    tasks = [connection_loop(), _heartbeat_loop(conn, stop_evt, health, delivery, detector)]
    tasks += [_stream_camera(spec, conn, stop_evt, frames) for spec in cfg.cameras]
    if detector is not None and delivery is not None:
        tasks += [_detect_camera(spec, frames, detector, delivery, stop_evt, cfg.detection_fps, health)
                  for spec in cfg.cameras]
        tasks.append(_deliver_loop(conn, delivery, stop_evt))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop_evt.set()
        await conn.close()
        if delivery is not None:
            delivery.outbox.close()
        log.info("[AGENT] stopped")
    return 0


async def _test_mode(cfg: AgentConfig, spec: CameraSpec, cam: CameraCapture, conn: CloudConnection) -> int:
    ok = True

    print("[TEST] 1. Camera connectivity ...", end=" ")
    if cam.probe():
        print("PASS")
    else:
        print("FAIL"); ok = False

    print("[TEST] 2. Frame capture ...", end=" ")
    f = cam.grab_jpeg(spec.jpeg_quality)
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
        await conn.probe_send_frame(spec.id, spec.name, f or b"\xff\xd8\xff\x00")
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
    setup_logging()
    cfg = AgentConfig.from_env()
    return asyncio.run(run(cfg, test_mode=args.test))


if __name__ == "__main__":
    sys.exit(main())
