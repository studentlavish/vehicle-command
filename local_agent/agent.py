"""Vashu Local Camera Agent — Windows edition.

Runs on the showroom PC. Keeps one outbound WSS connection to the Vashu
backend. The cameras it reads plates from are the portal cameras with
"Use for records" switched on for this agent (sent by the server while they
are online). With ENABLE_LOCAL_DETECTION=true it reads number plates locally
and delivers each detection through a durable outbox (see outbox.py).
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
from typing import Dict, List, Optional, Tuple

from config import AgentConfig, CameraSpec
from camera import CameraCapture
from connection import CloudConnection
from edge import Delivery, FrameBuffer, MotionGate, crop_roi
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
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if dsn:  # optional error tracking
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", "showroom"),
                            traces_sample_rate=0.0, send_default_pii=False)
        except ImportError:
            log.warning("SENTRY_DSN is set but sentry-sdk is not installed")


async def _capture_camera(spec: CameraSpec, stop_evt: asyncio.Event, frames: FrameBuffer) -> None:
    """Capture → shared FrameBuffer for detection. The portal keeps its own live view of
    this camera, so nothing is streamed back (and no camera_status is sent — that would
    make the backend hand the camera over to the agent)."""
    cam = CameraCapture(spec.source, width=spec.width, height=spec.height, target_fps=spec.fps)
    interval = 1.0 / max(1, spec.fps)
    last_status: Optional[str] = None
    try:
        while not stop_evt.is_set():
            t0 = time.time()
            frame = None
            try:
                frame = await asyncio.to_thread(cam.grab_frame)
            except Exception as e:
                log.warning("[CAMERA] %s grab error: %s", spec.id, e)

            if frame is not None:
                frames.put(spec.id, frame, time.time())
                if last_status != "online":
                    log.info("[CAMERA] %s online", spec.id)
                    last_status = "online"
            else:
                if last_status != "offline":
                    log.warning("[CAMERA] %s offline (capture failed) — retrying", spec.id)
                    last_status = "offline"
                await asyncio.sleep(1.0)

            delay = interval - (time.time() - t0)
            if delay > 0:
                await asyncio.sleep(delay)
    finally:
        await asyncio.to_thread(cam.release)


class CameraSupervisor:
    """Runs capture + detection for the cameras the portal assigns to this agent.

    The server sends the full list on every (re)connect and whenever it changes
    (toggle switched, camera went online/offline). Cameras keep running while the
    agent is disconnected; detections wait in the outbox.
    """

    def __init__(self, cfg: AgentConfig, frames: FrameBuffer, detector, delivery: Delivery,
                 health: dict, stop_evt: asyncio.Event) -> None:
        self.cfg, self.frames, self.detector, self.delivery = cfg, frames, detector, delivery
        self.health, self.stop_evt = health, stop_evt
        self._running: Dict[str, Tuple[CameraSpec, asyncio.Event, List[asyncio.Task]]] = {}

    @property
    def camera_ids(self) -> List[str]:
        return sorted(self._running)

    def apply(self, items: list) -> None:
        wanted: Dict[str, CameraSpec] = {}
        for it in items:
            try:
                spec = self.cfg.camera_spec(it)
            except Exception as e:
                log.warning("[CAMERAS] ignoring bad assignment %r: %s", {k: v for k, v in it.items() if k != "source"}, e)
                continue
            wanted[spec.id] = spec
        for cid in list(self._running):
            spec = self._running[cid][0]
            new = wanted.get(cid)
            if new is None or (new.source, new.roi) != (spec.source, spec.roi):
                self._stop(cid)
        for cid, spec in wanted.items():
            if cid in self._running:
                self._running[cid] = (spec, *self._running[cid][1:])  # name change only
            else:
                self._start(spec)
        log.info("[CAMERAS] using %d camera(s) from the portal: %s", len(self._running),
                 ", ".join(self.camera_ids) or "none")

    def _start(self, spec: CameraSpec) -> None:
        cam_stop = asyncio.Event()
        log.info("[CAMERAS] start %s (%s) source=%s @ %dx%d/%dfps roi=%s", spec.id, spec.name, _redact(spec.source),
                 spec.width, spec.height, spec.fps, spec.roi or "full frame")
        tasks = [
            asyncio.create_task(_capture_camera(spec, cam_stop, self.frames)),
            asyncio.create_task(_detect_camera(spec, self.frames, self.detector, self.delivery, cam_stop,
                                               self.cfg.detection_fps, self.health)),
        ]
        self._running[spec.id] = (spec, cam_stop, tasks)

    def _stop(self, camera_id: str) -> None:
        _, cam_stop, _tasks = self._running.pop(camera_id)
        cam_stop.set()  # loops finish their current step and release the camera
        self.frames.drop(camera_id)
        self.health.get("cameras", {}).pop(camera_id, None)
        log.info("[CAMERAS] stop %s", camera_id)

    async def run_until_stopped(self) -> None:
        await self.stop_evt.wait()
        for cid in list(self._running):
            tasks = self._running[cid][2]
            self._stop(cid)
            await asyncio.gather(*tasks, return_exceptions=True)


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
                frame = crop_roi(frame, spec.roi)  # search only the lane
                busy = detector.active_tracks(spec.id) > 0
                if gate.should_process(frame, ts, busy=busy):
                    dets = await asyncio.to_thread(detector.process_frame, frame, spec.id, ts)
                    cam_health["last_detect_ms"] = int((time.time() - t0) * 1000)
                    for det in dets:
                        now = time.time()
                        last = recent.get(det.plate) if det.plate else None  # "" = no-plate vehicle
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
    log.info("[AGENT] starting version=%s agent_id=%s host=%s local_detection=%s",
             AGENT_VERSION, cfg.agent_id, socket.gethostname(), "ON" if cfg.enable_local_detection else "off")

    conn = CloudConnection(cfg.ws_url, cfg.agent_id, cfg.agent_secret)

    if test_mode:
        return await _test_mode(cfg, conn)

    stop_evt = asyncio.Event()
    frames = FrameBuffer()
    health: dict = {}

    detector = None
    delivery: Optional[Delivery] = None
    supervisor: Optional[CameraSupervisor] = None
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
            supervisor = CameraSupervisor(cfg, frames, detector, delivery, health, stop_evt)
            conn.on_assignments = supervisor.apply
            log.info("[DETECTOR] local detection ENABLED at up to %.1f fps per camera; outbox=%s (%d pending)",
                     cfg.detection_fps, cfg.outbox_path, delivery.outbox.depth())
            log.info("[CAMERAS] waiting for camera assignments from the portal "
                     "(Cameras page → \"Use for records\")")
    if supervisor is None:
        def _ignore(cams: list) -> None:
            if cams:
                log.warning("[CAMERAS] portal assigned %d camera(s) but local detection is off — "
                            "set ENABLE_LOCAL_DETECTION=true", len(cams))
        conn.on_assignments = _ignore

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
    if supervisor is not None and delivery is not None:
        tasks += [supervisor.run_until_stopped(), _deliver_loop(conn, delivery, stop_evt)]

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


async def _test_mode(cfg: AgentConfig, conn: CloudConnection) -> int:
    """Check the cloud connection. Cameras come from the portal, so they are checked there."""
    ok = True
    print("[TEST] Cloud WSS + auth + heartbeat ...", end=" ")
    try:
        await conn.probe(agent_id=cfg.agent_id)
        print("PASS")
    except Exception as e:
        print(f"FAIL ({e})"); ok = False
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
