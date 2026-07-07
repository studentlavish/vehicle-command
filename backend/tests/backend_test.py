"""RDX Car Showroom Backend API tests.

Covers: auth (cookie + bearer), dashboard stats, vehicles CRUD & filters,
analytics, reports export (csv/xlsx/pdf), users, settings and unauthorized checks.
"""
import os
import io
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or "https://vehicle-command-17.preview.emergentagent.com"
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@rdx.com"
ADMIN_PASSWORD = "admin123"


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
    # cookies should be set
    assert "access_token" in s.cookies, "access_token cookie not set"
    assert "refresh_token" in s.cookies, "refresh_token cookie not set"
    # attach bearer token also for redundancy
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
        assert isinstance(data["token"], str) and len(data["token"]) > 20
        # check cookies (Set-Cookie header)
        set_cookie = r.headers.get("set-cookie", "")
        assert "access_token" in set_cookie
        assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()

    def test_login_wrong_password(self, anon):
        r = anon.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrongpw"})
        assert r.status_code == 401

    def test_me_with_cookies(self, client):
        r = client.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_me_without_auth(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401


# ---------------- Dashboard ----------------
class TestDashboard:
    def test_dashboard_stats_shape(self, client):
        r = client.get(f"{API}/dashboard/stats")
        assert r.status_code == 200
        data = r.json()
        required = ["today_entries", "today_exits", "cars_inside", "total_vehicles", "visitors_today", "monthly_visitors"]
        for k in required:
            assert k in data, f"missing key {k}"
            assert "value" in data[k]
            assert "change" in data[k]
            assert "trend" in data[k] and isinstance(data[k]["trend"], list)

    def test_dashboard_stats_unauth(self):
        r = requests.get(f"{API}/dashboard/stats")
        assert r.status_code == 401


# ---------------- Vehicles ----------------
class TestVehicles:
    def test_list_seeded(self, client):
        r = client.get(f"{API}/vehicles")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 42, f"expected >=42 seeded vehicles, got {len(data)}"
        # ensure no mongo _id leak
        assert "_id" not in data[0]
        assert "id" in data[0]

    def test_filter_status(self, client):
        r = client.get(f"{API}/vehicles", params={"status": "inside"})
        assert r.status_code == 200
        for v in r.json():
            assert v["status"] == "inside"

    def test_search_query(self, client):
        # get a vehicle number, then search for it
        first = client.get(f"{API}/vehicles").json()[0]
        vn = first["vehicle_number"]
        r = client.get(f"{API}/vehicles", params={"q": vn})
        assert r.status_code == 200
        assert any(v["vehicle_number"] == vn for v in r.json())

    def test_crud_flow(self, client):
        payload = {
            "vehicle_number": f"TEST{uuid.uuid4().hex[:6].upper()}",
            "owner_name": "TEST_Owner",
            "contact_number": "+91 9999999999",
            "vehicle_model": "Hyundai Creta",
            "status": "inside",
            "notes": "created by pytest",
        }
        # CREATE
        r = client.post(f"{API}/vehicles", json=payload)
        assert r.status_code == 200, r.text
        v = r.json()
        vid = v["id"]
        assert v["vehicle_number"] == payload["vehicle_number"]
        assert v["owner_name"] == "TEST_Owner"
        assert v["entry_time"], "entry_time should be auto-set"

        # GET
        r = client.get(f"{API}/vehicles/{vid}")
        assert r.status_code == 200
        assert r.json()["owner_name"] == "TEST_Owner"

        # PATCH -> exited (should set exit_time)
        r = client.patch(f"{API}/vehicles/{vid}", json={"status": "exited"})
        assert r.status_code == 200
        updated = r.json()
        assert updated["status"] == "exited"
        assert updated["exit_time"], "exit_time should be auto-set on exit"

        # DELETE
        r = client.delete(f"{API}/vehicles/{vid}")
        assert r.status_code == 200
        r = client.get(f"{API}/vehicles/{vid}")
        assert r.status_code == 404

    def test_vehicles_unauth(self):
        r = requests.get(f"{API}/vehicles")
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

    def test_analytics_unauth(self):
        r = requests.get(f"{API}/analytics/overview")
        assert r.status_code == 401


# ---------------- Reports ----------------
class TestReports:
    @pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
    def test_export_csv(self, client, period):
        r = client.get(f"{API}/reports/export", params={"period": period, "format": "csv"})
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        assert "attachment" in r.headers.get("content-disposition", "").lower()
        assert b"Vehicle Number" in r.content

    @pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
    def test_export_xlsx(self, client, period):
        r = client.get(f"{API}/reports/export", params={"period": period, "format": "xlsx"})
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "spreadsheetml" in ct or "openxml" in ct
        assert r.content[:2] == b"PK"  # xlsx is zip

    @pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
    def test_export_pdf(self, client, period):
        r = client.get(f"{API}/reports/export", params={"period": period, "format": "pdf"})
        assert r.status_code == 200
        assert "application/pdf" in r.headers.get("content-type", "")
        assert r.content[:4] == b"%PDF"

    def test_reports_unauth(self):
        r = requests.get(f"{API}/reports/export", params={"period": "daily", "format": "csv"})
        assert r.status_code == 401


# ---------------- Users ----------------
class TestUsers:
    def test_list_users(self, client):
        r = client.get(f"{API}/users")
        assert r.status_code == 200
        users = r.json()
        emails = [u["email"] for u in users]
        assert ADMIN_EMAIL in emails
        assert len(users) >= 4, f"expected admin + 3 demo users, got {len(users)}"
        # no _id leak
        assert all("_id" not in u for u in users)

    def test_create_and_delete_user(self, client):
        email = f"test_{uuid.uuid4().hex[:6]}@rdx.com"
        r = client.post(f"{API}/users", json={"name": "TEST User", "email": email, "role": "manager", "status": "active"})
        assert r.status_code == 200, r.text
        uid = r.json()["id"]
        assert r.json()["email"] == email.lower()

        # delete
        r = client.delete(f"{API}/users/{uid}")
        assert r.status_code == 200

    def test_cannot_delete_self(self, client):
        me = client.get(f"{API}/auth/me").json()
        r = client.delete(f"{API}/users/{me['id']}")
        assert r.status_code == 400

    def test_users_unauth(self):
        r = requests.get(f"{API}/users")
        assert r.status_code == 401


# ---------------- Settings ----------------
class TestSettings:
    def test_get_settings_defaults(self, client):
        r = client.get(f"{API}/settings")
        assert r.status_code == 200
        s = r.json()
        for k in ["company_name", "camera_url", "auto_delete_days", "whatsapp_report_time", "backup_enabled"]:
            assert k in s

    def test_update_settings(self, client):
        new_name = f"RDX Test {uuid.uuid4().hex[:5]}"
        r = client.put(f"{API}/settings", json={"company_name": new_name, "auto_delete_days": 90})
        assert r.status_code == 200
        assert r.json()["company_name"] == new_name

        # persistence via GET
        r = client.get(f"{API}/settings")
        assert r.json()["company_name"] == new_name
        assert r.json()["auto_delete_days"] == 90

    def test_settings_unauth(self):
        r = requests.get(f"{API}/settings")
        assert r.status_code == 401
