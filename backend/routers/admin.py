"""Users, app settings and the retention purge."""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Depends

import plate_pipeline
import security


from core import PasswordChangeIn, PasswordSetIn, RETENTION_DAYS, ROLE_ADMIN_MANAGER, ROLE_ADMIN_ONLY, SettingsIn, TWILIO_TOKEN, UserIn, db, get_current_user, hash_password, logger, now_utc, require_roles, verify_password

router = APIRouter(prefix="/api")

# ===================== Users =====================
@router.get("/users")
async def list_users(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    docs = await db.users.find({}).sort("created_at", -1).to_list(200)
    return [
        {
            "id": str(d["_id"]),
            "name": d.get("name", ""),
            "email": d.get("email", ""),
            "role": d.get("role", "manager"),
            "status": d.get("status", "active"),
            "last_login": d.get("last_login"),
            "created_at": d.get("created_at"),
        }
        for d in docs
    ]


@router.post("/users")
async def create_user(payload: UserIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    password, generated = _password_or_generate(payload.password)
    doc = {
        "name": payload.name,
        "email": payload.email.lower(),
        "role": payload.role,
        "status": payload.status,
        "password_hash": hash_password(password),
        "created_at": now_utc().isoformat(),
        "last_login": None,
    }
    r = await db.users.insert_one(doc)
    await security.audit(db, user, "user.create", doc["email"], role=payload.role)
    out = {"id": str(r.inserted_id), **{k: v for k, v in doc.items() if k not in ("password_hash", "_id")}}
    if generated:
        out["temporary_password"] = password
    return out


def _password_or_generate(password: Optional[str]) -> Tuple[str, bool]:
    """Validate a chosen password, or generate one. Returns (password, was_generated)."""
    if password:
        problem = security.password_problem(password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        return password, False
    return security.generate_password(), True


@router.post("/users/{uid}/password")
async def set_user_password(uid: str, payload: PasswordSetIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Admin resets someone's password (the 'Forgot password?' path)."""
    try:
        oid = ObjectId(uid)
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")
    target = await db.users.find_one({"_id": oid}, {"email": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    password, generated = _password_or_generate(payload.password)
    await db.users.update_one({"_id": oid}, {"$set": {"password_hash": hash_password(password),
                                                       "password_changed_at": now_utc().isoformat()}})
    await security.audit(db, user, "user.password_reset", target["email"])
    return {"ok": True, **({"temporary_password": password} if generated else {})}


@router.get("/audit-log")
async def list_audit_log(limit: int = 200, action: Optional[str] = None,
                         user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    q = {"action": action} if action else {}
    docs = await db.audit_log.find(q, {"_id": 0}).sort("at", -1).to_list(min(max(limit, 1), 1000))
    for d in docs:
        if isinstance(d.get("at"), datetime):
            d["at"] = d["at"].replace(tzinfo=timezone.utc).isoformat()
    return docs


@router.post("/auth/change-password")
async def change_own_password(payload: PasswordChangeIn, user: dict = Depends(get_current_user)):
    doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    if not doc or not verify_password(payload.current_password, doc.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    problem = security.password_problem(payload.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    await db.users.update_one({"_id": doc["_id"]}, {"$set": {"password_hash": hash_password(payload.new_password),
                                                              "password_changed_at": now_utc().isoformat()}})
    await security.audit(db, user, "user.password_change", doc["email"])
    return {"ok": True}


@router.delete("/users/{uid}")
async def delete_user(uid: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    if uid == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    target = await db.users.find_one({"_id": ObjectId(uid)}, {"email": 1, "role": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.delete_one({"_id": target["_id"]})
    await security.audit(db, user, "user.delete", target.get("email", uid))
    return {"ok": True}


# ===================== Settings =====================
DEFAULT_SETTINGS = {
    "company_name": "RDX Hyundai Showroom",
    "company_address": "123 Motor Mile, Downtown",
    "company_phone": "+1 (555) 010-2026",
    "camera_url": "",
    "camera_enabled": False,
    "auto_delete_days": 180,
    "whatsapp_report_time": "09:00",
    "backup_enabled": True,
    "database_url_display": "mongodb://***",
    "twilio_sid": "",
    "twilio_token": "",
    "twilio_from": "",
    "twilio_to": "",
    "digest_extra_emails": [],
}


def _public_settings(doc: Optional[dict]) -> dict:
    """Settings for the UI — secrets are never sent back, only whether they're set."""
    merged = {**DEFAULT_SETTINGS, **(doc or {})}
    merged.pop("_id", None)
    merged.pop("key", None)
    merged["twilio_token_set"] = bool(merged.get("twilio_token") or TWILIO_TOKEN)
    merged["twilio_token"] = ""
    return merged


@router.get("/settings")
async def get_settings(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    return _public_settings(await db.settings.find_one({"key": "app"}))


@router.put("/settings")
async def update_settings(payload: SettingsIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    # A blank token means "keep the saved one" (the UI never receives it).
    if not (updates.get("twilio_token") or "").strip():
        updates.pop("twilio_token", None)
    updates.pop("database_url_display", None)
    await db.settings.update_one({"key": "app"}, {"$set": updates}, upsert=True)
    await security.audit(db, user, "settings.update", "app", fields=sorted(k for k in updates if "token" not in k))
    return _public_settings(await db.settings.find_one({"key": "app"}))


# ===================== Retention purge =====================
# Auto retention is user-requested ("180-day auto cleanup so old visits and
# frames stop piling up in Mongo") but can be disabled globally via
# RETENTION_ENABLED=false if operators want to hold off deletes temporarily.
RETENTION_ENABLED = os.environ.get("RETENTION_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


_RETENTION_IMG_FIELDS = ("entry_image", "exit_image", "entry_plate_image", "exit_plate_image")


async def purge_expired_sessions() -> Dict[str, Any]:
    """Delete finished visits older than RETENTION_DAYS and their snapshot files.

    Visits still marked active (car inside) are never purged, however old.
    Safe to re-run (idempotent).
    """
    cutoff = (now_utc() - timedelta(days=RETENTION_DAYS)).isoformat()
    query = {"status": {"$ne": "active"}, "$or": [
        {"entry_time": {"$lt": cutoff}},
        {"entry_time": None, "exit_time": {"$lt": cutoff}},  # entry_missed visits have no entry time
    ]}
    files: list[str] = []
    expired = 0
    async for doc in db.visit_sessions.find(query, {k: 1 for k in _RETENTION_IMG_FIELDS}):
        expired += 1
        for k in _RETENTION_IMG_FIELDS:
            url = (doc.get(k) or "").strip()
            if url.startswith("/api/snapshots/"):
                files.append(url.rsplit("/", 1)[-1])
    r = await db.visit_sessions.delete_many(query)
    removed = failed = 0
    for name in files:
        try:
            if await plate_pipeline.delete_snapshot(name):
                removed += 1
        except Exception as e:
            failed += 1
            logger.warning("[retention] failed to remove snapshot %s: %s", name, e)
    return {"cutoff": cutoff, "expired_records": expired, "deleted": r.deleted_count,
            "files_removed": removed, "files_failed": failed}


@router.post("/maintenance/purge-expired")
async def purge_expired(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Delete finished visit sessions older than RETENTION_DAYS (and their snapshots)."""
    res = await purge_expired_sessions()
    await security.audit(db, user, "maintenance.purge_expired", "", **res)
    return res


