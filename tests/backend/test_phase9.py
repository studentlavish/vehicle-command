"""Phase 9 — test drives, staffing insights, per-camera accuracy, richer digest, WhatsApp PDF."""
from datetime import datetime, timedelta, timezone

import httpx

import alerts
import analytics
import digest_pdf
import email_utils
import server
import test_drives
import whatsapp_meta
from conftest import TINY_JPEG, login, make_user

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat()


# ---------- Test drives ----------
def test_test_drive_out_in_and_driver_note(client, db, admin_headers):
    db.known_plates.insert_many([{"plate": "KA05MG9999", "kind": "test_drive", "label": "Creta demo"},
                                 {"plate": "MH12AB1234", "kind": "test_drive", "label": "Venue demo"},
                                 {"plate": "DL3CAB1234", "kind": "staff"}])
    left = NOW - timedelta(minutes=75)
    db.visit_sessions.insert_many([
        # Creta: parked, left 75 min ago, not back → out and overdue
        {"id": "1", "vehicle_number": "KA05MG9999", "entry_time": iso(NOW - timedelta(days=1)),
         "exit_time": iso(left), "status": "completed"},
        # Venue: out this morning, back 10 min ago → in, plus one finished trip today
        {"id": "2", "vehicle_number": "MH12AB1234", "entry_time": iso(NOW - timedelta(days=1)),
         "exit_time": iso(NOW - timedelta(minutes=40)), "status": "completed"},
        {"id": "3", "vehicle_number": "MH12AB1234", "entry_time": iso(NOW - timedelta(minutes=10)), "status": "active"},
    ])
    guard, pw = make_user(client, admin_headers, "security")
    h = login(client, guard["email"], pw)

    r = client.get("/api/test-drives", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["plate"] for t in body["out"]] == ["KA05MG9999"]
    out = body["out"][0]
    assert out["overdue"] is True and 74 <= out["minutes_out"] <= 76 and out["label"] == "Creta demo"
    assert [t["plate"] for t in body["in"]] == ["MH12AB1234"]
    assert body["max_minutes"] == 60

    r = client.put("/api/test-drives/KA05MG9999/trip", headers=h, json={"driver": "Ravi", "customer": "Mr Sharma"})
    assert r.status_code == 200 and r.json()["driver"] == "Ravi"
    assert client.get("/api/test-drives", headers=h).json()["out"][0]["customer"] == "Mr Sharma"
    # Only a car that is out can get a trip note
    assert client.put("/api/test-drives/MH12AB1234/trip", headers=h, json={"driver": "x"}).status_code == 409

    findings = client.portal.call(lambda: alerts.evaluate(
        server.db, NOW, cameras={}, disk={"healthy": True}, started_at=NOW - timedelta(days=1),
        backups_enabled=False, long_open=[],
        test_drives_out=[out | {"driver": "Ravi"}]))
    td = [f for f in findings if f.rule == "test_drive_overdue"]
    assert len(td) == 1 and "KA05MG9999" in td[0].message and "Ravi" in td[0].message


def test_finished_trip_pairs_exit_with_next_entry(client, db):
    db.known_plates.insert_one({"plate": "KA05MG9999", "kind": "test_drive"})
    out_at, back_at = NOW - timedelta(hours=2), NOW - timedelta(hours=1, minutes=25)
    db.visit_sessions.insert_many([
        {"id": "1", "vehicle_number": "KA05MG9999", "entry_time": iso(NOW - timedelta(days=2)),
         "exit_time": iso(out_at), "status": "completed"},
        {"id": "2", "vehicle_number": "KA05MG9999", "entry_time": iso(back_at), "status": "active"},
    ])
    db.test_drives.insert_one({"plate": "KA05MG9999", "out_at": iso(out_at), "driver": "Asha"})
    trips = client.portal.call(lambda: test_drives.trips_between(server.db, iso(NOW - timedelta(hours=3)), iso(NOW)))
    assert trips == [{"plate": "KA05MG9999", "label": "", "out_at": iso(out_at), "back_at": iso(back_at),
                      "minutes": 35, "driver": "Asha", "customer": ""}]


# ---------- Analytics ----------
def test_visit_insights_heatmap_repeat_and_dwell(client, db, admin_headers):
    tz = server.APP_TZ
    base = datetime.now(tz).replace(hour=11, minute=15, second=0, microsecond=0) - timedelta(days=2)
    rows = [
        # KA05 visited 40 days ago too → returning in the window
        {"vehicle_number": "KA05MG9999", "entry_time": base - timedelta(days=40), "duration_seconds": 600},
        {"vehicle_number": "KA05MG9999", "entry_time": base, "duration_seconds": 3600},
        # MH12 first ever visit in the window, then a second one → 1 new + 1 returning
        {"vehicle_number": "MH12AB1234", "entry_time": base - timedelta(days=1), "duration_seconds": 1200},
        {"vehicle_number": "MH12AB1234", "entry_time": base + timedelta(minutes=10), "duration_seconds": 2400},
        # DL3C: one new visit, still inside
        {"vehicle_number": "DL3CAB1234", "entry_time": base + timedelta(minutes=20), "status": "active"},
    ]
    db.visit_sessions.insert_many([{"id": str(i), "status": r.get("status", "completed"), **r,
                                    "entry_time": iso(r["entry_time"].astimezone(timezone.utc))}
                                   for i, r in enumerate(rows)])
    r = client.get("/api/analytics/insights", headers=admin_headers, params={"days": 28})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["heatmap"][base.weekday()][11] == 3
    assert s["busiest"] == {"weekday": base.weekday(), "hour": 11, "entries": 3}
    assert s["repeat"] == {"unique_vehicles": 3, "returning_vehicles": 2, "rate": 0.667}
    assert s["dwell"] == {"new_avg_seconds": 1200, "new_visits": 1,
                          "returning_avg_seconds": 3000, "returning_visits": 2}


def test_insights_are_management_only(client, admin_headers):
    guard, pw = make_user(client, admin_headers, "security")
    h = login(client, guard["email"], pw)
    assert client.get("/api/analytics/insights", headers=h).status_code == 403
    assert client.get("/api/analytics/accuracy", headers=h).status_code == 403


def test_accuracy_by_camera(client, db, admin_headers):
    t = NOW - timedelta(hours=1)
    db.visit_sessions.insert_many([
        {"id": "1", "vehicle_number": "A", "entry_time": iso(t), "entry_camera": "GATE-IN", "entry_confidence": "high",
         "exit_time": iso(t), "exit_camera": "GATE-OUT", "exit_confidence": "medium"},
        {"id": "2", "vehicle_number": "B", "entry_time": iso(t), "entry_camera": "GATE-IN", "entry_confidence": "high"},
        {"id": "3", "vehicle_number": "C", "entry_time": iso(t), "entry_camera": "GATE-IN", "entry_confidence": "confirmed"},
    ])
    db.plate_reviews.insert_many([
        {"id": "r1", "camera_id": "GATE-IN", "seen_at": iso(t), "status": "accepted", "plate": "C", "final_plate": "C"},
        {"id": "r2", "camera_id": "GATE-IN", "seen_at": iso(t), "status": "accepted", "plate": "X", "final_plate": "D"},
        {"id": "r3", "camera_id": "GATE-OUT", "seen_at": iso(t), "status": "rejected", "plate": None},
    ])
    r = client.get("/api/analytics/accuracy", headers=admin_headers, params={"days": 7})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["cameras"] == ["GATE-IN", "GATE-OUT"]
    totals = {x["camera"]: x for x in s["totals"]}
    assert (totals["GATE-IN"]["auto"], totals["GATE-IN"]["reviewed"], totals["GATE-IN"]["corrected"]) == (2, 2, 1)
    assert totals["GATE-IN"]["auto_rate"] == 0.5 and totals["GATE-IN"]["correction_rate"] == 0.5
    assert (totals["GATE-OUT"]["auto"], totals["GATE-OUT"]["rejected"]) == (1, 1)
    today = datetime.now(server.APP_TZ).date().isoformat()
    last = s["series"][-1]
    if last["date"] == t.astimezone(server.APP_TZ).date().isoformat() == today:
        assert last["GATE-IN"] == 50.0 and last["GATE-OUT"] == 50.0
    assert len(s["series"]) == 7


def test_accuracy_buckets_use_showroom_timezone(client, db):
    tz = server.APP_TZ
    local_midnight = datetime.now(tz).replace(hour=0, minute=30, second=0, microsecond=0)
    db.visit_sessions.insert_one({"id": "1", "vehicle_number": "A", "entry_camera": "CAM", "entry_confidence": "high",
                                  "entry_time": iso(local_midnight.astimezone(timezone.utc))})
    s = client.portal.call(lambda: analytics.accuracy_by_camera(server.db, tz, days=7))
    row = next(r for r in s["series"] if r["date"] == local_midnight.date().isoformat())
    assert row["CAM"] == 100.0


# ---------- Digest ----------
def test_digest_week_over_week_and_busiest_hour(client, db, snapshots_dir):
    tz = server.APP_TZ
    yday = (datetime.now(tz) - timedelta(days=1)).replace(hour=10, minute=5, second=0, microsecond=0)
    week = yday - timedelta(days=7)
    (snapshots_dir / "first.jpg").write_bytes(TINY_JPEG)
    rows = [
        (yday, "KA05MG9999", ""), (yday + timedelta(minutes=10), "MH12AB1234", "/api/snapshots/first.jpg"),
        (yday + timedelta(minutes=20), "DL3CAB1234", "/api/snapshots/later.jpg"),
        (yday + timedelta(hours=3), "KA01AB0001", ""), (week, "KA05MG9999", ""), (week, "MH12AB1234", ""),
    ]
    db.visit_sessions.insert_many([{"id": str(i), "vehicle_number": p, "entry_time": iso(at.astimezone(timezone.utc)),
                                    "entry_image": img, "status": "active" if at > week else "completed"}
                                   for i, (at, p, img) in enumerate(rows)])
    s = client.portal.call(server._collect_digest_stats)
    assert s["entries"] == 4 and s["compare"]["entries"] == 2 and s["deltas"]["entries"] == "+100%"
    assert s["compare"]["label"] == f"last {week.strftime('%A')}"
    assert s["busiest_hour"] == "10:00–11:00" and s["busiest_hour_entries"] == 3
    assert s["busiest_hour_image"] == "/api/snapshots/first.jpg"

    html = server._render_digest_html(s, image_cid=server.DIGEST_IMAGE_CID)
    assert "+100% vs last" in html and 'src="cid:busiest-hour"' in html and "10:00–11:00" in html
    email_utils._assert_safe_email("subject", html)  # guardrails still pass
    assert "cid:" not in server._render_digest_html(s)

    pdf = digest_pdf.build_digest_pdf(s, client.portal.call(lambda: server._digest_image(s)))
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


def test_pct_delta():
    assert [server._pct_delta(*x) for x in [(12, 10), (5, 10), (3, 3), (4, 0), (0, 0)]] == \
           ["+20%", "-50%", "±0%", "new", "±0%"]


def test_digest_email_carries_pdf_and_inline_image(client, db, snapshots_dir, monkeypatch):
    tz = server.APP_TZ
    yday = (datetime.now(tz) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    (snapshots_dir / "snap.jpg").write_bytes(TINY_JPEG)
    db.visit_sessions.insert_one({"id": "1", "vehicle_number": "KA05MG9999", "status": "active",
                                  "entry_time": iso(yday.astimezone(timezone.utc)), "entry_image": "/api/snapshots/snap.jpg"})
    sent = []

    async def fake_send(**kw):
        sent.append(kw)

    monkeypatch.setattr(server, "_send_email", fake_send)
    monkeypatch.setattr(server, "_email_supports_attachments", lambda: True)
    client.portal.call(lambda: server._run_digest_send("t-1"))
    assert sent, "no email sent"
    files = {f["filename"]: f for f in sent[0]["attachments"]}
    assert files["busiest-hour.jpg"]["cid"] == "busiest-hour"
    assert any(n.endswith(".pdf") and f["content"].startswith(b"%PDF") for n, f in files.items())
    assert 'src="cid:busiest-hour"' in sent[0]["html"]


def test_smtp_message_has_related_image_and_pdf(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(email_utils, "EMAIL_FROM", "reports@example.com")
    monkeypatch.setattr(email_utils.smtplib, "SMTP", FakeSMTP)
    email_utils._send_smtp("a@example.com", "s", '<img src="cid:x">', None, [
        {"filename": "x.jpg", "content": TINY_JPEG, "content_type": "image/jpeg", "cid": "x"},
        {"filename": "d.pdf", "content": b"%PDF-1.4", "content_type": "application/pdf"},
    ])
    msg = captured["msg"]
    types = [p.get_content_type() for p in msg.walk()]
    assert "multipart/related" in types and "image/jpeg" in types and "application/pdf" in types
    img = next(p for p in msg.walk() if p.get_content_type() == "image/jpeg")
    assert img["Content-ID"] == "<x>"


# ---------- WhatsApp ----------
def _fake_meta(monkeypatch, calls):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, data=None, files=None):
            calls.append({"url": url, "json": json, "data": data, "files": files})
            req = httpx.Request("POST", url)
            if url.endswith("/media"):
                return httpx.Response(200, json={"id": "MEDIA-1"}, request=req)
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}, request=req)

    monkeypatch.setattr(whatsapp_meta.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(whatsapp_meta, "META_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(whatsapp_meta, "META_PHONE_NUMBER_ID", "123")


def test_whatsapp_digest_template_with_pdf_header(client, db, monkeypatch):
    calls = []
    _fake_meta(monkeypatch, calls)
    monkeypatch.setattr(whatsapp_meta, "META_DIGEST_TEMPLATE", "daily_digest")

    async def cfg():
        return {"to": "919999999999"}

    monkeypatch.setattr(server, "_twilio_config", cfg)
    stats = {"date": "2026-11-19", "entries": 4, "exits": 3, "unique_vehicles": 4}
    client.portal.call(lambda: server._send_digest_whatsapp("t", stats, b"%PDF-1.4 test"))
    assert calls[0]["url"].endswith("/123/media") and calls[0]["data"]["type"] == "application/pdf"
    tpl = calls[1]["json"]["template"]
    assert tpl["name"] == "daily_digest"
    header, body = tpl["components"]
    assert header["parameters"][0]["document"] == {"id": "MEDIA-1", "filename": "vashu-digest-2026-11-19.pdf"}
    assert [p["text"] for p in body["parameters"]] == ["2026-11-19", "4", "3", "4"]
    log = db.whatsapp_log.find_one({"context": "daily_digest"})
    assert log["status"] == "sent" and "tok" not in str(log)


def test_whatsapp_digest_without_template_sends_text_then_document(client, db, monkeypatch):
    calls = []
    _fake_meta(monkeypatch, calls)
    monkeypatch.setattr(whatsapp_meta, "META_DIGEST_TEMPLATE", "")

    async def cfg():
        return {"to": "919999999999"}

    monkeypatch.setattr(server, "_twilio_config", cfg)
    stats = {"date": "2026-11-19", "entries": 0, "exits": 0, "unique_vehicles": 0}
    client.portal.call(lambda: server._send_digest_whatsapp("t", stats, b"%PDF-1.4"))
    kinds = [c["json"]["type"] for c in calls if c["json"]]
    assert kinds == ["text", "document"]
    assert calls[-1]["json"]["document"]["id"] == "MEDIA-1"
