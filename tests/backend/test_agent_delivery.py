"""Phase 3 — exactly-once processing of agent detections, nack, real seen-at time."""
import time
from datetime import datetime, timezone

import server


def _msg(event_id, plate="KA05MG9999", **extra):
    return {"type": "plate_detected", "event_id": event_id, "camera_id": "CAM-01", "plate": plate,
            "confidence": "high", "reads": 3, "support": 1.0, **extra}


def test_resent_event_is_processed_once(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json(_msg("evt-1"))
        first = ws.receive_json()
        ws.send_json(_msg("evt-1"))  # agent never saw the ack and re-sends
        second = ws.receive_json()
    assert first == second and first["event_id"] == "evt-1" and first["event"] == "entry"
    assert db.visit_sessions.count_documents({}) == 1
    assert db.processed_events.find_one({"event_id": "evt-1"})["status"] == "done"


def test_resend_after_reconnect_is_answered_from_record(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json(_msg("evt-2"))
        ack = ws.receive_json()
    with agent_ws() as ws:
        ws.send_json(_msg("evt-2"))
        assert ws.receive_json() == ack
    assert db.visit_sessions.count_documents({}) == 1


def test_failure_sends_nack_and_retry_succeeds(client, db, agent_ws, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(server, "route_detection", boom)
    with agent_ws() as ws:
        ws.send_json(_msg("evt-3"))
        nack = ws.receive_json()
    assert nack == {"type": "plate_nack", "event_id": "evt-3", "message": "RuntimeError"}
    assert db.processed_events.count_documents({"event_id": "evt-3"}) == 0  # claim released
    monkeypatch.undo()
    with agent_ws() as ws:
        ws.send_json(_msg("evt-3"))
        assert ws.receive_json()["event"] == "entry"


def test_stale_claim_is_taken_over(client, db, agent_ws):
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.processed_events.insert_one({"event_id": "evt-4", "status": "processing", "at": old})  # crashed attempt
    with agent_ws() as ws:
        ws.send_json(_msg("evt-4"))
        assert ws.receive_json()["event"] == "entry"


def test_detection_time_from_agent_is_kept(client, db, agent_ws):
    seen = time.time() - 600  # seen 10 min ago, delivered now (after an outage)
    with agent_ws() as ws:
        ws.send_json(_msg("evt-5", ts=seen))
        ws.receive_json()
    entry = datetime.fromisoformat(db.visit_sessions.find_one({})["entry_time"])
    assert abs(entry.timestamp() - seen) < 1


def test_implausible_agent_time_is_ignored(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json(_msg("evt-6", ts=time.time() + 86400))  # PC clock a day ahead
        ws.receive_json()
    entry = datetime.fromisoformat(db.visit_sessions.find_one({})["entry_time"])
    assert abs(entry.timestamp() - time.time()) < 30


def test_messages_without_event_id_still_work(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json({k: v for k, v in _msg("x").items() if k != "event_id"})
        ack = ws.receive_json()
    assert ack["event"] == "entry" and ack["event_id"] is None


def test_heartbeat_health_is_stored(client, admin_headers, agent_ws):
    with agent_ws("pc-health") as ws:
        ws.send_json({"type": "heartbeat", "agent_id": "pc-health",
                      "health": {"outbox_depth": 3, "detector": "ready", "cameras": {"GATE-IN": {"fps": 7.9}}}})
        ws.send_json({"type": "ping", "ts": 1})
        ws.receive_json()  # pong — heartbeat handled before it
    agent = next(a for a in client.get("/api/agent/status", headers=admin_headers).json() if a["agent_id"] == "pc-health")
    assert agent["health"]["outbox_depth"] == 3 and agent["health"]["cameras"]["GATE-IN"]["fps"] == 7.9
