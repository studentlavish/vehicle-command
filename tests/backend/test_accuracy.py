"""Phase 4 — risky corrections, known plates, no-plate vehicles, labelled data."""
import base64
import csv
import io
import zipfile

import plate_pipeline
import server
from conftest import TINY_JPEG, login, make_user


def route(client, plate, confidence="high", camera="CAM-01", **kw):
    return client.portal.call(lambda: plate_pipeline.route_detection(server.db, camera_id=camera, raw_plate=plate,
                                                                     confidence=confidence, reads=3, support=1.0, **kw))


def test_risky_server_side_fix_is_not_trusted_fully(client, db):
    r = route(client, "KA05MG999T")  # valid only as KA05MG9997 after T→7
    assert r["queued_for_review"] is True
    assert db.plate_reviews.find_one({"id": r["review_id"]})["confidence"] == "medium"
    assert route(client, "KAO5MG9999")["event"] == "entry"  # a safe O→0 fix is still saved


def test_known_plate_medium_read_is_saved(client, db, admin_headers):
    assert route(client, "MH12AB1234", confidence="medium")["queued_for_review"] is True
    client.post("/api/known-plates", headers=admin_headers, json={"plate": "MH12AB1234", "kind": "demo"})
    assert route(client, "MH12AB1234", confidence="medium")["event"] == "entry"


def test_known_trade_plate_is_accepted_despite_format(client, db, admin_headers):
    assert route(client, "KA01TC123")["queued_for_review"] is True
    r = client.post("/api/known-plates", headers=admin_headers, json={"plate": "ka-01-tc-123", "kind": "trade"})
    assert r.json()["plate"] == "KA01TC123" and r.json()["standard"] is False
    assert route(client, "KA01TC123")["event"] == "entry"


def test_known_plates_api(client, admin_headers):
    assert client.post("/api/known-plates", headers=admin_headers, json={"plate": "KA05MG9999"}).status_code == 200
    assert client.post("/api/known-plates", headers=admin_headers, json={"plate": "KA05MG9999"}).status_code == 409
    assert client.post("/api/known-plates", headers=admin_headers, json={"plate": "X1"}).status_code == 400
    assert [p["plate"] for p in client.get("/api/known-plates", headers=admin_headers).json()] == ["KA05MG9999"]
    guard = login(client, *(lambda u: (u[0]["email"], u[1]))(make_user(client, admin_headers, "security")))
    assert client.get("/api/known-plates", headers=guard).status_code == 403
    assert client.delete("/api/known-plates/KA05MG9999", headers=admin_headers).status_code == 200


def test_vehicle_without_plate_goes_to_review(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json({"type": "plate_detected", "event_id": "np-1", "camera_id": "CAM-01", "plate": "",
                      "confidence": "none", "crop_jpeg_b64": base64.b64encode(TINY_JPEG).decode()})
        ack = ws.receive_json()
    assert ack["queued_for_review"] is True and ack["event_id"] == "np-1"
    review = db.plate_reviews.find_one({"id": ack["review_id"]})
    assert review["kind"] == "no_plate" and review["image"]
    assert db.visit_sessions.count_documents({}) == 0


def test_message_without_camera_is_still_answered(client, agent_ws):
    with agent_ws() as ws:
        ws.send_json({"type": "plate_detected", "event_id": "bad-1", "plate": "KA05MG9999"})
        assert ws.receive_json() == {"type": "plate_ack", "event_id": "bad-1", "error": "camera_id missing"}


def test_reviewer_can_save_nonstandard_plate(client, db, admin_headers):
    rid = route(client, "")["review_id"]
    bad = client.post(f"/api/plate-reviews/{rid}/accept", headers=admin_headers, json={"plate": "KA01TC123"})
    assert bad.status_code == 400 and "Non-standard" in bad.json()["detail"]
    ok = client.post(f"/api/plate-reviews/{rid}/accept", headers=admin_headers,
                     json={"plate": "KA01TC123", "nonstandard": True})
    assert ok.json()["event"] == "entry"
    assert db.visit_sessions.find_one({"vehicle_number": "KA01TC123"})["status"] == "active"


def test_review_decisions_become_labels_and_export(client, db, admin_headers):
    r1 = route(client, "KA05MG9998", confidence="low", plate_jpeg=TINY_JPEG)
    r2 = route(client, "MH12AB1234", confidence="low", plate_jpeg=TINY_JPEG)
    r3 = route(client, "DL3CAB1234", confidence="low", plate_jpeg=TINY_JPEG)
    client.post(f"/api/plate-reviews/{r1['review_id']}/accept", headers=admin_headers, json={"plate": "KA05MG9999"})  # corrected
    client.post(f"/api/plate-reviews/{r2['review_id']}/accept", headers=admin_headers, json={"plate": "MH12AB1234"})  # confirmed
    client.post(f"/api/plate-reviews/{r3['review_id']}/reject", headers=admin_headers)                               # not a plate

    assert client.get("/api/ocr-labels/stats", headers=admin_headers).json() == \
        {"total": 3, "corrected": 1, "rejected": 1, "confirmed": 1}
    label = db.ocr_labels.find_one({"review_id": r1["review_id"]})
    assert label["plate"] == "KA05MG9999" and label["model_plate"] == "KA05MG9998" and label["corrected"] is True

    res = client.get("/api/ocr-labels/export", headers=admin_headers)
    assert res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    rows = list(csv.DictReader(io.StringIO(z.read("labels.csv").decode())))
    assert sorted(r["plate"] or "NOTAPLATE" for r in rows) == ["KA05MG9999", "MH12AB1234", "NOTAPLATE"]
    assert all(z.read(r["file"]) == TINY_JPEG for r in rows)
    assert any(n.startswith("images/KA05MG9999_") for n in z.namelist())  # benchmark naming

    only = zipfile.ZipFile(io.BytesIO(client.get("/api/ocr-labels/export?only_corrected=true", headers=admin_headers).content))
    assert len([n for n in only.namelist() if n.startswith("images/")]) == 1
