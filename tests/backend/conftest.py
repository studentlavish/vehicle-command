"""Backend test harness.

Every run gets its own throw-away MongoDB database (vc_test_<random>) and a
temporary snapshots folder. Both are removed when the run ends, so tests never
touch real data and leave nothing behind.

Requires a MongoDB reachable at TEST_MONGO_URL (default mongodb://localhost:27017).
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

MONGO_URL = os.environ.get("TEST_MONGO_URL", "mongodb://localhost:27017")
DB_NAME = f"vc_test_{secrets.token_hex(4)}"
ADMIN_EMAIL = "owner@example.com"
ADMIN_PASSWORD = secrets.token_urlsafe(16)
AGENT_SECRET = secrets.token_urlsafe(24)
CRON_SECRET = secrets.token_urlsafe(24)
_SNAP_DIR = tempfile.mkdtemp(prefix="vc_test_snapshots_")

# Force (not setdefault) so values from backend/.env are never used by tests.
_TEST_ENV = {
    "MONGO_URL": MONGO_URL,
    "DB_NAME": DB_NAME,
    "JWT_SECRET": secrets.token_hex(32),
    "ADMIN_EMAIL": ADMIN_EMAIL,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "CORS_ORIGINS": "http://testserver",
    "VASHU_AGENT_SECRET": AGENT_SECRET,
    "WEBHOOK_CRON_SECRET": CRON_SECRET,
    "SNAPSHOTS_DIR": _SNAP_DIR,
    "RETENTION_ENABLED": "false",
    "SEED_DEMO": "false",
    "COOKIE_SECURE": "false",
    "EMERGENT_LLM_KEY": "",
    "EMERGENT_EMAIL_KEY": "",
    "GOOGLE_ADMIN_EMAIL": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_WHATSAPP_FROM": "",
    "TWILIO_WHATSAPP_TO": "",
    "META_ACCESS_TOKEN": "",
    "META_PHONE_NUMBER_ID": "",
    "META_APP_SECRET": "",
    "META_VERIFY_TOKEN": "",
}
os.environ.update(_TEST_ENV)
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _drop_test_db() -> None:
    import pymongo
    c = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        c.drop_database(DB_NAME)
    finally:
        c.close()


def pytest_unconfigure(config):
    _drop_test_db()
    shutil.rmtree(_SNAP_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def snapshots_dir() -> Path:
    return Path(_SNAP_DIR)


@pytest.fixture(scope="session")
def client():
    """One app instance for the whole run (Motor binds to a single event loop)."""
    from fastapi.testclient import TestClient
    import server

    with TestClient(server.app) as c:
        yield c


@pytest.fixture(scope="session")
def db():
    """Synchronous handle on the test database, for assertions."""
    import pymongo
    c = pymongo.MongoClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.fixture(autouse=True)
def _isolate(request):
    """Wipe everything except the seeded admin after each test."""
    yield
    if "db" not in request.fixturenames and "client" not in request.fixturenames:
        return
    import pymongo
    c = pymongo.MongoClient(MONGO_URL)
    d = c[DB_NAME]
    for name in d.list_collection_names():
        if name == "users":
            d.users.delete_many({"email": {"$ne": ADMIN_EMAIL}})
        else:
            d[name].delete_many({})
    c.close()
    for p in Path(_SNAP_DIR).iterdir():
        if p.is_file():
            p.unlink()


def login(client, email: str, password: str) -> dict:
    """Log in and return bearer-auth headers (cookies cleared so roles never mix)."""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin_headers(client) -> dict:
    return login(client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture
def agent_ws(client):
    """Factory: open an authenticated agent WebSocket and consume the hello."""
    from contextlib import contextmanager

    @contextmanager
    def _open(agent_id: str = "agent-under-test", secret: str = AGENT_SECRET):
        with client.websocket_connect(f"/api/agent/ws?agent_id={agent_id}",
                                      headers={"x-agent-secret": secret}) as ws:
            hello = ws.receive_json()
            assert hello.get("type") == "hello", hello
            yield ws

    return _open


# Minimal valid JPEG (1×1 px) for snapshot tests.
TINY_JPEG_HEX = (
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912"
    "130f141d1a1f1e1d1a1c1c20242e2720222c231c1c283728292c30313432271f393d3830393530312effc0000b080001"
    "000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc4001f0100030101"
    "010101010101010000000000000102030405060708090a0bffda0008010100003f00fbd0ffd9"
)
TINY_JPEG = bytes.fromhex(TINY_JPEG_HEX)
