"""Phase 1 — security hardening."""
import base64
import secrets
from datetime import datetime, timedelta, timezone

import pytest

import security
import server
from conftest import (ADMIN_EMAIL, ADMIN_PASSWORD, AGENT_SECRET, TINY_JPEG, login, make_user,
                      register_agent)


# ---------- Admin seeding ----------
def test_restart_does_not_reset_changed_admin_password(client, admin_headers):
    new_pw = secrets.token_urlsafe(16)
    r = client.post("/api/auth/change-password", headers=admin_headers,
                    json={"current_password": ADMIN_PASSWORD, "new_password": new_pw})
    assert r.status_code == 200
    try:
        client.portal.call(server.seed_admin)  # what every restart runs
        assert client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 401
        login(client, ADMIN_EMAIL, new_pw)
    finally:
        h = login(client, ADMIN_EMAIL, new_pw)
        client.post("/api/auth/change-password", headers=h,
                    json={"current_password": new_pw, "new_password": ADMIN_PASSWORD})


def test_fresh_install_refuses_weak_admin_password(client, db, monkeypatch):
    saved = list(db.users.find({"role": "admin"}))
    db.users.delete_many({"role": "admin"})
    try:
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "admin123")
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            client.portal.call(server.seed_admin)
        assert db.users.count_documents({"role": "admin"}) == 0
    finally:
        db.users.insert_many(saved)


@pytest.mark.parametrize("pw,ok", [("short", False), ("admin123", False),
                                   ("password123", False), ("a-long-unusual-pass", True)])
def test_password_policy(pw, ok):
    assert (security.password_problem(pw) is None) is ok


# ---------- Cookies & CORS ----------
def test_auth_cookies_are_httponly_and_samesite(client):
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    cookies = r.headers.get_list("set-cookie")
    client.cookies.clear()
    access = next(c for c in cookies if c.startswith("access_token="))
    assert "HttpOnly" in access and "SameSite=lax" in access
    assert "Secure" not in access  # tests run with COOKIE_SECURE=false


def test_secure_cookie_flag_follows_setting(client, monkeypatch):
    monkeypatch.setattr(security, "COOKIE_SECURE", True)
    r = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    client.cookies.clear()
    assert all("Secure" in c for c in r.headers.get_list("set-cookie") if c.split("=")[0] in ("access_token", "refresh_token"))


def test_cors_only_allows_configured_origin(client):
    ok = client.options("/api/auth/me", headers={"Origin": "http://testserver", "Access-Control-Request-Method": "GET"})
    bad = client.options("/api/auth/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://testserver"
    assert "access-control-allow-origin" not in bad.headers


@pytest.mark.parametrize("value", ["", "*", "https://a.example,*"])
def test_cors_wildcard_or_missing_is_refused(monkeypatch, value):
    monkeypatch.setenv("CORS_ORIGINS", value)
    with pytest.raises(RuntimeError):
        security.cors_origins()


# ---------- Login lockout ----------
def test_login_locks_after_repeated_failures(client, admin_headers):
    user, pw = make_user(client, admin_headers, "security")
    for _ in range(security.LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-password"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": user["email"], "password": pw})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


def test_successful_login_clears_failures(client, admin_headers, db):
    user, pw = make_user(client, admin_headers, "manager")
    client.post("/api/auth/login", json={"email": user["email"], "password": "wrong-password"})
    login(client, user["email"], pw)
    assert db.login_attempts.count_documents({"key": {"$regex": f"^{user['email']}"}}) == 0


# ---------- Users & passwords ----------
def test_created_user_gets_one_time_temporary_password(client, admin_headers):
    r = client.post("/api/users", headers=admin_headers,
                    json={"name": "Gate Guard", "email": "guard@example.com", "role": "security"})
    temp = r.json()["temporary_password"]
    login(client, "guard@example.com", temp)
    listed = client.get("/api/users", headers=admin_headers).json()
    assert all("temporary_password" not in u and "password_hash" not in u for u in listed)


def test_weak_chosen_password_rejected(client, admin_headers):
    r = client.post("/api/users", headers=admin_headers,
                    json={"name": "X", "email": "x@example.com", "role": "manager", "password": "admin123"})
    assert r.status_code == 400


def test_admin_password_reset(client, admin_headers):
    user, old = make_user(client, admin_headers, "manager")
    r = client.post(f"/api/users/{user['id']}/password", headers=admin_headers, json={})
    new = r.json()["temporary_password"]
    assert client.post("/api/auth/login", json={"email": user["email"], "password": old}).status_code == 401
    login(client, user["email"], new)


def test_change_password_requires_current(client, admin_headers):
    user, pw = make_user(client, admin_headers, "manager")
    h = login(client, user["email"], pw)
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": "nope-nope-nope", "new_password": "another-long-pass"}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": pw, "new_password": "short"}).status_code == 400
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": pw, "new_password": "another-long-pass"}).status_code == 200
    login(client, user["email"], "another-long-pass")


# ---------- Role checks ----------
ROUTES = [
    # (method, path, json, allowed roles)
    ("get", "/api/dashboard/stats", None, {"admin", "manager", "security"}),
    ("get", "/api/plate-reviews", None, {"admin", "manager", "security"}),
    ("get", "/api/users", None, {"admin", "manager"}),
    ("post", "/api/users", {"name": "N", "email": "n-{r}@example.com", "role": "security"}, {"admin"}),
    ("get", "/api/settings", None, {"admin"}),
    ("put", "/api/settings", {"company_name": "Showroom"}, {"admin"}),
    ("post", "/api/maintenance/purge-expired", None, {"admin"}),
    ("post", "/api/agent/register", {"agent_id": "pc-{r}"}, {"admin"}),
    ("get", "/api/audit-log", None, {"admin"}),
]


@pytest.mark.parametrize("role", ["admin", "manager", "security"])
def test_role_permissions(client, admin_headers, role):
    h = admin_headers if role == "admin" else login(client, *_creds(client, admin_headers, role))
    for method, path, body, allowed in ROUTES:
        if body:
            body = {k: (v.format(r=secrets.token_hex(3)) if isinstance(v, str) else v) for k, v in body.items()}
        r = getattr(client, method)(path, headers=h, **({"json": body} if body is not None else {}))
        if role in allowed:
            assert r.status_code < 400, (role, path, r.status_code, r.text)
        else:
            assert r.status_code == 403, (role, path, r.status_code)


def _creds(client, admin_headers, role):
    user, pw = make_user(client, admin_headers, role)
    return user["email"], pw


# ---------- Agents ----------
def _connect(client, agent_id, secret, via_query=False):
    if via_query:
        return client.websocket_connect(f"/api/agent/ws?agent_id={agent_id}&secret={secret}")
    return client.websocket_connect("/api/agent/ws", headers={"x-agent-id": agent_id, "x-agent-secret": secret})


def test_unknown_agent_is_refused_even_with_shared_secret(client):
    with _connect(client, "not-registered", AGENT_SECRET) as ws:
        assert ws.receive_json() == {"type": "error", "message": "unauthorized"}


def test_agent_with_own_secret(client, db):
    good = register_agent(db, "pc-own", secrets.token_urlsafe(24))
    with _connect(client, "pc-own", good) as ws:
        assert ws.receive_json()["type"] == "hello"
    with _connect(client, "pc-own", "wrong-secret") as ws:
        assert ws.receive_json()["message"] == "unauthorized"
    with _connect(client, "pc-own", AGENT_SECRET) as ws:  # shared secret no longer valid once it has its own
        assert ws.receive_json()["message"] == "unauthorized"


def test_legacy_agent_may_use_shared_secret_via_query(client, db):
    register_agent(db, "pc-legacy")
    with _connect(client, "pc-legacy", AGENT_SECRET, via_query=True) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_register_and_rotate_agent_secret(client, admin_headers):
    r = client.post("/api/agent/register", headers=admin_headers, json={"agent_id": "showroom-pc-01"})
    first = r.json()["secret"]
    assert client.post("/api/agent/register", headers=admin_headers, json={"agent_id": "showroom-pc-01"}).status_code == 409
    with _connect(client, "showroom-pc-01", first) as ws:
        assert ws.receive_json()["type"] == "hello"
    second = client.post("/api/agent/showroom-pc-01/rotate-secret", headers=admin_headers).json()["secret"]
    with _connect(client, "showroom-pc-01", first) as ws:
        assert ws.receive_json()["message"] == "unauthorized"
    with _connect(client, "showroom-pc-01", second) as ws:
        assert ws.receive_json()["type"] == "hello"
    status = client.get("/api/agent/status", headers=admin_headers).json()
    assert status[0]["has_own_secret"] is True and "secret_hash" not in status[0]


def test_agent_cannot_claim_another_id(agent_ws):
    with agent_ws("pc-a") as ws:
        ws.send_json({"type": "agent_register", "agent_id": "pc-b"})
        assert "does not match" in ws.receive_json()["message"]


def test_oversized_detection_image_is_dropped(client, db, agent_ws, monkeypatch):
    monkeypatch.setattr(server, "MAX_AGENT_JPEG_BYTES", 10)
    with agent_ws() as ws:
        ws.send_json({"type": "plate_detected", "camera_id": "CAM-01", "plate": "KA05MG9999", "confidence": "high",
                      "crop_jpeg_b64": base64.b64encode(TINY_JPEG).decode()})
        assert ws.receive_json()["event"] == "entry"
    assert db.visit_sessions.find_one({"vehicle_number": "KA05MG9999"})["entry_image"] == ""


# ---------- Audit + retention ----------
def test_audit_log_records_actions(client, admin_headers, db):
    client.put("/api/settings", headers=admin_headers, json={"company_name": "Audit Motors"})
    make_user(client, admin_headers, "security")
    actions = {d["action"] for d in client.get("/api/audit-log", headers=admin_headers).json()}
    assert {"settings.update", "user.create"} <= actions
    row = db.audit_log.find_one({"action": "settings.update"})
    assert row["user"] == ADMIN_EMAIL and row["details"]["fields"] == ["company_name"]


def test_purge_keeps_active_and_removes_expired_files(client, admin_headers, db, snapshots_dir):
    old = (datetime.now(timezone.utc) - timedelta(days=server.RETENTION_DAYS + 5)).isoformat()
    (snapshots_dir / "old_entry.jpg").write_bytes(TINY_JPEG)
    db.visit_sessions.insert_many([
        {"id": "done", "vehicle_number": "KA05MG9999", "entry_time": old, "status": "completed",
         "entry_image": "/api/snapshots/old_entry.jpg"},
        {"id": "inside", "vehicle_number": "MH12AB1234", "entry_time": old, "status": "active"},
    ])
    r = client.post("/api/maintenance/purge-expired", headers=admin_headers).json()
    assert r["deleted"] == 1 and r["files_removed"] == 1
    assert not (snapshots_dir / "old_entry.jpg").exists()
    assert [d["id"] for d in db.visit_sessions.find()] == ["inside"]
    assert db.audit_log.count_documents({"action": "maintenance.purge_expired"}) == 1


def test_bootstrap_agent_for_local_development(client, db, monkeypatch):
    monkeypatch.setattr(server, "AGENT_BOOTSTRAP_ID", "pc-dev")
    client.portal.call(server.seed_bootstrap_agent)
    client.portal.call(server.seed_bootstrap_agent)  # idempotent
    assert db.agents.count_documents({"agent_id": "pc-dev"}) == 1
    with _connect(client, "pc-dev", AGENT_SECRET) as ws:
        assert ws.receive_json()["type"] == "hello"
