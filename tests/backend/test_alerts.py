"""Phase 6 — alerts, readiness, logging, digest metrics."""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

import alerts
import logging_setup
import server
from conftest import ADMIN_EMAIL, login, make_user

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=3)


def evaluate(client, *, cameras=None, disk=None, started_at=LONG_AGO, backups=False, long_open=()):
    return client.portal.call(lambda: alerts.evaluate(server.db, NOW, cameras=cameras or {}, disk=disk or {"healthy": True},
                                                      started_at=started_at, backups_enabled=backups,
                                                      long_open=list(long_open)))


def rules(findings):
    return sorted(f.key for f in findings)


def iso(dt):
    return dt.isoformat()


def test_offline_camera_and_agent(client, db):
    db.camera_config.insert_many([{"camera_id": "GATE-IN", "role": "entry", "enabled": True},
                                  {"camera_id": "OLD-CAM", "role": "both", "enabled": False}])
    db.agents.insert_one({"agent_id": "pc-1", "cameras": ["GATE-OUT", "OLD-CAM"],
                          "last_heartbeat": iso(NOW - timedelta(minutes=5))})
    live = {"GATE-OUT": {"last_frame_at": iso(NOW - timedelta(seconds=5))}}
    assert rules(evaluate(client, cameras=live)) == ["agent_offline:pc-1", "camera_offline:GATE-IN"]
    db.agents.update_one({"agent_id": "pc-1"}, {"$set": {"last_heartbeat": iso(NOW - timedelta(seconds=20))}})
    live["GATE-IN"] = {"last_frame_at": iso(NOW - timedelta(seconds=30))}
    assert evaluate(client, cameras=live) == []


def test_no_offline_alerts_right_after_a_restart(client, db):
    db.camera_config.insert_one({"camera_id": "GATE-IN", "role": "entry", "enabled": True})
    db.agents.insert_one({"agent_id": "pc-1", "last_heartbeat": None})
    assert evaluate(client, started_at=NOW - timedelta(seconds=30)) == []


def test_outbox_backlog(client, db):
    db.agents.insert_one({"agent_id": "pc-1", "last_heartbeat": iso(NOW), "health": {"outbox_depth": 25}})
    assert rules(evaluate(client)) == ["outbox_backlog:pc-1"]


def test_review_backlog(client, db):
    db.plate_reviews.insert_one({"id": "r0", "status": "pending", "created_at": iso(NOW - timedelta(minutes=5))})
    assert evaluate(client) == []
    db.plate_reviews.insert_one({"id": "r1", "status": "pending", "created_at": iso(NOW - timedelta(hours=2))})
    assert rules(evaluate(client)) == ["review_backlog"]
    db.plate_reviews.delete_many({})
    db.plate_reviews.insert_many([{"id": f"p{i}", "status": "pending", "created_at": iso(NOW)} for i in range(11)])
    assert "11 plate reads" in evaluate(client)[0].message


def test_storage_and_long_open(client):
    found = evaluate(client, disk={"healthy": False, "disk_free_bytes": 300 * 1024 * 1024},
                     long_open=[{"vehicle_number": "KA05MG9999"}])
    assert rules(found) == ["long_open", "storage_low"]
    assert "300 MB" in next(f for f in found if f.rule == "storage_low").message


@pytest.mark.parametrize("docs,fires", [
    ([], True),  # running for 3 days and never backed up
    ([{"status": "success", "created_at": iso(NOW - timedelta(hours=5))}], False),
    ([{"status": "success", "created_at": iso(NOW - timedelta(hours=40))}], True),
    ([{"status": "success", "created_at": iso(NOW - timedelta(hours=30))},
      {"status": "failed", "error": "mongodump exited 1", "created_at": iso(NOW - timedelta(hours=5))}], True),
])
def test_backup_alert(client, db, docs, fires):
    if docs:
        db.backups.insert_many([{"kind": "mongodb", **d} for d in docs])
    assert (rules(evaluate(client, backups=True)) == ["backup_failed"]) is fires


def test_no_backup_alert_on_a_fresh_install(client):
    assert evaluate(client, backups=True, started_at=NOW - timedelta(hours=2)) == []


def test_reconcile_fires_repeats_and_resolves(client, db):
    sent = []

    async def notify(kind, doc):
        sent.append((kind, doc["key"]))
    f = [alerts.Finding("camera_offline:GATE-IN", "camera_offline", "critical", "Camera GATE-IN is offline")]
    run = lambda findings, when: client.portal.call(lambda: alerts.reconcile(server.db, findings, when, notify))  # noqa: E731
    run(f, NOW)
    run(f, NOW + timedelta(minutes=30))                     # still firing — no repeat yet
    run(f, NOW + timedelta(hours=alerts.REPEAT_HOURS, minutes=1))
    run([], NOW + timedelta(hours=7))                       # fixed
    run([], NOW + timedelta(hours=8))
    assert sent == [("fired", "camera_offline:GATE-IN"), ("repeat", "camera_offline:GATE-IN"),
                    ("resolved", "camera_offline:GATE-IN")]
    assert db.alerts.find_one({"key": "camera_offline:GATE-IN"})["status"] == "resolved"


def test_alert_check_notifies_admins_by_email(client, db, admin_headers, monkeypatch):
    sent = []

    async def fake_send(*, to, subject, html, reply_to=None):
        sent.append((to, subject))
    monkeypatch.setattr(server, "_send_email", fake_send)
    monkeypatch.setattr(server, "_email_provider", lambda: "resend")
    monkeypatch.setattr(server, "SERVER_STARTED_AT", LONG_AGO)
    db.camera_config.insert_one({"camera_id": "GATE-IN", "role": "entry", "enabled": True})
    result = client.portal.call(server.run_alert_check)
    assert result["fired"] == ["camera_offline:GATE-IN"]
    assert sent == [(ADMIN_EMAIL, "[Vehicle Command] ALERT: Camera GATE-IN is offline — no picture since the server started")]
    firing = client.get("/api/alerts", headers=admin_headers).json()["firing"]
    assert firing[0]["key"] == "camera_offline:GATE-IN" and isinstance(firing[0]["first_at"], str)
    guard = login(client, *(lambda u: (u[0]["email"], u[1]))(make_user(client, admin_headers, "security")))
    assert client.get("/api/alerts", headers=guard).status_code == 403


def test_readiness(client, monkeypatch):
    r = client.get("/api/ready")
    assert r.status_code == 200 and r.json() == {"ok": True, "checks": {"database": "ok", "storage": "ok"}}

    async def down(*a, **k):
        raise ConnectionError("no db")
    monkeypatch.setattr(server.db, "command", down)
    r = client.get("/api/ready")
    assert r.status_code == 503 and r.json()["checks"]["database"].startswith("error")


def test_json_log_format():
    rec = logging.LogRecord("rdx.test", logging.WARNING, __file__, 1, "camera %s offline", ("GATE-IN",), None)
    out = json.loads(logging_setup.JsonFormatter().format(rec))
    assert out["level"] == "WARNING" and out["logger"] == "rdx.test" and out["msg"] == "camera GATE-IN offline"


def test_digest_reports_automation(client, db):
    tz = server.APP_TZ
    yday = (datetime.now(tz) - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    db.visit_sessions.insert_many([
        {"id": "a", "vehicle_number": "KA05MG9999", "entry_time": iso(yday), "detected_by": "agent_yolo",
         "exit_time": iso(yday + timedelta(hours=1)), "exit_confidence": "high", "status": "completed"},
        {"id": "b", "vehicle_number": "MH12AB1234", "entry_time": iso(yday), "detected_by": "review", "status": "active"},
    ])
    db.plate_reviews.insert_one({"id": "r", "status": "accepted", "created_at": iso(yday)})
    db.ocr_labels.insert_one({"id": "l", "corrected": True, "at": iso(yday)})
    s = client.portal.call(server._collect_digest_stats)
    assert (s["auto_saved"], s["sent_to_review"], s["review_rate"], s["corrections"]) == (2, 1, "33.3%", 1)
    html = server._render_digest_html(s)
    assert "Saved automatically" in html and "33.3%" in html
