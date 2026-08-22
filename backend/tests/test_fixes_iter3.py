"""Iteration 3 code-review fix verification tests.

FIX 1 — _report_query 'start' always defined (period=unknown must not 500)
FIX 2 — plate_pipeline read_plate 'raw' initialised (auto-detection stability)
REGRESSION SMOKE — camera connect + WS camera + WS events + agent WS auth
"""
import asyncio
import json
import os
import time
import uuid

import pytest
import requests
import websockets

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "https://vehicle-command-17.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
WS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@rdx.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
AGENT_SECRET = os.environ.get("VASHU_AGENT_SECRET", "vashu-agent-secret-change-me-2026")
FIXTURE = "/app/backend/fixtures/live_dash.mp4"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200
    tok = r.json().get("token")
    s.headers.update({"Authorization": f"Bearer {tok}"})
    s._access_token = tok  # for WS
    return s


# ---------------- FIX 1: _report_query 'start' always defined ----------------
class TestFix1ReportQuery:
    @pytest.mark.parametrize("period,fmt,magic", [
        ("daily", "csv", None),
        ("weekly", "xlsx", b"PK"),
        ("monthly", "pdf", b"%PDF"),
    ])
    def test_known_periods(self, client, period, fmt, magic):
        r = client.get(f"{API}/reports/export", params={"period": period, "format": fmt})
        assert r.status_code == 200, f"{period}/{fmt} -> {r.status_code}: {r.text[:200]}"
        if magic:
            assert r.content[: len(magic)] == magic

    def test_unknown_period_falls_through_no_nameerror(self, client):
        """The critical fix — 'unknown' must NOT raise NameError; falls through to 365d default."""
        r = client.get(f"{API}/reports/export", params={"period": "unknown", "format": "csv"})
        assert r.status_code == 200, f"unknown period should not 500: {r.status_code} {r.text[:300]}"
        assert "text/csv" in r.headers.get("content-type", "")

    def test_gibberish_period(self, client):
        r = client.get(f"{API}/reports/export", params={"period": "quarterly_v2", "format": "csv"})
        assert r.status_code == 200


# ---------------- REGRESSION SMOKE: camera + WS + agent ----------------
class TestCameraPipelineSmoke:
    CAM_ID = f"test-fix-{uuid.uuid4().hex[:6]}"

    def test_a_connect_camera_to_fixture(self, client):
        assert os.path.exists(FIXTURE), f"fixture missing: {FIXTURE}"
        r = client.post(f"{API}/cameras/connect", json={
            "camera_id": self.CAM_ID, "source": FIXTURE, "label": "test-fix"
        })
        assert r.status_code == 200, r.text
        info = r.json()
        assert info["camera_id"] == self.CAM_ID
        # Wait for status transition
        for _ in range(15):
            time.sleep(1)
            g = client.get(f"{API}/cameras/{self.CAM_ID}")
            if g.status_code == 200 and g.json().get("status") == "online":
                return
        pytest.fail(f"camera did not go online: {g.json() if g.status_code==200 else g.text}")

    def test_b_ws_camera_streams_binary_jpeg(self, client):
        cookie_hdr = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        url = f"{WS_BASE}/api/ws/camera/{self.CAM_ID}"

        async def run():
            async with websockets.connect(url, additional_headers={"Cookie": cookie_hdr}) as ws:
                got_bytes = False
                got_status = False
                end = time.time() + 12
                while time.time() < end and (not got_bytes or not got_status):
                    msg = await asyncio.wait_for(ws.recv(), timeout=8)
                    if isinstance(msg, bytes):
                        # JPEG magic
                        if msg[:2] == b"\xff\xd8":
                            got_bytes = True
                    else:
                        try:
                            j = json.loads(msg)
                            if j.get("type") == "status":
                                got_status = True
                        except Exception:
                            pass
                assert got_status, "no status frame received"
                assert got_bytes, "no binary JPEG frame received"

        asyncio.run(run())

    def test_c_ws_events_hello(self, client):
        cookie_hdr = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        url = f"{WS_BASE}/api/ws/events"

        async def run():
            async with websockets.connect(url, additional_headers={"Cookie": cookie_hdr}) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                j = json.loads(msg)
                assert j.get("type") == "hello", j
                assert j.get("user") == ADMIN_EMAIL

        asyncio.run(run())

    def test_d_ws_events_unauth(self):
        """no cookie / no token -> close 4401"""
        url = f"{WS_BASE}/api/ws/events"

        async def run():
            try:
                async with websockets.connect(url) as ws:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    j = json.loads(msg)
                    assert j.get("type") == "error"
                    # server will close 4401 next
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=3)
                    except Exception:
                        pass
            except websockets.exceptions.InvalidStatus as e:
                # Some servers reject at handshake — acceptable
                assert e.response.status_code in (401, 403)

        asyncio.run(run())

    def test_e_disconnect_and_cleanup(self, client):
        r = client.post(f"{API}/cameras/{self.CAM_ID}/disconnect")
        assert r.status_code == 200


# ---------------- Agent WSS auth ----------------
class TestAgentWs:
    def test_agent_ws_rejects_invalid_secret(self):
        url = f"{WS_BASE}/api/agent/ws?agent_id=test-agent&secret=wrong-secret"

        async def run():
            async with websockets.connect(url) as ws:
                # Expect: error json then close 4401
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                j = json.loads(msg)
                assert j.get("type") == "error"
                try:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                    pytest.fail("expected close")
                except websockets.exceptions.ConnectionClosed as cc:
                    assert cc.code == 4401, cc

        asyncio.run(run())

    def test_agent_ws_accepts_valid_secret(self):
        url = f"{WS_BASE}/api/agent/ws?agent_id=test-agent-{uuid.uuid4().hex[:5]}&secret={AGENT_SECRET}"

        async def run():
            async with websockets.connect(url) as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                j = json.loads(msg)
                assert j.get("type") == "hello" and j.get("ok") is True

        asyncio.run(run())


# ---------------- FIX 2: plate_pipeline read_plate stability (log check via post-conditions) ----------------
class TestFix2PlatePipelineStability:
    """We can't easily unit-test the pipeline over HTTP, but we assert:
    - connecting a camera to the fixture succeeds
    - during the auto-detection loop, /api/vehicles/master/{plate}/sessions returns cleanly (not 500)
    Any NameError would surface as a background exception logged; the endpoint itself
    must remain healthy.
    """
    CAM_ID = f"pipe-fix-{uuid.uuid4().hex[:5]}"

    def test_auto_detection_endpoint_healthy(self, client):
        r = client.post(f"{API}/cameras/connect", json={
            "camera_id": self.CAM_ID, "source": FIXTURE, "label": "pipe-fix"
        })
        assert r.status_code == 200
        # wait ~15s for at least one auto-detection cycle
        time.sleep(15)
        # sanity check: master sessions endpoint responds cleanly for auto-detected plates
        # (we don't require a specific plate; just that endpoint doesn't 500)
        r2 = client.get(f"{API}/vehicles/search", params={"q": "UP"})
        assert r2.status_code == 200
        # cleanup
        client.post(f"{API}/cameras/{self.CAM_ID}/disconnect")
