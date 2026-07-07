"""RDX Car Showroom Backend API tests (iteration 2).

Covers:
- Auth (cookie + bearer)
- Vehicle visit sessions (entry / exit / multi-session immutability)
- Vehicle master search + detail + range sessions
- Master PATCH
- /visit-sessions listing + status mapping (active/inside)
- Legacy /vehicles CRUD + status labels
- Dashboard stats (uses vehicle_masters count)
- Analytics overview (with stay_time)
- Reports export (csv/xlsx/pdf)
- /visits/export (csv/xlsx/pdf) with and without vehicle_number
- Users list
- Settings
- Unauthorized (401) checks for all new endpoints
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or "https://vehicle-command-17.preview.emergentagent.com"
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@rdx.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

SHOWCASE_PLATE = "UP21AB1234"


# ---------------- Fixtures ----------------
@pytest.fixture(scope="session")
def anon():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def client():
    """Authenticated session using httpOnly cookies from login."""
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("email") == ADMIN_EMAIL
    assert data.get("role") == "admin"
    assert data.get("token")
    assert "access_token" in s.cookies
    assert "refresh_token" in s.cookies
    s.headers.update({"Authorization": f"Bearer {data['token']}"})
    return s


# ---------------- Auth ----------------
class TestAuth:
    def test_login_success(self, anon):
        r = anon.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        set_cookie = r.headers.get("set-cookie", "")
        assert "access_token" in set_cookie
        assert "httponly" in set_cookie.lower()

    def test_login_wrong_password(self, anon):
        r = anon.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrongpw"})
        assert r.status_code == 401

    def test_me_with_cookies(self, client):
        r = client.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL


# ---------------- Vehicle Visit Sessions core ----------------
class TestVisitSessions:
    def test_entry_creates_new_session_and_master(self, client):
        plate = f"TEST{uuid.uuid4().hex[:6].upper()}"
        r = client.post(f"{API}/vehicles/entry", json={
            "vehicle_number": plate,
            "owner_name": "TEST_Entry_Owner",
            "phone_number": "+91 9000000001",
            "vehicle_model": "Hyundai Test",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["master"]["vehicle_number"] == plate
        assert body["session"]["vehicle_number"] == plate
        assert body["session"]["status"] == "active"
        assert body["session"]["entry_time"]
        assert body["session"]["exit_time"] is None
        # duplicate entry -> another active session (2 total, both active)
        r2 = client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate})
        assert r2.status_code == 200
        # search should now show total_visits = 2, active_visits = 2
        s = client.get(f"{API}/vehicles/search", params={"q": plate}).json()
        found = [x for x in s if x["vehicle_number"] == plate]
        assert found and found[0]["total_visits"] == 2
        assert found[0]["active_visits"] == 2

    def test_exit_closes_latest_active_session_only(self, client):
        plate = f"TEST{uuid.uuid4().hex[:6].upper()}"
        # 2 entries
        e1 = client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate}).json()["session"]
        e2 = client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate}).json()["session"]
        assert e1["id"] != e2["id"]
        # exit -> should close the LATEST one (e2)
        r = client.post(f"{API}/vehicles/exit", json={"vehicle_number": plate})
        assert r.status_code == 200
        closed = r.json()
        assert closed["status"] == "completed"
        assert closed["exit_time"]
        assert closed["id"] == e2["id"], "exit should close latest active session"
        # e1 entry_time is unchanged and still active
        sessions = client.get(f"{API}/vehicles/master/{plate}/sessions", params={"range": "today"}).json()
        by_id = {s["id"]: s for s in sessions}
        assert by_id[e1["id"]]["entry_time"] == e1["entry_time"]
        assert by_id[e1["id"]]["status"] == "active"
        assert by_id[e2["id"]]["status"] == "completed"
        # second exit closes e1
        r2 = client.post(f"{API}/vehicles/exit", json={"vehicle_number": plate})
        assert r2.status_code == 200
        # third exit -> 404
        r3 = client.post(f"{API}/vehicles/exit", json={"vehicle_number": plate})
        assert r3.status_code == 404

    def test_exit_no_active_returns_404(self, client):
        plate = f"NOACT{uuid.uuid4().hex[:5].upper()}"
        # Never entered
        r = client.post(f"{API}/vehicles/exit", json={"vehicle_number": plate})
        assert r.status_code == 404

    def test_entry_exit_immutability(self, client):
        plate = f"IMM{uuid.uuid4().hex[:5].upper()}"
        s1 = client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate}).json()["session"]
        client.post(f"{API}/vehicles/exit", json={"vehicle_number": plate})
        s2 = client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate}).json()["session"]
        # Fetch and ensure entry_time on s1 unchanged
        sessions = client.get(f"{API}/vehicles/master/{plate}/sessions", params={"range": "today"}).json()
        by_id = {s["id"]: s for s in sessions}
        assert by_id[s1["id"]]["entry_time"] == s1["entry_time"]
        assert by_id[s2["id"]]["entry_time"] == s2["entry_time"]

    def test_entry_unauth(self):
        r = requests.post(f"{API}/vehicles/entry", json={"vehicle_number": "XX"})
        assert r.status_code == 401

    def test_exit_unauth(self):
        r = requests.post(f"{API}/vehicles/exit", json={"vehicle_number": "XX"})
        assert r.status_code == 401


# ---------------- Search + Master Detail ----------------
class TestSearchAndDetail:
    def test_search_matches_by_plate(self, client):
        r = client.get(f"{API}/vehicles/search", params={"q": SHOWCASE_PLATE})
        assert r.status_code == 200
        data = r.json()
        assert any(m["vehicle_number"] == SHOWCASE_PLATE for m in data)
        m = [x for x in data if x["vehicle_number"] == SHOWCASE_PLATE][0]
        assert "total_visits" in m and "active_visits" in m
        assert m["total_visits"] >= 4

    def test_search_by_customer_id(self, client):
        r = client.get(f"{API}/vehicles/search", params={"q": "CUS-DEMO01"})
        assert r.status_code == 200
        assert any(m["vehicle_number"] == SHOWCASE_PLATE for m in r.json())

    def test_search_by_owner_name(self, client):
        r = client.get(f"{API}/vehicles/search", params={"q": "Aarav"})
        assert r.status_code == 200
        assert any(m["vehicle_number"] == SHOWCASE_PLATE for m in r.json())

    def test_search_by_phone(self, client):
        r = client.get(f"{API}/vehicles/search", params={"q": "9876543210"})
        assert r.status_code == 200
        assert any(m["vehicle_number"] == SHOWCASE_PLATE for m in r.json())

    def test_master_detail_showcase(self, client):
        r = client.get(f"{API}/vehicles/master/{SHOWCASE_PLATE}")
        assert r.status_code == 200
        body = r.json()
        assert body["master"]["vehicle_number"] == SHOWCASE_PLATE
        assert body["master"]["customer_id"] == "CUS-DEMO01"
        summary = body["summary"]
        assert summary["today_visits"] == 4, f"expected 4 today_visits, got {summary}"
        # today_stay_seconds ~ 4h 10m = 15000s (sum of 45+50+75+80 = 250 min = 15000s)
        assert summary["today_stay_seconds"] == 15000, f"expected 15000s stay, got {summary['today_stay_seconds']}"
        assert summary["month_visits"] >= 4
        assert summary["overall_visits"] >= 4
        ana = body["analytics"]
        assert ana["avg_seconds"] > 0
        assert ana["longest_seconds"] >= ana["avg_seconds"]
        assert ana["shortest_seconds"] <= ana["avg_seconds"]

    def test_master_sessions_today_exact(self, client):
        r = client.get(f"{API}/vehicles/master/{SHOWCASE_PLATE}/sessions", params={"range": "today"})
        assert r.status_code == 200
        sessions = r.json()
        assert len(sessions) == 4, f"expected 4 sessions today, got {len(sessions)}"
        # ascending by entry_time; check H:M endings
        hm = [s["entry_time"][11:16] for s in sessions]
        assert hm == ["08:30", "10:20", "13:45", "17:10"], hm

    def test_master_sessions_ranges(self, client):
        for rng in ["today", "yesterday", "7d", "30d", "180d"]:
            r = client.get(f"{API}/vehicles/master/{SHOWCASE_PLATE}/sessions", params={"range": rng})
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_master_sessions_custom_range(self, client):
        from datetime import date, timedelta
        today = date.today()
        r = client.get(f"{API}/vehicles/master/{SHOWCASE_PLATE}/sessions",
                       params={"range": "custom", "from_": (today - timedelta(days=1)).isoformat(),
                               "to": today.isoformat()})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_master_detail_404(self, client):
        r = client.get(f"{API}/vehicles/master/DOESNOTEXIST99")
        assert r.status_code == 404

    def test_master_patch(self, client):
        # Create a temporary master via entry
        plate = f"PATCH{uuid.uuid4().hex[:5].upper()}"
        client.post(f"{API}/vehicles/entry", json={"vehicle_number": plate, "owner_name": "Old"})
        r = client.patch(f"{API}/vehicles/master/{plate}",
                         json={"owner_name": "TEST_New Owner", "phone_number": "+91 8888888888", "vehicle_model": "Hyundai Kona"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["owner_name"] == "TEST_New Owner"
        assert body["phone_number"] == "+91 8888888888"
        assert body["vehicle_model"] == "Hyundai Kona"

    def test_master_patch_404(self, client):
        r = client.patch(f"{API}/vehicles/master/DOESNOTEXIST", json={"owner_name": "X"})
        assert r.status_code == 404

    def test_search_unauth(self):
        r = requests.get(f"{API}/vehicles/search", params={"q": "UP"})
        assert r.status_code == 401

    def test_master_unauth(self):
        r = requests.get(f"{API}/vehicles/master/{SHOWCASE_PLATE}")
        assert r.status_code == 401


# ---------------- /visit-sessions global list ----------------
class TestVisitSessionsList:
    def test_active_status_filter(self, client):
        r = client.get(f"{API}/visit-sessions", params={"status": "active"})
        assert r.status_code == 200
        data = r.json()
        # legacy serialization maps 'active' -> 'inside' in status field
        for d in data[:20]:
            assert d["status"] == "inside"

    def test_inside_status_alias(self, client):
        # 'inside' should map to 'active'
        r = client.get(f"{API}/visit-sessions", params={"status": "inside"})
        assert r.status_code == 200
        for d in r.json()[:20]:
            assert d["status"] == "inside"

    def test_unauth(self):
        r = requests.get(f"{API}/visit-sessions")
        assert r.status_code == 401


# ---------------- Legacy /vehicles ----------------
class TestLegacyVehicles:
    def test_list_seeded(self, client):
        r = client.get(f"{API}/vehicles")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) > 0
        d0 = data[0]
        assert "owner_name" in d0
        assert "contact_number" in d0
        assert "vehicle_model" in d0
        # legacy status labels
        assert d0["status"] in ("inside", "exited", "pending")
        assert "_id" not in d0

    def test_filter_status_inside(self, client):
        r = client.get(f"{API}/vehicles", params={"status": "inside"})
        assert r.status_code == 200
        for v in r.json():
            assert v["status"] == "inside"

    def test_search_query(self, client):
        r = client.get(f"{API}/vehicles", params={"q": SHOWCASE_PLATE})
        assert r.status_code == 200
        assert any(v["vehicle_number"] == SHOWCASE_PLATE for v in r.json())

    def test_crud_flow(self, client):
        payload = {
            "vehicle_number": f"TEST{uuid.uuid4().hex[:6].upper()}",
            "owner_name": "TEST_Owner",
            "contact_number": "+91 9999999999",
            "vehicle_model": "Hyundai Creta",
            "status": "inside",
            "notes": "created by pytest",
        }
        r = client.post(f"{API}/vehicles", json=payload)
        assert r.status_code == 200, r.text
        v = r.json()
        vid = v["id"]
        assert v["vehicle_number"] == payload["vehicle_number"]

        r = client.get(f"{API}/vehicles/{vid}")
        assert r.status_code == 200

        r = client.patch(f"{API}/vehicles/{vid}", json={"status": "exited"})
        assert r.status_code == 200
        assert r.json()["status"] == "exited"
        assert r.json()["exit_time"]

        r = client.delete(f"{API}/vehicles/{vid}")
        assert r.status_code == 200
        r = client.get(f"{API}/vehicles/{vid}")
        assert r.status_code == 404


# ---------------- Dashboard ----------------
class TestDashboard:
    def test_dashboard_stats_shape(self, client):
        r = client.get(f"{API}/dashboard/stats")
        assert r.status_code == 200
        data = r.json()
        for k in ["today_entries", "today_exits", "cars_inside", "total_vehicles", "visitors_today", "monthly_visitors"]:
            assert k in data
            assert "value" in data[k]
        # total_vehicles should reflect masters (seeded ~22-23)
        assert data["total_vehicles"]["value"] >= 15
        assert data["monthly_visitors"]["value"] > 0

    def test_unauth(self):
        r = requests.get(f"{API}/dashboard/stats")
        assert r.status_code == 401


# ---------------- Analytics ----------------
class TestAnalytics:
    def test_overview(self, client):
        r = client.get(f"{API}/analytics/overview")
        assert r.status_code == 200
        data = r.json()
        assert len(data["daily"]) == 7
        assert len(data["weekly"]) == 28
        assert len(data["monthly"]) == 12
        assert isinstance(data["pie"], list) and len(data["pie"]) == 2
        # new stay_time block
        assert "stay_time" in data
        st = data["stay_time"]
        for k in ["avg_seconds", "longest_seconds", "shortest_seconds", "completed_sessions"]:
            assert k in st
        assert st["completed_sessions"] > 0
        assert st["longest_seconds"] >= st["avg_seconds"] >= st["shortest_seconds"]

    def test_unauth(self):
        r = requests.get(f"{API}/analytics/overview")
        assert r.status_code == 401


# ---------------- Reports export ----------------
class TestReportsExport:
    @pytest.mark.parametrize("fmt,ct,magic", [
        ("csv", "text/csv", None),
        ("xlsx", "spreadsheetml", b"PK"),
        ("pdf", "application/pdf", b"%PDF"),
    ])
    def test_export(self, client, fmt, ct, magic):
        r = client.get(f"{API}/reports/export", params={"period": "daily", "format": fmt})
        assert r.status_code == 200
        assert ct in r.headers.get("content-type", "")
        assert "attachment" in r.headers.get("content-disposition", "").lower()
        if magic:
            assert r.content[:len(magic)] == magic


# ---------------- Visits export ----------------
class TestVisitsExport:
    @pytest.mark.parametrize("fmt,ct,magic", [
        ("csv", "text/csv", None),
        ("xlsx", "spreadsheetml", b"PK"),
        ("pdf", "application/pdf", b"%PDF"),
    ])
    def test_per_vehicle(self, client, fmt, ct, magic):
        r = client.get(f"{API}/visits/export", params={
            "vehicle_number": SHOWCASE_PLATE, "range": "today", "format": fmt
        })
        assert r.status_code == 200, r.text
        assert ct in r.headers.get("content-type", "")
        cd = r.headers.get("content-disposition", "").lower()
        assert "attachment" in cd
        assert SHOWCASE_PLATE.lower() in cd or SHOWCASE_PLATE in r.headers.get("content-disposition", "")
        if magic:
            assert r.content[:len(magic)] == magic

    def test_all_vehicles_export_csv(self, client):
        r = client.get(f"{API}/visits/export", params={"range": "7d", "format": "csv"})
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")

    def test_invalid_format(self, client):
        r = client.get(f"{API}/visits/export", params={"format": "docx"})
        assert r.status_code == 400

    def test_unauth(self):
        r = requests.get(f"{API}/visits/export")
        assert r.status_code == 401


# ---------------- Users ----------------
class TestUsers:
    def test_list_users(self, client):
        r = client.get(f"{API}/users")
        assert r.status_code == 200
        users = r.json()
        assert ADMIN_EMAIL in [u["email"] for u in users]
        assert all("_id" not in u for u in users)


# ---------------- Settings ----------------
class TestSettings:
    def test_get_settings(self, client):
        r = client.get(f"{API}/settings")
        assert r.status_code == 200
        for k in ["company_name", "auto_delete_days"]:
            assert k in r.json()
