"""Durable outbox + delivery (ack / nack / resend)."""
import asyncio

from edge import Delivery, detection_payload
from outbox import Outbox
from plate_detector import LocalDetection


def _det(plate="KA05MG9999", seen_at=1_700_000_000.0):
    return LocalDetection(plate=plate, confidence="high", crop_jpeg=b"\xff\xd8x", plate_jpeg=b"", vehicle_class="car",
                          vehicle_conf=1.0, reads=3, support=1.0, candidates=[plate], seen_at=seen_at)


class FakeConn:
    def __init__(self, up=True):
        self.up = up
        self.sent = []

    async def safe_send(self, payload):
        if not self.up:
            return False
        self.sent.append(payload)
        return True


def test_outbox_survives_restart(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    box = Outbox(path)
    box.add("e1", {"plate": "KA05MG9999"}, now=100.0)
    box.close()
    reopened = Outbox(path)
    assert reopened.depth() == 1
    assert reopened.due(now=101.0) == [("e1", {"plate": "KA05MG9999"})]
    reopened.close()


def test_sent_events_wait_for_ack_then_resend(tmp_path):
    box = Outbox(tmp_path / "o.db")
    box.add("e1", {"n": 1}, now=0.0)
    box.mark_sent("e1", now=10.0)
    assert box.due(now=20.0, retry_after=15) == []          # in flight
    assert [e for e, _ in box.due(now=26.0, retry_after=15)] == ["e1"]  # no ack → resend
    assert box.ack("e1") is True and box.depth() == 0
    assert box.ack("e1") is False                           # duplicate ack is harmless
    box.close()


def test_release_and_reconnect_make_events_due(tmp_path):
    box = Outbox(tmp_path / "o.db")
    box.add("a", {}, now=0.0)
    box.add("b", {}, now=1.0)
    box.mark_sent("a", now=5.0)
    box.mark_sent("b", now=5.0)
    box.release("a")
    assert [e for e, _ in box.due(now=6.0)] == ["a"]
    box.release_all()
    assert [e for e, _ in box.due(now=6.0)] == ["a", "b"]  # oldest first
    box.close()


def test_payload_keeps_detection_time_and_event_id():
    p = detection_payload(_det(seen_at=123.0), "GATE-IN", "Entrance", "abc")
    assert p["type"] == "plate_detected" and p["event_id"] == "abc" and p["ts"] == 123.0
    assert p["camera_id"] == "GATE-IN" and p["crop_jpeg_b64"] and p["plate_jpeg_b64"] is None


def test_delivery_flow(tmp_path):
    d = Delivery(Outbox(tmp_path / "o.db"))
    eid = d.enqueue(_det(), "GATE-IN", "Entrance")
    assert d.outbox.depth() == 1

    down = FakeConn(up=False)
    assert asyncio.run(d.flush(down)) == 0 and d.outbox.depth() == 1   # offline: kept

    up = FakeConn()
    assert asyncio.run(d.flush(up)) == 1 and up.sent[0]["event_id"] == eid
    assert asyncio.run(d.flush(up)) == 0                                # in flight, not re-sent yet

    d.handle({"type": "plate_nack", "event_id": eid})
    assert asyncio.run(d.flush(up)) == 1                                # nack → retried at once

    d.handle({"type": "plate_ack", "event_id": eid, "plate": "KA05MG9999"})
    assert d.outbox.depth() == 0 and d.health()["outbox_depth"] == 0
    d.outbox.close()
