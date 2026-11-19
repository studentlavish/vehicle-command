"""Phase 2 — entry/exit decided by camera role, with missed-event handling."""
from datetime import datetime, timedelta, timezone

import anyio
import pymongo
import pytest

import plate_pipeline
import server

T0 = datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc)  # 10:00 IST


def at(**delta):
    return T0 + timedelta(**delta)


def roles(db, **cams):
    for cam, role in cams.items():
        db.camera_config.update_one({"camera_id": cam}, {"$set": {"camera_id": cam, "role": role, "enabled": True}},
                                    upsert=True)


def persist(client, camera, plate, when, **kw):
    return client.portal.call(lambda: plate_pipeline._persist_entry(server.db, camera, plate, b"", at=when, **kw))


def route(client, camera, plate, confidence="high"):
    return client.portal.call(lambda: plate_pipeline.route_detection(server.db, camera_id=camera, raw_plate=plate,
                                                                     confidence=confidence, reads=3, support=1.0))


@pytest.fixture
def gate(db):
    roles(db, **{"GATE-IN": "entry", "GATE-OUT": "exit"})


def test_entry_then_exit_on_role_cameras(client, db, gate):
    e = persist(client, "GATE-IN", "KA05MG9999", at())
    x = persist(client, "GATE-OUT", "KA05MG9999", at(minutes=10))
    assert e["event"] == "entry" and x["event"] == "exit"
    s = db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})
    assert s["status"] == "completed" and s["duration_seconds"] == 600
    assert s["entry_camera"] == "GATE-IN" and s["exit_camera"] == "GATE-OUT" and s["exit_matched_by"] == "exact"
    assert db.visit_sessions.count_documents({}) == 1


def test_entry_camera_never_closes_a_visit(client, db, gate):
    persist(client, "GATE-IN", "KA05MG9999", at())
    again = persist(client, "GATE-IN", "KA05MG9999", at(seconds=40))
    assert again["duplicate"] is True  # same car still at the entry camera
    assert db.visit_sessions.count_documents({"status": "active"}) == 1


def test_entry_seen_again_marks_old_visit_exit_missed(client, db, gate):
    first = persist(client, "GATE-IN", "KA05MG9999", at())
    second = persist(client, "GATE-IN", "KA05MG9999", at(hours=3))
    assert second["event"] == "entry" and second["closed_exit_missed"] == first["session"]["id"]
    old = db.visit_sessions.find_one({"id": first["session"]["id"]})
    assert old["status"] == "exit_missed" and old["exit_time"] is None  # no invented exit time
    assert db.visit_sessions.count_documents({"status": "active"}) == 1


def test_exit_camera_never_opens_a_visit(client, db, gate):
    r = route(client, "GATE-OUT", "KA05MG9999")
    assert r["queued_for_review"] is True
    assert db.visit_sessions.count_documents({}) == 0
    review = db.plate_reviews.find_one({"id": r["review_id"]})
    assert "no open entry" in review["reason"]


def test_confirmed_unmatched_exit_is_stored_as_entry_missed(client, db, admin_headers, gate):
    rid = route(client, "GATE-OUT", "KA05MG9999")["review_id"]
    res = client.post(f"/api/plate-reviews/{rid}/accept", headers=admin_headers, json={"plate": "KA05MG9999"}).json()
    assert res["event"] == "exit"
    s = db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})
    assert s["status"] == "entry_missed" and s["entry_time"] is None and s["exit_time"]
    stats = client.get("/api/dashboard/stats", headers=admin_headers).json()
    assert stats["cars_inside"]["value"] == 0


def test_fuzzy_exit_closes_the_single_near_match(client, db, gate):
    persist(client, "GATE-IN", "KA05MG9999", at())
    r = route(client, "GATE-OUT", "KA05MG9998")  # one-character OCR slip at the exit
    assert r["event"] == "exit" and r["plate"] == "KA05MG9999" and r["read_plate"] == "KA05MG9998"
    s = db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})
    assert s["status"] == "completed" and s["exit_matched_by"] == "fuzzy" and s["exit_plate_read"] == "KA05MG9998"
    assert db.vehicle_masters.find_one({"vehicle_number": "KA05MG9998"}) is None


def test_fuzzy_exit_with_two_candidates_goes_to_review(client, db, gate):
    persist(client, "GATE-IN", "KA05MG9999", at())
    persist(client, "GATE-IN", "KA05MG9990", at(minutes=1))
    r = route(client, "GATE-OUT", "KA05MG9998")
    assert r["queued_for_review"] is True
    assert db.visit_sessions.count_documents({"status": "active"}) == 2
    assert set(db.plate_reviews.find_one({"id": r["review_id"]})["suggestions"]) == {"KA05MG9999", "KA05MG9990"}


def test_reviewer_plate_is_taken_exactly(client, db, admin_headers, gate):
    """A person typing a plate in review must not be fuzzy-matched to another car."""
    persist(client, "GATE-IN", "KA05MG9999", at())
    rid = route(client, "GATE-OUT", "KA05MG9998", confidence="low")["review_id"]
    client.post(f"/api/plate-reviews/{rid}/accept", headers=admin_headers, json={"plate": "KA05MG9998"})
    assert db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})["status"] == "active"
    assert db.visit_sessions.find_one({"vehicle_number": "KA05MG9998"})["status"] == "entry_missed"


def test_unconfigured_camera_toggles_with_minimum_visit(client, db):
    assert persist(client, "CAM-01", "MH12AB1234", at())["event"] == "entry"
    assert persist(client, "CAM-01", "MH12AB1234", at(seconds=60))["duplicate"] is True
    assert persist(client, "CAM-01", "MH12AB1234", at(minutes=5))["event"] == "exit"
    assert persist(client, "CAM-01", "MH12AB1234", at(minutes=5, seconds=10))["duplicate"] is True  # cooldown


def test_disabled_camera_is_ignored(client, db):
    db.camera_config.insert_one({"camera_id": "CAM-09", "role": "entry", "enabled": False})
    assert persist(client, "CAM-09", "KA05MG9999", at())["ignored"] is True
    assert db.visit_sessions.count_documents({}) == 0


def test_concurrent_detections_create_one_visit(client, db, gate):
    async def burst():
        results = []

        async def one(i):
            results.append(await plate_pipeline._persist_entry(server.db, "GATE-IN", "KA05MG9999", b"", at=at(seconds=i)))
        async with anyio.create_task_group() as tg:
            for i in range(10):
                tg.start_soon(one, i)
        return results

    results = client.portal.call(burst)
    assert sum(1 for r in results if r["event"] == "entry" and not r["duplicate"]) == 1
    assert db.visit_sessions.count_documents({"vehicle_number": "KA05MG9999"}) == 1


def test_database_enforces_one_open_visit_per_plate(client, db):
    db.visit_sessions.insert_one({"id": "a", "vehicle_number": "KA05MG9999", "status": "active"})
    with pytest.raises(pymongo.errors.DuplicateKeyError):
        db.visit_sessions.insert_one({"id": "b", "vehicle_number": "KA05MG9999", "status": "active"})
    db.visit_sessions.insert_one({"id": "c", "vehicle_number": "KA05MG9999", "status": "completed"})


def test_manual_entry_respects_the_index(client, admin_headers):
    body = {"vehicle_number": "KA05MG9999", "owner_name": "Walk-in"}
    assert client.post("/api/vehicles/entry", headers=admin_headers, json=body).status_code == 200
    assert client.post("/api/vehicles/entry", headers=admin_headers, json=body).status_code == 409


def test_long_open_visits(client, db, admin_headers):
    now = datetime.now(timezone.utc)
    db.visit_sessions.insert_many([
        {"id": "old", "vehicle_number": "KA05MG9999", "status": "active", "entry_time": (now - timedelta(hours=30)).isoformat()},
        {"id": "new", "vehicle_number": "MH12AB1234", "status": "active", "entry_time": (now - timedelta(hours=2)).isoformat()},
    ])
    r = client.get("/api/visit-sessions/long-open", headers=admin_headers).json()
    assert [s["id"] for s in r["sessions"]] == ["old"]
    assert [s["id"] for s in client.get("/api/visit-sessions/long-open?hours=1", headers=admin_headers).json()["sessions"]] == ["old", "new"]
    stats = client.portal.call(server._collect_digest_stats)
    assert stats["long_open"] == 1 and stats["long_open_plates"] == ["KA05MG9999"]


def test_retention_purges_old_entry_missed(client, db):
    old = (datetime.now(timezone.utc) - timedelta(days=server.RETENTION_DAYS + 1)).isoformat()
    db.visit_sessions.insert_one({"id": "m", "vehicle_number": "KA05MG9999", "status": "entry_missed",
                                  "entry_time": None, "exit_time": old})
    assert client.portal.call(server.purge_expired_sessions)["deleted"] == 1


def test_camera_config_api(client, db, admin_headers):
    db.agents.insert_one({"agent_id": "pc", "cameras": ["CAM-NEW"]})
    rows = client.get("/api/camera-config", headers=admin_headers).json()
    assert {"camera_id": "CAM-NEW", "configured": False, "role": "both"}.items() <= next(r for r in rows if r["camera_id"] == "CAM-NEW").items()
    r = client.put("/api/camera-config/cam-new", headers=admin_headers, json={"role": "exit", "name": "Main gate out"})
    assert r.json()["camera_id"] == "CAM-NEW" and r.json()["role"] == "exit"
    assert client.portal.call(lambda: plate_pipeline.camera_settings(server.db, "CAM-NEW"))["role"] == "exit"
    assert client.put("/api/camera-config/X", headers=admin_headers, json={"role": "sideways"}).status_code == 422
    assert db.audit_log.count_documents({"action": "camera.config"}) == 1


def test_exit_camera_over_websocket(client, db, agent_ws):
    roles(db, **{"GATE-OUT": "exit"})
    with agent_ws() as ws:
        ws.send_json({"type": "plate_detected", "camera_id": "GATE-OUT", "plate": "KA05MG9999", "confidence": "high"})
        ack = ws.receive_json()
    assert ack["queued_for_review"] is True and ack["event"] is None
