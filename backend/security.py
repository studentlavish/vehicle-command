"""Security helpers: cookie/CORS settings, password policy, login lockout,
agent secrets and the audit log.

All state lives in MongoDB so it works the same with several server workers.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rdx.security")


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


# ---------- Cookies ----------
COOKIE_SECURE = _env_bool("COOKIE_SECURE", True)
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
if COOKIE_SAMESITE not in ("lax", "strict", "none"):
    raise RuntimeError("COOKIE_SAMESITE must be lax, strict or none")
if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")


def cookie_kwargs(max_age: int) -> Dict[str, Any]:
    return {"httponly": True, "secure": COOKIE_SECURE, "samesite": COOKIE_SAMESITE, "max_age": max_age, "path": "/"}


# ---------- CORS ----------
def cors_origins() -> List[str]:
    """Explicit list of allowed browser origins. '*' is refused because the API uses cookies."""
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if not origins:
        raise RuntimeError("CORS_ORIGINS is not set — list the dashboard URL(s), e.g. https://gate.example.com")
    if "*" in origins:
        raise RuntimeError("CORS_ORIGINS='*' is not allowed with cookie auth — list the dashboard URL(s) explicitly")
    return origins


# ---------- Passwords ----------
MIN_PASSWORD_LENGTH = 10
WEAK_PASSWORDS = {"admin123", "admin", "password", "demo1234", "123456", "12345678", "changeme",
                  "password123", "qwerty123", "1234567890"}


def password_problem(pw: str) -> Optional[str]:
    """Return why a new password is unacceptable, or None if it is fine."""
    if len(pw or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if pw.lower() in WEAK_PASSWORDS:
        return "That password is too common — choose another"
    return None


def generate_password() -> str:
    return secrets.token_urlsafe(12)


# ---------- Login lockout ----------
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    await db.login_attempts.create_index("key", unique=True)
    await db.login_attempts.create_index("expires_at", expireAfterSeconds=0)
    await db.audit_log.create_index([("at", -1)])
    try:
        await db.agents.create_index("agent_id", unique=True)
    except Exception as e:  # duplicate legacy rows — keep running, but say so
        logger.error("[security] could not create unique agents.agent_id index (duplicate agents?): %s", e)


def _attempt_key(email: str, ip: str) -> str:
    return f"{(email or '').lower()}|{ip or '?'}"


async def login_locked_for(db, email: str, ip: str) -> int:
    """Seconds left on a lockout for this email+IP (0 when not locked)."""
    doc = await db.login_attempts.find_one({"key": _attempt_key(email, ip)})
    until = doc.get("locked_until") if doc else None
    if until is None:
        return 0
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    left = int((until - _now()).total_seconds())
    return max(0, left)


async def record_login_failure(db, email: str, ip: str) -> None:
    now = _now()
    window = timedelta(minutes=LOGIN_LOCK_MINUTES)
    key = _attempt_key(email, ip)
    doc = await db.login_attempts.find_one_and_update(
        {"key": key},
        {"$inc": {"failures": 1}, "$setOnInsert": {"first_at": now}, "$set": {"expires_at": now + window}},
        upsert=True, return_document=True,
    )
    if doc and doc.get("failures", 0) >= LOGIN_MAX_FAILURES:
        await db.login_attempts.update_one({"key": key}, {"$set": {
            "locked_until": now + window, "expires_at": now + window,
        }})
        logger.warning("[auth] login locked for %s for %d min after %d failures",
                       key, LOGIN_LOCK_MINUTES, doc["failures"])


async def clear_login_failures(db, email: str, ip: str) -> None:
    await db.login_attempts.delete_one({"key": _attempt_key(email, ip)})


# ---------- Agent secrets ----------
def hash_secret(secret: str) -> str:
    """Agent secrets are long random tokens, so a plain SHA-256 is sufficient."""
    return hashlib.sha256((secret or "").encode()).hexdigest()


def secret_matches(secret: Optional[str], expected_hash: str) -> bool:
    return bool(secret) and hmac.compare_digest(hash_secret(secret), expected_hash or "")


def shared_secret_matches(secret: Optional[str], shared: str) -> bool:
    return bool(secret) and bool(shared) and hmac.compare_digest(secret.encode(), shared.encode())


def generate_agent_secret() -> str:
    return secrets.token_urlsafe(32)


# ---------- Audit log ----------
async def audit(db, user: Optional[dict], action: str, target: str = "", **details: Any) -> None:
    """Record who did what. Never raises — auditing must not break the action itself."""
    try:
        await db.audit_log.insert_one({
            "at": _now(),
            "user": (user or {}).get("email", "system"),
            "role": (user or {}).get("role", ""),
            "action": action,
            "target": target,
            "details": details,
        })
    except Exception:
        logger.exception("[audit] failed to record %s %s", action, target)
