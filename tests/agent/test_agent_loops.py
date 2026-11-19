"""Agent loops: detection reads the shared frame buffer; heartbeat carries health."""
import asyncio

import numpy as np
import pytest

import agent
from config import CameraSpec
from edge import Delivery, FrameBuffer, MotionGate
from outbox import Outbox
from plate_detector import LocalDetection

SPEC = CameraSpec(id="GATE-IN", name="Entrance", source="0", width=640, height=480, fps=8, jpeg_quality=55)


class FakeDetector:
    def __init__(self):
        self.frames = []

    def active_tracks(self, camera_id):
        return 0

    def process_frame(self, frame, camera_id, now):
        self.frames.append((camera_id, frame, now))
        return [LocalDetection(plate="KA05MG9999", confidence="high", crop_jpeg=b"", plate_jpeg=b"",
                               vehicle_class="car", vehicle_conf=1.0, reads=3, support=1.0, seen_at=now)]


class RecordingConn:
    agent_id = "pc"
    connected = True

    def __init__(self):
        self.sent = []

    async def safe_send(self, payload):
        self.sent.append(payload)
        return True


def test_detection_uses_shared_frames_not_a_second_capture(tmp_path, monkeypatch):
    def no_capture(*a, **k):
        raise AssertionError("detection must not open its own camera capture")
    monkeypatch.setattr(agent, "CameraCapture", no_capture)

    frames = FrameBuffer()
    frame = np.full((480, 640, 3), 7, dtype=np.uint8)
    frames.put("GATE-IN", frame, ts=1234.0)
    det, delivery, stop, health = FakeDetector(), Delivery(Outbox(tmp_path / "o.db")), asyncio.Event(), {}

    async def go():
        task = asyncio.create_task(agent._detect_camera(SPEC, frames, det, delivery, stop, fps=20, health=health))
        await asyncio.sleep(0.3)
        stop.set()
        await task

    asyncio.run(go())
    assert len(det.frames) == 1                      # same frame (seq) processed once
    assert det.frames[0][1] is frame and det.frames[0][2] == 1234.0
    assert delivery.outbox.depth() == 1              # queued durably
    (_, payload), = delivery.outbox.due()
    assert payload["ts"] == 1234.0 and payload["camera_id"] == "GATE-IN"
    delivery.outbox.close()


def test_heartbeat_reports_health(tmp_path):
    delivery = Delivery(Outbox(tmp_path / "o.db"))
    delivery.outbox.add("x", {})
    conn, stop = RecordingConn(), asyncio.Event()
    health = {"cameras": {"GATE-IN": {"fps": 7.5}}}

    async def go():
        task = asyncio.create_task(agent._heartbeat_loop(conn, stop, health, delivery, detector=object()))
        await asyncio.sleep(0.05)
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    hb = next(m for m in conn.sent if m["type"] == "heartbeat")
    assert hb["health"]["outbox_depth"] == 1 and hb["health"]["detector"] == "ready"
    assert hb["health"]["cameras"]["GATE-IN"]["fps"] == 7.5 and hb["health"]["version"] == agent.AGENT_VERSION
    delivery.outbox.close()


def test_frame_buffer_sequence_and_fps():
    fb = FrameBuffer()
    f = np.zeros((4, 4, 3), dtype=np.uint8)
    assert fb.get("C") is None
    for i in range(11):
        fb.put("C", f, ts=100 + i * 0.1)
    seq, _, ts = fb.get("C")
    assert seq == 11 and ts == pytest.approx(101.0)
    assert fb.fps("C", now=101.0) == pytest.approx(10.0)


def test_motion_gate():
    gate = MotionGate(threshold=2.5, idle_check_seconds=5)
    still = np.full((240, 320, 3), 100, dtype=np.uint8)
    moved = still.copy()
    moved[60:180, 80:240] = 220
    assert gate.should_process(still, now=0.0) is True        # first frame
    assert gate.should_process(still, now=1.0) is False       # nothing changed
    assert gate.should_process(still, now=1.5, busy=True) is True  # tracking a car
    assert gate.should_process(moved, now=2.0) is True        # motion
    assert gate.should_process(moved, now=3.0) is False
    assert gate.should_process(moved, now=8.5) is True        # periodic check while idle
