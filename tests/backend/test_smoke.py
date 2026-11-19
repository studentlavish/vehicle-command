"""Baseline API smoke tests: auth, dashboard, reviews, agent WebSocket."""
import base64

from conftest import ADMIN_EMAIL, TINY_JPEG


def test_login_rejects_wrong_password(client):
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "not-the-password"})
    assert r.status_code == 401


def test_login_and_me(client, admin_headers):
    r = client.get("/api/auth/me", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"
    assert "password_hash" not in body


def test_protected_route_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/dashboard/stats").status_code == 401


def test_dashboard_stats_shape(client, admin_headers):
    r = client.get("/api/dashboard/stats", headers=admin_headers)
    assert r.status_code == 200
    stats = r.json()
    for key in ("today_entries", "today_exits", "cars_inside", "total_vehicles", "visitors_today", "monthly_visitors"):
        assert set(stats[key]) >= {"value", "change", "trend"}, key
    assert stats["cars_inside"]["value"] == 0


def test_plate_review_list_starts_empty(client, admin_headers):
    r = client.get("/api/plate-reviews", headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_agent_ws_rejects_bad_secret(client):
    with client.websocket_connect("/api/agent/ws?agent_id=x", headers={"x-agent-secret": "wrong"}) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "unauthorized"}


def test_agent_high_confidence_plate_creates_entry(client, db, agent_ws, snapshots_dir):
    with agent_ws() as ws:
        ws.send_json({
            "type": "plate_detected", "camera_id": "CAM-01", "plate": "KA05MG9999", "confidence": "high",
            "reads": 3, "support": 1.0,
            "crop_jpeg_b64": base64.b64encode(TINY_JPEG).decode(),
            "plate_jpeg_b64": base64.b64encode(TINY_JPEG).decode(),
        })
        ack = ws.receive_json()
    assert ack["type"] == "plate_ack"
    assert ack["event"] == "entry" and ack["duplicate"] is False
    session = db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})
    assert session["status"] == "active"
    assert session["entry_image"].startswith("/api/snapshots/")
    assert len([p for p in snapshots_dir.iterdir() if p.suffix == ".jpg"]) == 2
    assert db.vehicle_masters.find_one({"vehicle_number": "KA05MG9999"})["owner_name"] == "Unknown Owner"


def test_agent_low_confidence_plate_goes_to_review(client, db, agent_ws):
    with agent_ws() as ws:
        ws.send_json({"type": "plate_detected", "camera_id": "CAM-01", "plate": "KA05MG9999",
                      "confidence": "low", "reads": 1, "support": 0.4})
        ack = ws.receive_json()
    assert ack["queued_for_review"] is True
    assert db.visit_sessions.count_documents({}) == 0
    assert db.plate_reviews.count_documents({"status": "pending"}) == 1
