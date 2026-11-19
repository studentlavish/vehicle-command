"""Startup configuration check — refuse to run with a missing or unsafe setting.

Every problem is listed at once, so a fresh deployment can be fixed in one go.
"""
from __future__ import annotations

import os
from typing import List
from urllib.parse import urlparse

_PLACEHOLDERS = {"", "change-me", "changeme", "secret", "jwt-secret"}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1")


def problems() -> List[str]:
    out: List[str] = []
    for name in ("MONGO_URL", "DB_NAME"):
        if not _env(name):
            out.append(f"{name} is not set")

    jwt_secret = _env("JWT_SECRET")
    if jwt_secret.lower() in _PLACEHOLDERS or len(jwt_secret) < 32:
        out.append('JWT_SECRET must be a random value of at least 32 characters '
                   '(python3 -c "import secrets; print(secrets.token_hex(32))")')

    backend = (_env("STORAGE_BACKEND") or "local").lower()
    if backend not in ("local", "s3"):
        out.append("STORAGE_BACKEND must be local or s3")
    if backend == "s3":
        for name in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
            if not _env(name):
                out.append(f"STORAGE_BACKEND=s3 needs {name}")

    email = _env("EMAIL_PROVIDER").lower()
    if email and email not in ("resend", "smtp", "emergent"):
        out.append("EMAIL_PROVIDER must be resend, smtp or emergent (or empty to disable email)")
    if email == "resend" and not (_env("RESEND_API_KEY") and _env("EMAIL_FROM")):
        out.append("EMAIL_PROVIDER=resend needs RESEND_API_KEY and EMAIL_FROM")
    if email == "smtp" and not (_env("SMTP_HOST") and _env("EMAIL_FROM")):
        out.append("EMAIL_PROVIDER=smtp needs SMTP_HOST and EMAIL_FROM")

    public = _env("PUBLIC_URL")
    if public:
        p = urlparse(public)
        if p.scheme not in ("http", "https") or not p.hostname:
            out.append("PUBLIC_URL must be the dashboard address, e.g. https://gate.example.com")
        elif p.scheme != "https" and not _is_local(public):
            out.append("PUBLIC_URL must use https (cookies are Secure in production)")
    if _env("GOOGLE_CLIENT_ID") and not (_env("GOOGLE_CLIENT_SECRET") and public):
        out.append("Google sign-in needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and PUBLIC_URL")

    for name in ("DIGEST_TIME", "BACKUP_TIME", "RETENTION_TIME"):
        v = _env(name)
        if v:
            try:
                hh, mm = (int(x) for x in v.split(":"))
                assert 0 <= hh < 24 and 0 <= mm < 60
            except Exception:
                out.append(f"{name} must be HH:MM (24-hour), got {v!r}")
    return out


def check_or_exit() -> None:
    found = problems()
    if found:
        raise RuntimeError("Configuration problems — fix backend/.env and restart:\n  - " + "\n  - ".join(found))
