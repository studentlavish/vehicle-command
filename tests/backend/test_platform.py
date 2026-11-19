"""Phase 5 — storage, email, Google sign-in, scheduler, backups, configuration."""
import asyncio
import os
import secrets
import shutil
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import boto3
import jwt
import pytest
from moto import mock_aws

import backup_utils
import config_check
import email_utils
import google_oauth
import plate_pipeline
import scheduler
import server
import storage
from conftest import ADMIN_EMAIL, DB_NAME, MONGO_URL, TINY_JPEG


# ---------- storage ----------
def test_local_storage(tmp_path):
    st = storage.LocalStorage(str(tmp_path / "store"), str(tmp_path / "snaps"))

    async def go():
        await st.put("backups/mongodb/a.gz", b"A")
        await st.put("snapshots/x.jpg", b"X")
        assert await st.get("backups/mongodb/a.gz") == b"A"
        assert (tmp_path / "snaps" / "x.jpg").read_bytes() == b"X"
        assert await st.list("backups/mongodb") == ["backups/mongodb/a.gz"]
        assert await st.delete("backups/mongodb/a.gz") is True
        assert await st.get("backups/mongodb/a.gz") is None
        with pytest.raises(ValueError):
            await st.put("../escape", b"")
    asyncio.run(go())


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test")
        client.create_bucket(Bucket="vc-test", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        st = storage.S3Storage(client, "vc-test", prefix="vehicle-command")
        previous = storage.get()
        storage.use(st)
        try:
            yield st
        finally:
            storage.use(previous)


def test_s3_storage_roundtrip(s3):
    async def go():
        await s3.put("backups/mongodb/a.gz", b"A", "application/gzip")
        assert await s3.get("backups/mongodb/a.gz") == b"A"
        assert await s3.get("backups/mongodb/missing.gz") is None
        assert await s3.list("backups/mongodb") == ["backups/mongodb/a.gz"]
        await s3.delete("backups/mongodb/a.gz")
        assert await s3.list("backups/mongodb") == []
    asyncio.run(go())
    assert s3.client.list_objects_v2(Bucket="vc-test").get("KeyCount", 0) == 0


def test_snapshots_live_in_bucket_and_retention_deletes_them(client, db, s3, admin_headers, snapshots_dir):
    r = client.portal.call(lambda: plate_pipeline.route_detection(server.db, camera_id="CAM-01", raw_plate="KA05MG9999",
                                                                  confidence="high", jpeg_frame=TINY_JPEG))
    url = r["session"]["entry_image"]
    name = os.path.basename(url)
    assert not (snapshots_dir / name).exists()  # not on local disk
    keys = [o["Key"] for o in s3.client.list_objects_v2(Bucket="vc-test")["Contents"]]
    assert keys == [f"vehicle-command/snapshots/{name}"]
    assert client.get(url, headers=admin_headers).content == TINY_JPEG

    old = (datetime.now(timezone.utc) - timedelta(days=server.RETENTION_DAYS + 1)).isoformat()
    db.visit_sessions.update_one({"id": r["session"]["id"]}, {"$set": {"entry_time": old, "status": "completed"}})
    assert client.portal.call(server.purge_expired_sessions)["files_removed"] == 1
    assert s3.client.list_objects_v2(Bucket="vc-test").get("KeyCount", 0) == 0


def test_bucket_outage_falls_back_to_local_disk(client, s3, snapshots_dir, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("bucket unreachable")
    monkeypatch.setattr(s3, "put", down)
    url = client.portal.call(lambda: plate_pipeline._write_snapshot("entry", "KA05MG9999", TINY_JPEG))
    assert (snapshots_dir / os.path.basename(url)).read_bytes() == TINY_JPEG


# ---------- email ----------
def test_email_provider_selection(monkeypatch):
    monkeypatch.setattr(email_utils, "EMAIL_KEY", "")
    monkeypatch.setenv("EMAIL_PROVIDER", "")
    assert email_utils.provider() == ""
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(email_utils.send_email(to="a@example.com", subject="s", html="<p>x</p>"))
    monkeypatch.setenv("EMAIL_PROVIDER", "SMTP")
    assert email_utils.provider() == "smtp"


def test_resend_payload(monkeypatch):
    sent = {}

    async def fake_post(url, headers, payload):
        sent.update(url=url, headers=headers, payload=payload)
        return {"id": "re_123"}
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_key")
    monkeypatch.setattr(email_utils, "EMAIL_FROM", "reports@example.com")
    monkeypatch.setattr(email_utils, "_post_json", fake_post)
    mid = asyncio.run(email_utils.send_email(to="owner@example.com", subject="Digest", html="<p>Hi</p>"))
    assert mid == "re_123" and sent["url"] == email_utils.RESEND_URL
    assert sent["headers"] == {"Authorization": "Bearer re_key"}
    assert sent["payload"]["to"] == ["owner@example.com"] and "reports@example.com" in sent["payload"]["from"]


def test_smtp_send(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, pw):
            calls.append(("login", user))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"], msg.get_body(("html",)).get_content().strip()))

    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "mailer")
    monkeypatch.setattr(email_utils, "EMAIL_FROM", "reports@example.com")
    monkeypatch.setattr(email_utils.smtplib, "SMTP", FakeSMTP)
    asyncio.run(email_utils.send_email(to="owner@example.com", subject="Digest", html="<p>Hi</p>"))
    assert calls == [("connect", "smtp.example.com", 587), ("starttls",), ("login", "mailer"),
                     ("send", "owner@example.com", "Digest", "<p>Hi</p>")]


def test_email_safety_gate_still_applies(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    with pytest.raises(ValueError, match="https"):
        asyncio.run(email_utils.send_email(to="a@example.com", subject="s", html='<a href="http://x.example">x</a>'))


# ---------- Google sign-in ----------
@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_URL", "https://gate.example.com")


def _start(client):
    r = client.get("/api/auth/google/start", follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    return q


def test_google_disabled_by_default(client):
    assert client.get("/api/auth/config").json() == {"google": False}
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 404


def test_google_start_uses_pkce_and_state(client, google):
    assert client.get("/api/auth/config").json() == {"google": True}
    q = _start(client)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"]
    assert q["redirect_uri"] == ["https://gate.example.com/api/auth/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0] and q["state"][0]
    client.cookies.clear()


def test_google_callback_signs_in_listed_user(client, google, monkeypatch):
    async def fake_identity(code, flow):
        assert code == "auth-code" and flow["verifier"]
        return {"email": ADMIN_EMAIL.upper(), "email_verified": True, "name": "Owner"}
    monkeypatch.setattr(google_oauth, "identity_from_code", fake_identity)
    state = _start(client)["state"][0]
    r = client.get(f"/api/auth/google/callback?code=auth-code&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://gate.example.com/"
    assert client.get("/api/auth/me").json()["email"] == ADMIN_EMAIL  # cookie session works
    client.cookies.clear()


@pytest.mark.parametrize("case,expected", [("bad_state", "google_state"), ("unknown", "google_not_allowed"),
                                           ("cancelled", "google_cancelled"), ("no_cookie", "google_expired")])
def test_google_callback_refusals(client, google, monkeypatch, case, expected):
    async def fake_identity(code, flow):
        return {"email": "stranger@example.com", "email_verified": True}
    monkeypatch.setattr(google_oauth, "identity_from_code", fake_identity)
    state = _start(client)["state"][0]
    if case == "no_cookie":
        client.cookies.clear()
    url = {"bad_state": "/api/auth/google/callback?code=c&state=forged",
           "unknown": f"/api/auth/google/callback?code=c&state={state}",
           "cancelled": "/api/auth/google/callback?error=access_denied",
           "no_cookie": f"/api/auth/google/callback?code=c&state={state}"}[case]
    r = client.get(url, follow_redirects=False)
    assert r.headers["location"] == f"https://gate.example.com/login?error={expected}"
    assert "access_token" not in r.cookies
    client.cookies.clear()


def test_id_token_verification(google, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setattr(google_oauth, "_jwks", FakeJWKS())

    def token(**over):
        claims = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "nonce": "n1",
                  "email": "a@example.com", "email_verified": True, "exp": int(time.time()) + 300, **over}
        return jwt.encode(claims, key, algorithm="RS256")

    assert google_oauth.verify_id_token(token(), "n1")["email"] == "a@example.com"
    for bad in (dict(nonce="other"), dict(aud="someone-else"), dict(iss="https://evil.example"),
                dict(email_verified=False), dict(exp=int(time.time()) - 10)):
        with pytest.raises(Exception):
            google_oauth.verify_id_token(token(**bad), "n1")


# ---------- scheduler ----------
def test_scheduler_runs_each_job_once_per_day(client, db):
    ran = []

    async def job(run_id):
        ran.append(run_id)

    async def boom(run_id):
        raise RuntimeError("smtp down")
    jobs = [scheduler.Job("digest", "09:00", job), scheduler.Job("backup", "03:00", boom),
            scheduler.Job("off", "00:00", job, enabled=False)]
    ist = scheduler.APP_TZ

    def at(day, hh, mm):
        return datetime(2026, 11, day, hh, mm, tzinfo=ist).astimezone(timezone.utc)

    call = lambda when: client.portal.call(lambda: scheduler.run_due(server.db, jobs, now=when))  # noqa: E731
    client.portal.call(lambda: scheduler.ensure_indexes(server.db))
    assert call(at(2, 2, 0)) == []                       # before either time
    assert call(at(2, 3, 5)) == ["backup"]               # backup due (and fails)
    assert db.job_runs.find_one({"job": "backup"})["status"] == "failed"
    assert call(at(2, 9, 1)) == ["digest"] and ran == ["digest-2026-11-02"]
    assert call(at(2, 9, 30)) == []                      # not twice the same day
    assert call(at(3, 20, 0)) == []                      # down past the catch-up window → skipped
    assert {d["status"] for d in db.job_runs.find({"date": "2026-11-03"})} == {"skipped"}
    assert call(at(4, 5, 0)) == ["backup"]               # 2 h late, within the window → caught up
    assert call(at(4, 9, 10)) == ["digest"]

    async def twice():
        return await asyncio.gather(scheduler.run_due(server.db, jobs, now=at(5, 4, 0)),
                                    scheduler.run_due(server.db, jobs, now=at(5, 4, 0)))
    first, second = client.portal.call(twice)
    assert sorted(first + second) == ["backup"]          # concurrent schedulers: each job once


# ---------- backups ----------
@pytest.mark.skipif(not shutil.which("mongodump") or not shutil.which("mongorestore"),
                    reason="MongoDB Database Tools not installed")
def test_backup_restore_roundtrip_and_retention(client, db, monkeypatch):
    import pymongo
    db.vehicle_masters.insert_many([{"vehicle_number": f"KA05MG{1000 + i}"} for i in range(5)])
    target = f"vc_test_restore_{secrets.token_hex(3)}"
    monkeypatch.setattr(backup_utils, "BACKUP_KEEP", 1)
    try:
        first = client.portal.call(lambda: backup_utils.run_backup(server.db, MONGO_URL, DB_NAME, "r1"))
        assert first["size_bytes"] > 0 and first["storage_path"].startswith("backups/mongodb/")
        time.sleep(1.1)  # distinct archive names
        second = client.portal.call(lambda: backup_utils.run_backup(server.db, MONGO_URL, DB_NAME, "r2"))
        assert client.portal.call(lambda: storage.get().get(first["storage_path"])) is None  # really deleted
        assert db.backups.find_one({"id": first["id"]})["expired"] is True

        client.portal.call(lambda: backup_utils.restore_backup(second["storage_path"], MONGO_URL, DB_NAME, target))
        restored = pymongo.MongoClient(MONGO_URL)[target]
        assert restored.vehicle_masters.count_documents({}) == 5
        assert restored.users.count_documents({"email": ADMIN_EMAIL}) == 1
    finally:
        pymongo.MongoClient(MONGO_URL).drop_database(target)


# ---------- configuration ----------
def test_config_check_current_test_env_is_clean():
    assert config_check.problems() == []


@pytest.mark.parametrize("env,fragment", [
    ({"JWT_SECRET": "change-me"}, "JWT_SECRET"),
    ({"STORAGE_BACKEND": "s3"}, "S3_BUCKET"),
    ({"STORAGE_BACKEND": "ftp"}, "STORAGE_BACKEND"),
    ({"EMAIL_PROVIDER": "resend"}, "RESEND_API_KEY"),
    ({"EMAIL_PROVIDER": "smtp"}, "SMTP_HOST"),
    ({"PUBLIC_URL": "http://gate.example.com"}, "https"),
    ({"GOOGLE_CLIENT_ID": "x"}, "GOOGLE_CLIENT_SECRET"),
    ({"DIGEST_TIME": "9am"}, "DIGEST_TIME"),
])
def test_config_check_reports_problems(monkeypatch, env, fragment):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert any(fragment in p for p in config_check.problems())


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}
