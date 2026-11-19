from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import io
import re
import csv
import json
import uuid
import logging
import asyncio
import secrets as pysecrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal, Dict, Any, Tuple

import bcrypt
import jwt
import whatsapp_meta as wa_meta
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr

from camera_manager import camera_manager, CameraSourceConflict
from plate_pipeline import _persist_entry, _persist_lock, route_detection, local_date
import plate_pipeline
from pymongo.errors import DuplicateKeyError
import plate_rules
import local_ocr
from zoneinfo import ZoneInfo
from event_bus import event_bus
import security
import scheduler
import google_oauth
import config_check

# Optional integrations
try:
    from twilio.rest import Client as TwilioClient
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False

# --------- Setup ---------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("rdx")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
ADMIN_EMAIL = os.environ["ADMIN_EMAIL"].lower()
# Only used to create the first admin on a fresh install (see seed_admin).
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
TWILIO_TO_DEFAULT = os.environ.get("TWILIO_WHATSAPP_TO", "")

RETENTION_DAYS = 180

# Day boundaries ("today", daily reports, visit_date) use the showroom's timezone.
APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))

# Role permissions
ROLE_ALL = ("admin", "manager", "security")
ROLE_ADMIN_ONLY = ("admin",)
ROLE_ADMIN_MANAGER = ("admin", "manager")

app = FastAPI(title="RDX Car Showroom API")
api = APIRouter(prefix="/api")


# ===================== Auth helpers =====================
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def set_auth_cookies(response: Response, access: str, refresh: str):
    response.set_cookie("access_token", access, **security.cookie_kwargs(43200))
    response.set_cookie("refresh_token", refresh, **security.cookie_kwargs(604800))


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if user.get("status") == "inactive":
            raise HTTPException(status_code=401, detail="Account is inactive")
        user["id"] = str(user.pop("_id"))
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_roles(*allowed_roles):
    """Dependency factory that ensures the current user has one of the given roles."""
    async def _guard(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(allowed_roles)}")
        return user
    return _guard


# ===================== Time helpers =====================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


class _LocalDT(datetime):
    """A datetime in the showroom's timezone whose isoformat() is UTC.

    Timestamps are stored as UTC ISO strings and compared as strings in Mongo
    queries, so day boundaries computed in local time must serialise to UTC.
    strftime() still shows local values, so labels (Mon, Tue…) are correct.
    """
    def isoformat(self, sep="T", timespec="auto"):
        return datetime.isoformat(datetime.astimezone(self, timezone.utc), sep, timespec)


def start_of_day(dt: datetime) -> datetime:
    """Local midnight (APP_TZ) of the day containing `dt`."""
    loc = dt.astimezone(APP_TZ)
    return _LocalDT(loc.year, loc.month, loc.day, tzinfo=APP_TZ)


def _local_day(date_str: str) -> datetime:
    d = datetime.fromisoformat(date_str[:10])
    return _LocalDT(d.year, d.month, d.day, tzinfo=APP_TZ)


def range_bounds(range_key: str, from_str: Optional[str] = None, to_str: Optional[str] = None) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Return (start, end) datetimes for a named range. end is exclusive."""
    n = now_utc()
    today = start_of_day(n)
    if range_key == "today":
        return today, today + timedelta(days=1)
    if range_key == "yesterday":
        return today - timedelta(days=1), today
    if range_key == "7d":
        return today - timedelta(days=7), today + timedelta(days=1)
    if range_key == "30d":
        return today - timedelta(days=30), today + timedelta(days=1)
    if range_key == "180d":
        return today - timedelta(days=180), today + timedelta(days=1)
    if range_key == "month":
        return today.replace(day=1), today + timedelta(days=1)
    if range_key == "all":
        return None, None
    if range_key == "custom":
        start = _local_day(from_str) if from_str else None
        end = (_local_day(to_str) + timedelta(days=1)) if to_str else None
        return start, end
    # default = all
    return None, None


def apply_range_query(query: Dict[str, Any], start: Optional[datetime], end: Optional[datetime], field: str = "entry_time"):
    if start or end:
        q: Dict[str, Any] = {}
        if start:
            q["$gte"] = start.isoformat()
        if end:
            q["$lt"] = end.isoformat()
        query[field] = q


# ===================== Serializers =====================
def serialize_master(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "vehicle_number": doc["vehicle_number"],
        "owner_name": doc.get("owner_name", ""),
        "phone_number": doc.get("phone_number", ""),
        "vehicle_model": doc.get("vehicle_model", ""),
        "vehicle_image": doc.get("vehicle_image", ""),
        "customer_id": doc.get("customer_id", ""),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def serialize_session(doc: dict) -> dict:
    duration = doc.get("duration_seconds")
    if duration is None and doc.get("exit_time") and doc.get("entry_time"):
        try:
            duration = int((datetime.fromisoformat(doc["exit_time"]) - datetime.fromisoformat(doc["entry_time"])).total_seconds())
        except Exception:
            duration = None
    return {
        "id": doc.get("id"),
        "vehicle_number": doc["vehicle_number"],
        "entry_time": doc.get("entry_time"),
        "exit_time": doc.get("exit_time"),
        "visit_date": doc.get("visit_date"),
        "duration_seconds": duration,
        "entry_camera": doc.get("entry_camera", ""),
        "exit_camera": doc.get("exit_camera", ""),
        "entry_image": doc.get("entry_image", ""),
        "exit_image": doc.get("exit_image", ""),
        "status": doc.get("status", "active"),
        "created_at": doc.get("created_at"),
    }


# Backward-compat: existing "vehicle records" table treats each session as a row and joins master info.
async def serialize_session_with_master(doc: dict, master: Optional[dict] = None) -> dict:
    if master is None:
        master = await db.vehicle_masters.find_one({"vehicle_number": doc["vehicle_number"]})
    s = serialize_session(doc)
    s["owner_name"] = (master or {}).get("owner_name", "")
    s["contact_number"] = (master or {}).get("phone_number", "")
    s["vehicle_model"] = (master or {}).get("vehicle_model", "")
    s["notes"] = doc.get("notes", "")
    # legacy status mapping
    if s["status"] == "active":
        s["status"] = "inside"
    elif s["status"] == "completed":
        s["status"] = "exited"
    return s


async def _batch_master_map(docs: list) -> Dict[str, dict]:
    """Pre-fetch every vehicle_master referenced by a list of sessions in one query."""
    numbers = list({d["vehicle_number"] for d in docs if d.get("vehicle_number")})
    if not numbers:
        return {}
    masters = await db.vehicle_masters.find({"vehicle_number": {"$in": numbers}}).to_list(len(numbers))
    return {m["vehicle_number"]: m for m in masters}


# ===================== Models =====================
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    remember_me: Optional[bool] = False


class EntryIn(BaseModel):
    vehicle_number: str
    owner_name: Optional[str] = None
    phone_number: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_image: Optional[str] = None
    entry_camera: Optional[str] = "CAM 01"
    entry_image: Optional[str] = None


class ExitIn(BaseModel):
    vehicle_number: str
    exit_camera: Optional[str] = "CAM 02"
    exit_image: Optional[str] = None
    session_id: Optional[str] = None  # if provided, close this specific session


class MasterIn(BaseModel):
    vehicle_number: str
    owner_name: str
    phone_number: str
    vehicle_model: Optional[str] = ""
    vehicle_image: Optional[str] = ""


class MasterPatch(BaseModel):
    owner_name: Optional[str] = None
    phone_number: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_image: Optional[str] = None


# Backward-compat models for existing /api/vehicles endpoints
class VehicleIn(BaseModel):
    vehicle_number: str
    owner_name: str
    contact_number: str = ""
    vehicle_model: str = ""
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    status: Literal["inside", "exited", "pending"] = "inside"
    notes: str = ""


class VehiclePatch(BaseModel):
    vehicle_number: Optional[str] = None
    owner_name: Optional[str] = None
    contact_number: Optional[str] = None
    vehicle_model: Optional[str] = None
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    status: Optional[Literal["inside", "exited", "pending"]] = None
    notes: Optional[str] = None


class UserIn(BaseModel):
    name: str
    email: EmailStr
    role: Literal["admin", "manager", "security"] = "manager"
    status: Literal["active", "inactive"] = "active"
    # Optional — when omitted a temporary password is generated and returned once.
    password: Optional[str] = None


class PasswordSetIn(BaseModel):
    password: Optional[str] = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class SettingsIn(BaseModel):
    company_name: Optional[str] = None
    company_address: Optional[str] = None
    company_phone: Optional[str] = None
    camera_url: Optional[str] = None
    camera_enabled: Optional[bool] = None
    auto_delete_days: Optional[int] = None
    whatsapp_report_time: Optional[str] = None
    backup_enabled: Optional[bool] = None
    database_url_display: Optional[str] = None
    twilio_sid: Optional[str] = None
    twilio_token: Optional[str] = None
    twilio_from: Optional[str] = None
    twilio_to: Optional[str] = None
    digest_extra_emails: Optional[List[str]] = None


# ===================== Auth Routes =====================
@api.post("/auth/login")
async def login(payload: LoginIn, request: Request, response: Response):
    email = payload.email.lower()
    ip = request.client.host if request.client else ""
    locked = await security.login_locked_for(db, email, ip)
    if locked:
        raise HTTPException(status_code=429, detail=f"Too many failed sign-in attempts. Try again in {locked // 60 + 1} min.",
                            headers={"Retry-After": str(locked)})
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        await security.record_login_failure(db, email, ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await security.clear_login_failures(db, email, ip)
    if user.get("status") == "inactive":
        raise HTTPException(status_code=403, detail="Account is inactive")
    if user.get("role") not in ROLE_ALL:
        raise HTTPException(status_code=403, detail="Unknown role")
    uid = str(user["_id"])
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": now_utc().isoformat()}})
    return {
        "id": uid,
        "email": email,
        "name": user.get("name", "User"),
        "role": user.get("role"),
        "token": access,
    }


# ---------- Sign in with Google (direct OAuth, see google_oauth.py) ----------
def _login_redirect(error: str = "") -> RedirectResponse:
    target = google_oauth.public_url() or ""
    return RedirectResponse(f"{target}/login?error={error}" if error else f"{target}/", status_code=303)


@api.get("/auth/config")
async def auth_config():
    """What the login page should offer."""
    return {"google": google_oauth.enabled()}


@api.get("/auth/google/start")
async def google_start():
    if not google_oauth.enabled():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    flow = google_oauth.new_flow()
    resp = RedirectResponse(google_oauth.authorization_url(flow), status_code=303)
    token = jwt.encode({**flow, "exp": datetime.now(timezone.utc) + timedelta(seconds=google_oauth.FLOW_SECONDS)},
                       JWT_SECRET, algorithm=JWT_ALG)
    resp.set_cookie(google_oauth.FLOW_COOKIE, token, **{**security.cookie_kwargs(google_oauth.FLOW_SECONDS),
                                                        "path": "/api/auth/google"})
    return resp


@api.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google sends the user back here. Only emails already listed under Users may sign in."""
    def fail(reason: str) -> RedirectResponse:
        r = _login_redirect(reason)
        r.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
        return r

    if error or not code:
        return fail("google_cancelled")
    try:
        flow = jwt.decode(request.cookies.get(google_oauth.FLOW_COOKIE, ""), JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return fail("google_expired")
    if not state or not pysecrets.compare_digest(state, flow.get("state", "")):
        return fail("google_state")
    try:
        claims = await google_oauth.identity_from_code(code, flow)
    except Exception as e:
        logger.warning("[auth] Google sign-in failed: %s", e)
        return fail("google_failed")
    email = claims["email"].strip().lower()
    user = await db.users.find_one({"email": email})
    if not user:
        logger.info("[auth] Google sign-in refused for %s (not under Users)", email)
        return fail("google_not_allowed")
    if user.get("status") == "inactive" or user.get("role") not in ROLE_ALL:
        return fail("google_not_allowed")
    uid = str(user["_id"])
    update = {"last_login": now_utc().isoformat(), "auth_provider": "google"}
    if claims.get("name") and not user.get("name"):
        update["name"] = claims["name"]
    if claims.get("picture"):
        update["picture"] = claims["picture"]
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    await security.audit(db, {"email": email, "role": user.get("role")}, "auth.google_login", email)
    resp = _login_redirect()
    set_auth_cookies(resp, create_access_token(uid, email), create_refresh_token(uid))
    resp.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
    return resp


@api.post("/auth/logout")
async def logout(request: Request, response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@api.post("/auth/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user or user.get("status") == "inactive":
            raise HTTPException(status_code=401, detail="User not found or inactive")
        access = create_access_token(str(user["_id"]), user["email"])
        response.set_cookie("access_token", access, **security.cookie_kwargs(43200))
        return {"ok": True}
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


# ===================== Vehicle Visit Session core =====================
def normalize_plate(vn: str) -> str:
    return "".join(vn.upper().split())


async def upsert_master(*, vehicle_number: str, owner_name: Optional[str] = None, phone_number: Optional[str] = None,
                        vehicle_model: Optional[str] = None, vehicle_image: Optional[str] = None) -> dict:
    vn = normalize_plate(vehicle_number)
    existing = await db.vehicle_masters.find_one({"vehicle_number": vn})
    now = now_utc().isoformat()
    if existing is None:
        doc = {
            "id": str(uuid.uuid4()),
            "vehicle_number": vn,
            "owner_name": owner_name or "Unknown Owner",
            "phone_number": phone_number or "",
            "vehicle_model": vehicle_model or "",
            "vehicle_image": vehicle_image or "",
            "customer_id": f"CUS-{pysecrets.token_hex(3).upper()}",
            "created_at": now,
            "updated_at": now,
        }
        await db.vehicle_masters.insert_one(doc)
        doc.pop("_id", None)
        return doc
    updates: Dict[str, Any] = {"updated_at": now}
    if owner_name and existing.get("owner_name") in ("", "Unknown Owner", None):
        updates["owner_name"] = owner_name
    if phone_number and not existing.get("phone_number"):
        updates["phone_number"] = phone_number
    if vehicle_model and not existing.get("vehicle_model"):
        updates["vehicle_model"] = vehicle_model
    if vehicle_image and not existing.get("vehicle_image"):
        updates["vehicle_image"] = vehicle_image
    if len(updates) > 1:
        await db.vehicle_masters.update_one({"_id": existing["_id"]}, {"$set": updates})
    return {**existing, **updates}


def _require_valid_plate(vn: str) -> str:
    """Reject empty/invalid plates — no ENTRY may be created from them."""
    if not vn or len(vn) < 5 or not vn.isalnum():
        raise HTTPException(status_code=400, detail="Invalid or missing vehicle plate")
    return vn


@api.post("/vehicles/entry")
async def record_entry(payload: EntryIn, user: dict = Depends(get_current_user)):
    vn = _require_valid_plate(normalize_plate(payload.vehicle_number))
    async with _persist_lock:
        # ACTIVE SESSION WINS — one normalized plate = at most one active session.
        active = await db.visit_sessions.find_one({"vehicle_number": vn, "status": "active"})
        if active:
            raise HTTPException(status_code=409, detail={
                "error": "already_inside",
                "message": f"{vn} already has an active session",
                "session_id": active.get("id"),
            })
        master = await upsert_master(
            vehicle_number=vn,
            owner_name=payload.owner_name,
            phone_number=payload.phone_number,
            vehicle_model=payload.vehicle_model,
            vehicle_image=payload.vehicle_image,
        )
        now = now_utc()
        session = {
            "id": str(uuid.uuid4()),
            "vehicle_number": vn,
            "entry_time": now.isoformat(),
            "exit_time": None,
            "visit_date": local_date(now),
            "duration_seconds": None,
            "entry_camera": payload.entry_camera or "CAM 01",
            "exit_camera": "",
            "entry_image": payload.entry_image or "",
            "exit_image": "",
            "status": "active",
            "created_at": now.isoformat(),
            "detected_by": "manual",
        }
        try:
            await db.visit_sessions.insert_one(session)
        except DuplicateKeyError:
            raise HTTPException(status_code=409, detail={"error": "already_inside", "message": f"{vn} already has an active session"})
        session.pop("_id", None)
    return {"master": serialize_master(master), "session": serialize_session(session)}


@api.post("/vehicles/exit")
async def record_exit(payload: ExitIn, user: dict = Depends(get_current_user)):
    vn = normalize_plate(payload.vehicle_number)
    query = {"vehicle_number": vn, "status": "active"}
    if payload.session_id:
        query = {"id": payload.session_id, "status": "active"}
    session = await db.visit_sessions.find_one(query, sort=[("entry_time", -1)])
    if not session:
        raise HTTPException(status_code=404, detail="No active session for this vehicle")
    now = now_utc()
    entry_dt = datetime.fromisoformat(session["entry_time"])
    duration = int((now - entry_dt).total_seconds())
    updates = {
        "exit_time": now.isoformat(),
        "duration_seconds": duration,
        "exit_camera": payload.exit_camera or "CAM 02",
        "exit_image": payload.exit_image or "",
        "status": "completed",
    }
    await db.visit_sessions.update_one({"_id": session["_id"]}, {"$set": updates})
    session.update(updates)
    session.pop("_id", None)
    return serialize_session(session)


# ---------- Vehicle Master search + detail ----------
@api.get("/vehicles/search")
async def search_masters(q: str, limit: int = 20, user: dict = Depends(get_current_user)):
    """Search vehicle masters by number, phone, owner name, or customer id."""
    q_clean = q.strip()
    if not q_clean:
        return []
    up = q_clean.upper()
    query = {
        "$or": [
            {"vehicle_number": {"$regex": re.escape(normalize_plate(q_clean)), "$options": "i"}},
            {"phone_number": {"$regex": re.escape(q_clean), "$options": "i"}},
            {"owner_name": {"$regex": re.escape(q_clean), "$options": "i"}},
            {"customer_id": {"$regex": re.escape(up), "$options": "i"}},
        ]
    }
    docs = await db.vehicle_masters.find(query).sort("updated_at", -1).to_list(limit)
    # Batch counts to avoid N+1: one aggregate for totals, one for actives.
    numbers = [m["vehicle_number"] for m in docs]
    total_map: Dict[str, int] = {}
    active_map: Dict[str, int] = {}
    if numbers:
        async for row in db.visit_sessions.aggregate([
            {"$match": {"vehicle_number": {"$in": numbers}}},
            {"$group": {"_id": "$vehicle_number", "count": {"$sum": 1}}},
        ]):
            total_map[row["_id"]] = row["count"]
        async for row in db.visit_sessions.aggregate([
            {"$match": {"vehicle_number": {"$in": numbers}, "status": "active"}},
            {"$group": {"_id": "$vehicle_number", "count": {"$sum": 1}}},
        ]):
            active_map[row["_id"]] = row["count"]
    out = []
    for m in docs:
        vn = m["vehicle_number"]
        out.append({
            **serialize_master(m),
            "total_visits": total_map.get(vn, 0),
            "active_visits": active_map.get(vn, 0),
        })
    return out


@api.get("/vehicles/master/{vehicle_number}")
async def get_vehicle_detail(vehicle_number: str, user: dict = Depends(get_current_user)):
    vn = normalize_plate(vehicle_number)
    master = await db.vehicle_masters.find_one({"vehicle_number": vn})
    if not master:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    n = now_utc()
    today_start = start_of_day(n)
    month_start = today_start.replace(day=1)
    horizon_180 = today_start - timedelta(days=RETENTION_DAYS)

    async def stats_for(range_start: Optional[datetime]) -> Dict[str, int]:
        q: Dict[str, Any] = {"vehicle_number": vn}
        if range_start:
            q["entry_time"] = {"$gte": range_start.isoformat()}
        pipeline = [
            {"$match": q},
            {"$group": {
                "_id": None,
                "count": {"$sum": 1},
                "total_seconds": {"$sum": {"$ifNull": ["$duration_seconds", 0]}},
            }},
        ]
        agg = await db.visit_sessions.aggregate(pipeline).to_list(1)
        if not agg:
            return {"count": 0, "total_seconds": 0}
        row = agg[0]
        return {"count": row.get("count", 0), "total_seconds": row.get("total_seconds", 0)}

    today = await stats_for(today_start)
    month = await stats_for(month_start)
    overall = await stats_for(horizon_180)

    # Analytics: avg / longest / shortest across last 180 days completed sessions
    ana_pipeline = [
        {"$match": {"vehicle_number": vn, "status": "completed", "entry_time": {"$gte": horizon_180.isoformat()}, "duration_seconds": {"$gt": 0}}},
        {"$group": {
            "_id": None,
            "avg": {"$avg": "$duration_seconds"},
            "max": {"$max": "$duration_seconds"},
            "min": {"$min": "$duration_seconds"},
        }},
    ]
    a = await db.visit_sessions.aggregate(ana_pipeline).to_list(1)
    analytics = {
        "avg_seconds": int((a[0].get("avg") or 0)) if a else 0,
        "longest_seconds": int((a[0].get("max") or 0)) if a else 0,
        "shortest_seconds": int((a[0].get("min") or 0)) if a else 0,
    }

    return {
        "master": serialize_master(master),
        "summary": {
            "today_visits": today["count"],
            "today_stay_seconds": today["total_seconds"],
            "month_visits": month["count"],
            "month_stay_seconds": month["total_seconds"],
            "overall_visits": overall["count"],
            "overall_stay_seconds": overall["total_seconds"],
        },
        "analytics": analytics,
    }


@api.get("/vehicles/master/{vehicle_number}/sessions")
async def list_vehicle_sessions(
    vehicle_number: str,
    range: str = "180d",
    from_: Optional[str] = None,
    to: Optional[str] = None,
    limit: int = 500,
    user: dict = Depends(get_current_user),
):
    vn = normalize_plate(vehicle_number)
    master = await db.vehicle_masters.find_one({"vehicle_number": vn})
    if not master:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    start, end = range_bounds(range, from_, to)
    query: Dict[str, Any] = {"vehicle_number": vn}
    apply_range_query(query, start, end, field="entry_time")
    docs = await db.visit_sessions.find(query).sort("entry_time", 1).to_list(limit)
    return [serialize_session(d) for d in docs]


@api.get("/vehicles/master/{vehicle_number}/sessions/paginated")
async def get_vehicle_sessions_paginated(
    vehicle_number: str,
    range: str = "180d",
    from_: Optional[str] = None,
    to: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    user: dict = Depends(get_current_user),
):
    vn = normalize_plate(vehicle_number)
    if not await db.vehicle_masters.find_one({"vehicle_number": vn}):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    start, end = range_bounds(range, from_, to)
    query: Dict[str, Any] = {"vehicle_number": vn}
    apply_range_query(query, start, end, field="entry_time")
    p, ps = _paginate_params(page, page_size, 50)
    total = await db.visit_sessions.count_documents(query)
    docs = await db.visit_sessions.find(query).sort("entry_time", -1).skip((p - 1) * ps).to_list(ps)
    return {"items": [serialize_session(d) for d in docs], "total": total,
            "page": p, "page_size": ps, "has_next": (p * ps) < total, "has_prev": p > 1}


@api.patch("/vehicles/master/{vehicle_number}")
async def patch_vehicle_master(vehicle_number: str, payload: MasterPatch, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    vn = normalize_plate(vehicle_number)
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = now_utc().isoformat()
    result = await db.vehicle_masters.update_one({"vehicle_number": vn}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await security.audit(db, user, "vehicle_master.update", vn, fields=sorted(k for k in updates if k != "updated_at"))
    doc = await db.vehicle_masters.find_one({"vehicle_number": vn})
    return serialize_master(doc)


# ---------- Global visit session listing ----------
# Pagination policy (applies to visit-sessions, vehicles, and per-vehicle sessions):
#   • Legacy behaviour preserved: when the client omits `page`, we return the
#     plain JSON array (existing frontend contracts unchanged).
#   • When the client passes `page`, we return a paginated envelope with
#     items + total + page + page_size + has_next + has_prev. `limit` is
#     still honoured as a maximum page size cap.
MAX_PAGE_SIZE = 500


def _paginate_params(page: Optional[int], page_size: Optional[int], limit_default: int) -> tuple[Optional[int], int]:
    if page is None:
        return None, min(max(1, limit_default), MAX_PAGE_SIZE * 4)  # legacy: return up to limit rows
    p = max(1, int(page))
    ps = min(max(1, int(page_size or 50)), MAX_PAGE_SIZE)
    return p, ps


MAX_VISIT_HOURS = float(os.environ.get("MAX_VISIT_HOURS", "24"))


@api.get("/visit-sessions/long-open")
async def list_long_open(hours: Optional[float] = None, user: dict = Depends(require_roles(*ROLE_ALL))):
    """Visits still open after MAX_VISIT_HOURS — a missed exit or a car kept overnight. Never auto-closed."""
    h = hours if hours and hours > 0 else MAX_VISIT_HOURS
    return {"hours": h, "sessions": await plate_pipeline.long_open_sessions(db, h)}


@api.get("/visit-sessions")
async def list_visit_sessions(
    q: Optional[str] = None,
    status: Optional[str] = None,
    range: str = "all",
    from_: Optional[str] = None,
    to: Optional[str] = None,
    limit: int = 500,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if q:
        query["vehicle_number"] = {"$regex": normalize_plate(q), "$options": "i"}
    if status and status != "all":
        mapping = {"inside": "active", "exited": "completed", "pending": "pending"}
        query["status"] = mapping.get(status, status)
    start, end = range_bounds(range, from_, to)
    apply_range_query(query, start, end, field="entry_time")

    p, ps = _paginate_params(page, page_size, limit)
    cursor = db.visit_sessions.find(query).sort("entry_time", -1)
    if p is None:
        docs = await cursor.to_list(ps)
        m_map = await _batch_master_map(docs)
        return [await serialize_session_with_master(d, master=m_map.get(d["vehicle_number"])) for d in docs]
    total = await db.visit_sessions.count_documents(query)
    docs = await cursor.skip((p - 1) * ps).to_list(ps)
    m_map = await _batch_master_map(docs)
    items = [await serialize_session_with_master(d, master=m_map.get(d["vehicle_number"])) for d in docs]
    return {"items": items, "total": total, "page": p, "page_size": ps,
            "has_next": (p * ps) < total, "has_prev": p > 1}


# ===================== Legacy /api/vehicles CRUD (backward compat) =====================
@api.get("/vehicles")
async def list_vehicles(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 500,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if q:
        matching_masters = await db.vehicle_masters.find({
            "$or": [
                {"vehicle_number": {"$regex": re.escape(normalize_plate(q)), "$options": "i"}},
                {"owner_name": {"$regex": re.escape(q), "$options": "i"}},
                {"phone_number": {"$regex": re.escape(q), "$options": "i"}},
                {"customer_id": {"$regex": re.escape(q.upper()), "$options": "i"}},
            ]
        }).to_list(1000)
        numbers = [m["vehicle_number"] for m in matching_masters]
        query["vehicle_number"] = {"$in": numbers} if numbers else {"$regex": re.escape(normalize_plate(q)), "$options": "i"}
    if status and status != "all":
        mapping = {"inside": "active", "exited": "completed", "pending": "pending"}
        query["status"] = mapping.get(status, status)
    if date:
        query["visit_date"] = date[:10]

    p, ps = _paginate_params(page, page_size, limit)
    cursor = db.visit_sessions.find(query).sort("entry_time", -1)
    if p is None:
        docs = await cursor.to_list(ps)
        m_map = await _batch_master_map(docs)
        return [await serialize_session_with_master(d, master=m_map.get(d["vehicle_number"])) for d in docs]
    total = await db.visit_sessions.count_documents(query)
    docs = await cursor.skip((p - 1) * ps).to_list(ps)
    m_map = await _batch_master_map(docs)
    items = [await serialize_session_with_master(d, master=m_map.get(d["vehicle_number"])) for d in docs]
    return {"items": items, "total": total, "page": p, "page_size": ps,
            "has_next": (p * ps) < total, "has_prev": p > 1}


@api.post("/vehicles")
async def create_vehicle(payload: VehicleIn, user: dict = Depends(get_current_user)):
    """Legacy create: upserts master and records a session at the given entry_time."""
    vn = normalize_plate(payload.vehicle_number)
    await upsert_master(
        vehicle_number=vn,
        owner_name=payload.owner_name,
        phone_number=payload.contact_number,
        vehicle_model=payload.vehicle_model,
    )
    entry_dt = datetime.fromisoformat(payload.entry_time) if payload.entry_time else now_utc()
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    exit_dt = None
    if payload.exit_time:
        exit_dt = datetime.fromisoformat(payload.exit_time)
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
    duration = int((exit_dt - entry_dt).total_seconds()) if exit_dt else None
    legacy_status = payload.status
    new_status = {"inside": "active", "exited": "completed", "pending": "pending"}.get(legacy_status, "active")
    doc = {
        "id": str(uuid.uuid4()),
        "vehicle_number": vn,
        "entry_time": entry_dt.isoformat(),
        "exit_time": exit_dt.isoformat() if exit_dt else None,
        "visit_date": local_date(entry_dt),
        "duration_seconds": duration,
        "entry_camera": "CAM 01",
        "exit_camera": "CAM 02" if exit_dt else "",
        "entry_image": "",
        "exit_image": "",
        "status": new_status,
        "notes": payload.notes or "",
        "created_at": now_utc().isoformat(),
    }
    if new_status == "active":
        # ACTIVE SESSION WINS — guard applies only to new INSIDE records;
        # historical (completed/pending) record creation stays unchanged.
        _require_valid_plate(vn)
        async with _persist_lock:
            active = await db.visit_sessions.find_one({"vehicle_number": vn, "status": "active"})
            if active:
                raise HTTPException(status_code=409, detail={
                    "error": "already_inside",
                    "message": f"{vn} already has an active session",
                    "session_id": active.get("id"),
                })
            await db.visit_sessions.insert_one(doc)
    else:
        await db.visit_sessions.insert_one(doc)
    doc.pop("_id", None)
    return await serialize_session_with_master(doc)


@api.get("/vehicles/{vid}")
async def get_vehicle(vid: str, user: dict = Depends(get_current_user)):
    doc = await db.visit_sessions.find_one({"id": vid})
    if not doc:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
    return await serialize_session_with_master(doc)


@api.patch("/vehicles/{vid}")
async def update_vehicle(vid: str, payload: VehiclePatch, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Map legacy status
    if "status" in updates:
        updates["status"] = {"inside": "active", "exited": "completed", "pending": "pending"}.get(updates["status"], updates["status"])
    if updates.get("status") == "completed" and "exit_time" not in updates:
        updates["exit_time"] = now_utc().isoformat()
    # Recompute duration if both times present
    if "exit_time" in updates or "entry_time" in updates:
        current = await db.visit_sessions.find_one({"id": vid})
        entry_time = updates.get("entry_time", (current or {}).get("entry_time"))
        exit_time = updates.get("exit_time", (current or {}).get("exit_time"))
        if entry_time and exit_time:
            try:
                updates["duration_seconds"] = int((datetime.fromisoformat(exit_time) - datetime.fromisoformat(entry_time)).total_seconds())
            except Exception:
                pass
    # Update master fields if provided
    master_updates: Dict[str, Any] = {}
    if "owner_name" in updates:
        master_updates["owner_name"] = updates.pop("owner_name")
    if "contact_number" in updates:
        master_updates["phone_number"] = updates.pop("contact_number")
    if "vehicle_model" in updates:
        master_updates["vehicle_model"] = updates.pop("vehicle_model")
    result = await db.visit_sessions.update_one({"id": vid}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
    await security.audit(db, user, "visit.update", vid, fields=sorted(set(updates) | set(master_updates)))
    doc = await db.visit_sessions.find_one({"id": vid})
    if master_updates:
        master_updates["updated_at"] = now_utc().isoformat()
        await db.vehicle_masters.update_one({"vehicle_number": doc["vehicle_number"]}, {"$set": master_updates})
    return await serialize_session_with_master(doc)


@api.delete("/vehicles/{vid}")
async def delete_vehicle(vid: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    doc = await db.visit_sessions.find_one({"id": vid}, {"_id": 0, "vehicle_number": 1, "entry_time": 1, "status": 1})
    result = await db.visit_sessions.delete_one({"id": vid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
    await security.audit(db, user, "visit.delete", vid, **(doc or {}))
    return {"ok": True}


# ===================== Dashboard =====================
@api.get("/dashboard/stats")
async def dashboard_stats(user: dict = Depends(get_current_user)):
    n = now_utc()
    today = start_of_day(n)
    yday = today - timedelta(days=1)

    async def cnt(field: str, start: datetime, end: datetime, extra: Optional[Dict[str, Any]] = None) -> int:
        q: Dict[str, Any] = {field: {"$gte": start.isoformat(), "$lt": end.isoformat()}}
        if extra:
            q.update(extra)
        return await db.visit_sessions.count_documents(q)

    today_entries = await cnt("entry_time", today, today + timedelta(days=1))
    today_exits = await cnt("exit_time", today, today + timedelta(days=1))
    yday_entries = await cnt("entry_time", yday, today)
    yday_exits = await cnt("exit_time", yday, today)
    cars_inside = await db.visit_sessions.count_documents({"status": "active"})
    total_vehicles = await db.vehicle_masters.count_documents({})

    month_start = today.replace(day=1)
    monthly_visitors = await db.visit_sessions.count_documents({"entry_time": {"$gte": month_start.isoformat()}})

    async def daily_series(field: str, days: int = 7):
        out = []
        end = today + timedelta(days=1)
        for i in range(days, 0, -1):
            day_start = end - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            c = await db.visit_sessions.count_documents({field: {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            out.append({"label": day_start.strftime("%a"), "value": c})
        return out

    entry_trend = await daily_series("entry_time")
    exit_trend = await daily_series("exit_time")

    def pct(cur: int, prev: int) -> float:
        if prev == 0:
            return 100.0 if cur > 0 else 0.0
        return round(((cur - prev) / prev) * 100, 1)

    return {
        "today_entries": {"value": today_entries, "change": pct(today_entries, yday_entries), "trend": entry_trend},
        "today_exits": {"value": today_exits, "change": pct(today_exits, yday_exits), "trend": exit_trend},
        "cars_inside": {"value": cars_inside, "change": 0, "trend": entry_trend},
        "total_vehicles": {"value": total_vehicles, "change": 0, "trend": entry_trend},
        "visitors_today": {"value": today_entries, "change": pct(today_entries, yday_entries), "trend": entry_trend},
        "monthly_visitors": {"value": monthly_visitors, "change": 0, "trend": entry_trend},
    }


# ===================== Analytics =====================
@api.get("/analytics/overview")
async def analytics_overview(user: dict = Depends(get_current_user)):
    async def series(days: int, label_fmt: str):
        out = []
        end = start_of_day(now_utc()) + timedelta(days=1)
        for i in range(days, 0, -1):
            day_start = end - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            out.append({"label": day_start.strftime(label_fmt), "entries": entries, "exits": exits})
        return out

    daily = await series(7, "%a")
    weekly = await series(28, "%d %b")
    monthly = []
    now = start_of_day(now_utc()).replace(day=1)
    for i in range(11, -1, -1):
        month_start = (now - timedelta(days=30 * i)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": month_start.isoformat(), "$lt": next_month.isoformat()}})
        exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": month_start.isoformat(), "$lt": next_month.isoformat()}})
        monthly.append({"label": month_start.strftime("%b"), "entries": entries, "exits": exits})

    total_entries = await db.visit_sessions.count_documents({})
    total_exits = await db.visit_sessions.count_documents({"exit_time": {"$ne": None}})

    # Stay-time aggregate
    horizon = (start_of_day(now_utc()) - timedelta(days=RETENTION_DAYS)).isoformat()
    stay_pipeline = [
        {"$match": {"status": "completed", "entry_time": {"$gte": horizon}, "duration_seconds": {"$gt": 0}}},
        {"$group": {
            "_id": None,
            "avg": {"$avg": "$duration_seconds"},
            "max": {"$max": "$duration_seconds"},
            "min": {"$min": "$duration_seconds"},
            "count": {"$sum": 1},
        }},
    ]
    s = await db.visit_sessions.aggregate(stay_pipeline).to_list(1)
    stay = s[0] if s else {}

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "pie": [
            {"name": "Entries", "value": total_entries},
            {"name": "Exits", "value": total_exits},
        ],
        "stay_time": {
            "avg_seconds": int(stay.get("avg") or 0),
            "longest_seconds": int(stay.get("max") or 0),
            "shortest_seconds": int(stay.get("min") or 0),
            "completed_sessions": int(stay.get("count") or 0),
        },
    }


# ===================== Reports / Exports =====================
def _rows_to_csv(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Vehicle #", "Owner", "Contact", "Model", "Entry", "Exit", "Duration (s)", "Status", "Visit Date", "Entry Camera", "Exit Camera"])
    for r in rows:
        writer.writerow([
            r.get("vehicle_number", ""), r.get("owner_name", ""), r.get("contact_number", ""),
            r.get("vehicle_model", ""), r.get("entry_time", "") or "", r.get("exit_time", "") or "",
            r.get("duration_seconds", "") or "", r.get("status", ""),
            r.get("visit_date", ""), r.get("entry_camera", ""), r.get("exit_camera", ""),
        ])
    return buf.getvalue().encode()


def _rows_to_xlsx(rows: List[Dict[str, Any]], title: str) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(["Vehicle #", "Owner", "Contact", "Model", "Entry", "Exit", "Duration (s)", "Status", "Visit Date", "Entry Camera", "Exit Camera"])
    for r in rows:
        ws.append([
            r.get("vehicle_number", ""), r.get("owner_name", ""), r.get("contact_number", ""),
            r.get("vehicle_model", ""), r.get("entry_time", "") or "", r.get("exit_time", "") or "",
            r.get("duration_seconds", "") or "", r.get("status", ""),
            r.get("visit_date", ""), r.get("entry_camera", ""), r.get("exit_camera", ""),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows_to_pdf(rows: List[Dict[str, Any]], title: str) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    data = [["Vehicle #", "Owner", "Contact", "Entry", "Exit", "Duration", "Status"]]
    for r in rows:
        data.append([
            r.get("vehicle_number", ""),
            r.get("owner_name", ""),
            r.get("contact_number", ""),
            (r.get("entry_time") or "")[:16],
            (r.get("exit_time") or "")[:16] or "—",
            _fmt_duration(r.get("duration_seconds")),
            r.get("status", ""),
        ])
    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    doc.build([Paragraph(title, styles["Title"]), Spacer(1, 12), tbl])
    return buf.getvalue()


def _fmt_duration(seconds):
    if not seconds or seconds <= 0:
        return "—"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _report_query(period: str) -> Dict[str, Any]:
    n = now_utc()
    start = n - timedelta(days=365)  # explicit default so `start` is always defined
    if period == "daily":
        start = start_of_day(n)
    elif period == "weekly":
        start = n - timedelta(days=7)
    elif period == "monthly":
        start = n - timedelta(days=30)
    return {"entry_time": {"$gte": start.isoformat()}}


@api.get("/reports/export")
async def export_report(period: str = "daily", format: str = "csv", user: dict = Depends(get_current_user)):
    # No silent truncation — iterate the cursor to include every matching row.
    rows: list = []
    async for d in db.visit_sessions.find(_report_query(period)).sort("entry_time", -1):
        rows.append(await serialize_session_with_master(d))
    filename_base = f"rdx_report_{period}_{now_utc().strftime('%Y%m%d_%H%M%S')}"
    if format == "csv":
        return StreamingResponse(io.BytesIO(_rows_to_csv(rows)), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={filename_base}.csv"})
    if format == "xlsx":
        return StreamingResponse(io.BytesIO(_rows_to_xlsx(rows, f"{period.capitalize()} Report")),
                                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f"attachment; filename={filename_base}.xlsx"})
    if format == "pdf":
        return StreamingResponse(io.BytesIO(_rows_to_pdf(rows, f"RDX Car Showroom — {period.capitalize()} Report")),
                                 media_type="application/pdf",
                                 headers={"Content-Disposition": f"attachment; filename={filename_base}.pdf"})
    raise HTTPException(status_code=400, detail="Invalid format. Use csv, xlsx, or pdf.")


@api.get("/visits/export")
async def export_visits(
    vehicle_number: Optional[str] = None,
    range: str = "180d",
    from_: Optional[str] = None,
    to: Optional[str] = None,
    format: str = "csv",
    user: dict = Depends(get_current_user),
):
    start, end = range_bounds(range, from_, to)
    query: Dict[str, Any] = {}
    apply_range_query(query, start, end, field="entry_time")
    title_suffix = "All Vehicles"
    if vehicle_number:
        vn = normalize_plate(vehicle_number)
        query["vehicle_number"] = vn
        title_suffix = vn
    # No silent truncation — stream every matching row from the cursor.
    rows: list = []
    async for d in db.visit_sessions.find(query).sort("entry_time", -1):
        rows.append(await serialize_session_with_master(d))
    filename = f"rdx_visits_{(vehicle_number or 'all').replace(' ', '')}_{now_utc().strftime('%Y%m%d_%H%M%S')}"
    if format == "csv":
        return StreamingResponse(io.BytesIO(_rows_to_csv(rows)), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename={filename}.csv"})
    if format == "xlsx":
        return StreamingResponse(io.BytesIO(_rows_to_xlsx(rows, "Visit History")),
                                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"})
    if format == "pdf":
        return StreamingResponse(io.BytesIO(_rows_to_pdf(rows, f"RDX Visit History — {title_suffix}")),
                                 media_type="application/pdf",
                                 headers={"Content-Disposition": f"attachment; filename={filename}.pdf"})
    raise HTTPException(status_code=400, detail="Invalid format")


# ===================== Users =====================
@api.get("/users")
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


@api.post("/users")
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


@api.post("/users/{uid}/password")
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


@api.get("/audit-log")
async def list_audit_log(limit: int = 200, action: Optional[str] = None,
                         user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    q = {"action": action} if action else {}
    docs = await db.audit_log.find(q, {"_id": 0}).sort("at", -1).to_list(min(max(limit, 1), 1000))
    for d in docs:
        if isinstance(d.get("at"), datetime):
            d["at"] = d["at"].replace(tzinfo=timezone.utc).isoformat()
    return docs


@api.post("/auth/change-password")
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


@api.delete("/users/{uid}")
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


@api.get("/settings")
async def get_settings(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    return _public_settings(await db.settings.find_one({"key": "app"}))


@api.put("/settings")
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


@api.post("/maintenance/purge-expired")
async def purge_expired(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Delete finished visit sessions older than RETENTION_DAYS (and their snapshots)."""
    res = await purge_expired_sessions()
    await security.audit(db, user, "maintenance.purge_expired", "", **res)
    return res


# ===================== AI Number Plate Recognition =====================
import re as _re

_PLATE_RE = _re.compile(r"[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{3,4}")


class PlateScanIn(BaseModel):
    image_base64: str  # data URL or plain base64
    auto_action: Optional[Literal["none", "entry", "exit"]] = "none"
    camera: Optional[str] = None


def _strip_data_url(b64: str) -> str:
    if "," in b64 and b64.strip().startswith("data:"):
        return b64.split(",", 1)[1]
    return b64


def _extract_plate(text: str) -> Optional[str]:
    if not text:
        return None
    txt = text.upper().replace("-", "").replace(".", " ")
    m = _PLATE_RE.search(txt)
    if m:
        return "".join(m.group(0).split())
    # Fallback: pick first token of >=6 alphanumerics
    for line in txt.splitlines():
        cleaned = "".join(c for c in line if c.isalnum())
        if 6 <= len(cleaned) <= 12 and any(c.isdigit() for c in cleaned) and any(c.isalpha() for c in cleaned):
            return cleaned
    return None


@api.post("/vehicles/scan-plate")
async def scan_plate(payload: PlateScanIn, user: dict = Depends(require_roles(*ROLE_ALL))):
    # Free offline readers on this server (see local_ocr.py).
    b64 = _strip_data_url(payload.image_base64).strip()
    if not b64:
        raise HTTPException(status_code=400, detail="image_base64 is required")

    try:
        try:
            local = await asyncio.to_thread(local_ocr.read_base64, b64)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        engine_note = local["engine"]
        if not local["plate"]:
            return {"plate": None, "confidence": "low", "raw": "", "action": None, "engine": engine_note,
                    "reason": "Found a plate but couldn't read a valid Indian number" if local["plate_found"]
                              else "No number plate found in the photo — try a closer, straighter shot"}
        raw = local["plate"]

        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN" or not cleaned:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None,
                    "reason": "No plate visible in the photo"}

        # Only accept text that is (or can be corrected into) a valid Indian plate.
        plate, corrected = plate_rules.extract(cleaned)
        if not plate:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None,
                    "reason": f"Read “{cleaned[:24]}”, which isn't a valid Indian number plate"}

        # Optional auto entry/exit
        action_result: Optional[Dict[str, Any]] = None
        action = payload.auto_action or "none"
        cam = payload.camera or "AI-CAM"
        if action == "entry":
            vn_entry = normalize_plate(plate)
            async with _persist_lock:
                # ACTIVE SESSION WINS — never insert a second active session.
                active = await db.visit_sessions.find_one({"vehicle_number": vn_entry, "status": "active"})
                if active:
                    action_result = {"type": "entry", "duplicate": True,
                                     "error": "Vehicle already has an active session",
                                     "session_id": active.get("id")}
                else:
                    await upsert_master(vehicle_number=plate)
                    now = now_utc()
                    session = {
                        "id": str(uuid.uuid4()),
                        "vehicle_number": vn_entry,
                        "entry_time": now.isoformat(),
                        "exit_time": None,
                        "visit_date": local_date(now),
                        "duration_seconds": None,
                        "entry_camera": cam,
                        "exit_camera": "",
                        "entry_image": "",
                        "exit_image": "",
                        "status": "active",
                        "created_at": now.isoformat(),
                    }
                    await db.visit_sessions.insert_one(session)
                    session.pop("_id", None)
                    action_result = {"type": "entry", "session": serialize_session(session)}
        elif action == "exit":
            vn = normalize_plate(plate)
            sess = await db.visit_sessions.find_one({"vehicle_number": vn, "status": "active"}, sort=[("entry_time", -1)])
            if sess:
                now = now_utc()
                entry_dt = datetime.fromisoformat(sess["entry_time"])
                duration = int((now - entry_dt).total_seconds())
                updates = {
                    "exit_time": now.isoformat(),
                    "duration_seconds": duration,
                    "exit_camera": cam,
                    "status": "completed",
                }
                await db.visit_sessions.update_one({"_id": sess["_id"]}, {"$set": updates})
                sess.update(updates)
                sess.pop("_id", None)
                action_result = {"type": "exit", "session": serialize_session(sess)}
            else:
                action_result = {"type": "exit", "error": "No active session to close for this plate"}

        # A single AI read of one photo is never "high" — corrected reads are flagged.
        # One photo is never "high". Readers disagreeing or characters corrected → "low".
        conf_label = "low" if (corrected or "≠" in engine_note) else "medium"
        return {"plate": plate, "confidence": conf_label, "corrected": corrected,
                "raw": raw, "action": action_result, "engine": engine_note}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Plate scan failed")
        raise HTTPException(status_code=500, detail=f"AI scan failed: {e}")


# ===================== WhatsApp Reports =====================
class WhatsAppSendIn(BaseModel):
    to: Optional[str] = None  # override recipient; default from settings/env
    body: Optional[str] = None  # override body


async def _build_daily_summary_text() -> str:
    n = now_utc()
    today = start_of_day(n)
    end = today + timedelta(days=1)
    entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}})
    exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}})
    inside = await db.visit_sessions.count_documents({"status": "active"})
    total_masters = await db.vehicle_masters.count_documents({})

    # Top 3 busiest vehicles today
    pipe = [
        {"$match": {"entry_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}}},
        {"$group": {"_id": "$vehicle_number", "visits": {"$sum": 1}}},
        {"$sort": {"visits": -1}},
        {"$limit": 3},
    ]
    top = await db.visit_sessions.aggregate(pipe).to_list(3)
    top_lines = "\n".join(f"  • {t['_id']}: {t['visits']} visits" for t in top) or "  (none)"

    return (
        f"🚗 *RDX Car Showroom — Daily Report*\n"
        f"📅 {today.strftime('%A, %d %b %Y')}\n\n"
        f"• Entries today:  *{entries}*\n"
        f"• Exits today:    *{exits}*\n"
        f"• Cars inside:    *{inside}*\n"
        f"• Total vehicles: *{total_masters}*\n\n"
        f"Top visitors today:\n{top_lines}\n\n"
        f"— sent by RDX AI"
    )


async def _twilio_config() -> Dict[str, str]:
    """Load Twilio config from settings collection, falling back to env vars."""
    doc = await db.settings.find_one({"key": "app"}) or {}
    return {
        "sid": doc.get("twilio_sid") or TWILIO_SID,
        "token": doc.get("twilio_token") or TWILIO_TOKEN,
        "from": doc.get("twilio_from") or TWILIO_FROM,
        "to": doc.get("twilio_to") or TWILIO_TO_DEFAULT,
    }


@api.post("/whatsapp/send-report")
async def send_whatsapp_report(payload: WhatsAppSendIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    cfg = await _twilio_config()
    body = payload.body or await _build_daily_summary_text()
    to = payload.to or cfg["to"]

    # Primary provider: Meta WhatsApp Cloud API (active when META_* env is configured)
    if wa_meta.meta_whatsapp_enabled():
        if not to:
            raise HTTPException(status_code=400, detail="Recipient number not configured (Settings → Admin WhatsApp To).")
        try:
            result = await wa_meta.send_text(db, to=to, body=body, by=user["email"], context="manual_report")
            return {"delivered": True, "mode": "live", "provider": "meta", "sid": result.get("message_id"), "preview": {"to": to, "body": body}}
        except wa_meta.WhatsAppMetaError as e:
            raise HTTPException(status_code=502, detail=str(e))

    if not (HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and to):
        # Dry-run mode — return preview so the UI can display it
        return {
            "delivered": False,
            "mode": "dry-run",
            "provider": "none",
            "reason": "WhatsApp credentials not configured (set META_ACCESS_TOKEN + META_PHONE_NUMBER_ID in backend/.env, or the legacy Twilio vars, plus the recipient number).",
            "preview": {"to": to or "(missing)", "body": body},
        }

    try:
        cli = TwilioClient(cfg["sid"], cfg["token"])
        message = cli.messages.create(from_=cfg["from"], to=to, body=body)
        await db.whatsapp_log.insert_one({
            "sid": message.sid,
            "to": to,
            "body": body,
            "provider": "twilio",
            "status": "sent",
            "direction": "outbound",
            "sent_at": now_utc().isoformat(),
            "by": user["email"],
        })
        return {"delivered": True, "mode": "live", "provider": "twilio", "sid": message.sid, "preview": {"to": to, "body": body}}
    except Exception as e:
        await db.whatsapp_log.insert_one({
            "to": to,
            "body": body,
            "provider": "twilio",
            "status": "failed",
            "direction": "outbound",
            "error": str(e)[:300],
            "sent_at": now_utc().isoformat(),
            "by": user["email"],
        })
        logger.exception("Twilio send failed")
        raise HTTPException(status_code=500, detail=f"WhatsApp send failed: {e}")


@api.get("/whatsapp/preview")
async def whatsapp_preview(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    cfg = await _twilio_config()
    body = await _build_daily_summary_text()
    meta_on = wa_meta.meta_whatsapp_enabled()
    twilio_on = bool(HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and cfg["to"])
    return {
        "configured": meta_on or twilio_on,
        "provider": "meta" if meta_on else ("twilio" if twilio_on else "none"),
        "to": cfg["to"] or "",
        "from_": cfg["from"] or "",
        "body": body,
    }


# ---------- Meta WhatsApp webhooks (verification + inbound) ----------
@api.get("/whatsapp/webhook")
async def whatsapp_webhook_verify(request: Request):
    q = request.query_params
    challenge = wa_meta.verify_webhook_challenge(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return Response(content=challenge, media_type="text/plain")


@api.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request):
    raw = await request.body()
    if not wa_meta.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = await request.json()  # body is cached; safe after signature check
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return {"ok": True, "ignored": True}
    stored = 0
    for entry in payload.get("entry", []) or []:
        if wa_meta.META_WABA_ID and entry.get("id") != wa_meta.META_WABA_ID:
            continue
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            # Inbound messages — upsert on message_id so Meta delivery retries are idempotent
            for message in value.get("messages", []) or []:
                doc = {
                    "direction": "inbound",
                    "provider": "meta",
                    "status": "received",
                    "message_id": message.get("id"),
                    "from": message.get("from"),
                    "type": message.get("type"),
                    "text": (message.get("text") or {}).get("body"),
                    "created_at": now_utc().isoformat(),
                }
                if doc["message_id"]:
                    await db.whatsapp_log.update_one(
                        {"direction": "inbound", "message_id": doc["message_id"]},
                        {"$setOnInsert": doc}, upsert=True)
                else:
                    await db.whatsapp_log.insert_one(doc)
                stored += 1
            # Delivery/read status events for our outbound messages
            for st in value.get("statuses", []) or []:
                await db.whatsapp_log.update_one(
                    {"direction": "status", "message_id": st.get("id"), "status": st.get("status")},
                    {"$setOnInsert": {
                        "direction": "status", "provider": "meta",
                        "message_id": st.get("id"), "status": st.get("status"),
                        "recipient_id": st.get("recipient_id"),
                        "created_at": now_utc().isoformat(),
                    }},
                    upsert=True)
    return {"ok": True, "stored": stored}


# ===================== Health =====================
# ---------- Cameras (USB / IP Webcam / RTSP) ----------
class CameraConnectIn(BaseModel):
    camera_id: str
    source: str  # int-as-string for USB, or http/rtsp URL
    label: Optional[str] = ""


@api.get("/cameras")
async def list_cameras(user: dict = Depends(require_roles(*ROLE_ALL))):
    # Hide fully-removed / stopped cameras from the UI so the card disappears
    # when an admin disconnects an obsolete direct-URL camera.
    all_cams = camera_manager.list()
    return {cid: st for cid, st in all_cams.items() if st.get("status") != "stopped"}


# ---------- Camera roles (entry / exit / both) ----------
class CameraConfigIn(BaseModel):
    role: Literal["entry", "exit", "both"]
    name: Optional[str] = ""
    gate: Optional[str] = ""
    enabled: bool = True


@api.get("/camera-config")
async def list_camera_config(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Configured cameras plus any live/recent camera that has no role yet."""
    configured = {d["camera_id"]: d async for d in db.camera_config.find({}, {"_id": 0})}
    seen = set(camera_manager.list().keys())
    async for a in db.agents.find({}, {"cameras": 1}):
        seen.update(c.strip().upper() for c in a.get("cameras", []) if c)
    out = list(configured.values())
    for cid in sorted(seen - set(configured)):
        out.append({"camera_id": cid, "role": "both", "name": "", "gate": "", "enabled": True, "configured": False})
    for d in out:
        d.setdefault("configured", True)
    return sorted(out, key=lambda d: d["camera_id"])


@api.put("/camera-config/{camera_id}")
async def put_camera_config(camera_id: str, payload: CameraConfigIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    cid = camera_id.strip().upper()
    if not cid:
        raise HTTPException(status_code=400, detail="camera_id required")
    doc = {"camera_id": cid, **payload.model_dump(), "updated_at": now_utc().isoformat()}
    await db.camera_config.update_one({"camera_id": cid}, {"$set": doc}, upsert=True)
    await security.audit(db, user, "camera.config", cid, role=payload.role, enabled=payload.enabled)
    doc.pop("_id", None)
    return {**doc, "configured": True}


@api.delete("/camera-config/{camera_id}")
async def delete_camera_config(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    r = await db.camera_config.delete_one({"camera_id": camera_id.strip().upper()})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Camera not configured")
    await security.audit(db, user, "camera.config_delete", camera_id.strip().upper())
    return {"ok": True}


@api.get("/cameras/{camera_id}")
async def get_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    state = camera_manager.get(camera_id)
    if not state:
        raise HTTPException(status_code=404, detail="Camera not found")
    return state.public()


@api.post("/cameras/connect")
async def connect_camera(payload: CameraConnectIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    # Refuse to overlay a direct-URL capture on top of an agent-fed camera.
    # The local agent is the authoritative source for its camera IDs — otherwise
    # the cloud would try to reach the phone's private LAN IP (e.g. 192.168.x.x).
    try:
        info = camera_manager.connect(payload.camera_id, payload.source, payload.label or "")
        return info
    except CameraSourceConflict as ce:
        raise HTTPException(status_code=409, detail=str(ce))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Camera connect failed: {e}")


@api.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Fully remove a camera from the registry (stops capture thread if any).
    Use this to clean up stale duplicates. Agent-fed cameras that are still
    online will be re-registered on the next frame from the local agent."""
    ok = camera_manager.remove(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"ok": True, "camera_id": camera_id.strip().upper()}


@api.post("/cameras/{camera_id}/disconnect")
async def disconnect_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Disconnect a camera and remove it from the registry.

    - Direct-URL cameras: capture thread is stopped, entry removed.
    - Agent-fed cameras: entry removed; the local agent will re-register the
      camera automatically on the next frame if it's still streaming — so
      only *stale* agent-fed cameras stay disconnected.
    """
    if not camera_manager.remove(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    logger.info("[CAMERA] disconnected + removed camera_id=%s by user=%s", camera_id.strip().upper(), user.get("email"))
    return {"ok": True, "camera_id": camera_id.strip().upper()}


def _authorize_ws(token: Optional[str]) -> Optional[dict]:
    """Verify a token passed via ?token=... query string on the WebSocket."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ---------- Local Agent (outbound WSS from private-network showroom PCs) ----------
class AgentRegisterIn(BaseModel):
    agent_id: str
    hostname: Optional[str] = ""
    platform: Optional[str] = "windows"
    version: Optional[str] = "1.0.0"


VASHU_AGENT_SECRET = os.environ.get("VASHU_AGENT_SECRET", "")


# Largest JPEG accepted from an agent (live frames and detection crops).
MAX_AGENT_JPEG_BYTES = int(os.environ.get("MAX_AGENT_JPEG_BYTES", str(2 * 1024 * 1024)))
_warned_shared_secret: set = set()


async def _authorize_agent(agent_id: Optional[str], secret: Optional[str]) -> bool:
    """An agent must be registered (POST /api/agent/register) and present its own secret.

    Agents registered before per-agent secrets existed (no secret_hash yet) may
    still use the shared VASHU_AGENT_SECRET until an admin rotates their secret.
    Unknown agent IDs are always refused.
    """
    if not agent_id or not secret:
        return False
    doc = await db.agents.find_one({"agent_id": agent_id}, {"secret_hash": 1})
    if not doc:
        return False
    if doc.get("secret_hash"):
        return security.secret_matches(secret, doc["secret_hash"])
    if security.shared_secret_matches(secret, VASHU_AGENT_SECRET):
        if agent_id not in _warned_shared_secret:
            _warned_shared_secret.add(agent_id)
            logger.warning("[agent %s] authenticated with the shared VASHU_AGENT_SECRET — give it its own secret "
                           "(Settings → Camera agents → Rotate secret)", agent_id)
        return True
    return False


def _public_agent(d: dict) -> dict:
    return {
        "agent_id": d.get("agent_id"),
        "hostname": d.get("hostname", ""),
        "platform": d.get("platform", ""),
        "version": d.get("version", ""),
        "status": d.get("status", "offline"),
        "last_heartbeat": d.get("last_heartbeat"),
        "connected_at": d.get("connected_at"),
        "cameras": d.get("cameras", []),
        "has_own_secret": bool(d.get("secret_hash")),
        "health": d.get("health") or {},
    }


@api.get("/agent/status")
async def agent_status(user: dict = Depends(require_roles(*ROLE_ALL))):
    docs = await db.agents.find({}).to_list(200)
    return [_public_agent(d) for d in docs]


_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")


@api.post("/agent/register")
async def agent_register_http(payload: AgentRegisterIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Register a new camera agent. The secret is returned ONCE — put it in the agent's .env."""
    agent_id = payload.agent_id.strip()
    if not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=400, detail="Agent ID: 2–64 letters, digits, '-', '_' or '.'")
    if await db.agents.find_one({"agent_id": agent_id}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An agent with this ID already exists — rotate its secret instead")
    secret = security.generate_agent_secret()
    await db.agents.insert_one({
        "agent_id": agent_id, "hostname": payload.hostname, "platform": payload.platform,
        "version": payload.version, "created_at": now_utc().isoformat(), "status": "offline",
        "secret_hash": security.hash_secret(secret),
    })
    await security.audit(db, user, "agent.register", agent_id)
    return {"ok": True, "agent_id": agent_id, "secret": secret}


@api.post("/agent/{agent_id}/rotate-secret")
async def agent_rotate_secret(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Issue a new secret for an agent (the old one stops working immediately)."""
    secret = security.generate_agent_secret()
    r = await db.agents.update_one({"agent_id": agent_id}, {"$set": {"secret_hash": security.hash_secret(secret),
                                                                    "secret_rotated_at": now_utc().isoformat()}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    await security.audit(db, user, "agent.rotate_secret", agent_id)
    return {"ok": True, "agent_id": agent_id, "secret": secret}


@api.delete("/agent/{agent_id}")
async def agent_delete(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    doc = await db.agents.find_one_and_delete({"agent_id": agent_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Agent not found")
    for cam_id in doc.get("cameras", []):
        camera_manager.mark_agent_offline(cam_id, "agent removed")
    await security.audit(db, user, "agent.delete", agent_id)
    return {"ok": True}


@api.post("/agent/{agent_id}/disconnect")
async def agent_disconnect_http(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    await db.agents.update_one({"agent_id": agent_id}, {"$set": {"status": "offline"}})
    doc = await db.agents.find_one({"agent_id": agent_id}) or {}
    for cam_id in doc.get("cameras", []):
        camera_manager.mark_agent_offline(cam_id, "agent disconnect requested")
    return {"ok": True}


AGENT_EVENT_STALE_SECONDS = 120


async def _claim_agent_event(event_id: str) -> Optional[dict]:
    """Claim an agent event for processing.

    Returns None when this call owns it (go ahead), the stored ack when it was
    already processed, or {} when another connection is processing it right now.
    """
    now = datetime.now(timezone.utc)
    try:
        await db.processed_events.insert_one({"event_id": event_id, "status": "processing", "at": now})
        return None
    except DuplicateKeyError:
        prev = await db.processed_events.find_one({"event_id": event_id}) or {}
        if prev.get("status") == "done":
            return prev.get("ack") or {}
        started = prev.get("at")
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or (now - started).total_seconds() > AGENT_EVENT_STALE_SECONDS:
            # A previous attempt died mid-way — take it over.
            await db.processed_events.update_one({"event_id": event_id}, {"$set": {"status": "processing", "at": now}})
            return None
        return {}


def _agent_seen_at(ts, agent_id: str = "") -> Optional[datetime]:
    """The agent's 'seen at' time, if plausible (within the last 7 days, not in the future)."""
    try:
        t = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    now = datetime.now(timezone.utc)
    if t > now + timedelta(seconds=120) or t < now - timedelta(days=7):
        logger.warning("[agent %s] ignoring implausible detection time %s (check the PC clock)", agent_id, t.isoformat())
        return None
    return min(t, now)


@app.websocket("/api/agent/ws")
async def ws_agent(websocket: WebSocket, agent_id: Optional[str] = Query(default=None), secret: Optional[str] = Query(default=None)):
    """Outbound WSS endpoint for the Windows Local Camera Agent.

    Accepts two frame delivery modes for Cloudflare-compatibility:
      1. Legacy JSON text frame:  {"type":"frame","camera_id":..,"jpeg_b64":".."}
      2. Binary frame (preferred): [4-byte BE header-len][utf-8 JSON header][raw JPEG]
         header must include {"type":"frame","camera_id":..,"name":..,"seq":..,"ts":..}
    """
    await websocket.accept()
    # Prefer headers (secrets in URLs end up in proxy logs); query params kept for older agents.
    agent_id = (websocket.headers.get("x-agent-id") or agent_id or "").strip()
    secret = websocket.headers.get("x-agent-secret") or secret or ""
    if not await _authorize_agent(agent_id, secret):
        logger.warning("[agent %s] refused: unknown agent or bad secret", agent_id or "?")
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    active_agent_id: str = agent_id
    cameras_registered: set = set()
    await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
        "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
    }})

    await websocket.send_json({"type": "hello", "ok": True})
    logger.info("[agent %s] connected", active_agent_id or "?")

    import base64 as _b64
    import struct as _struct

    async def _handle_frame(cam_id: str, jpeg: bytes, name: str) -> None:
        if not cam_id or not jpeg:
            return
        if len(jpeg) > MAX_AGENT_JPEG_BYTES:
            logger.warning("[agent %s] dropped oversized frame (%d bytes) camera=%s", active_agent_id, len(jpeg), cam_id)
            return
        cam_id_norm = cam_id.strip().upper()
        if cam_id_norm not in cameras_registered:
            camera_manager.register_agent_camera(cam_id_norm, label=name or cam_id_norm, agent_id=active_agent_id or "")
            cameras_registered.add(cam_id_norm)
            logger.info("[CAMERA] agent camera registered camera_id=%s agent_id=%s", cam_id_norm, active_agent_id or "?")
        camera_manager.push_frame(cam_id_norm, jpeg)
        camera_manager.push_frame(cam_id, jpeg)

    try:
        while True:
            event = await websocket.receive()
            # WebSocket disconnected
            if event.get("type") == "websocket.disconnect":
                break

            # ---- Binary path (preferred) ----
            if "bytes" in event and event["bytes"] is not None:
                data: bytes = event["bytes"]
                if len(data) < 4:
                    continue
                try:
                    (hlen,) = _struct.unpack(">I", data[:4])
                    if hlen <= 0 or hlen > len(data) - 4 or hlen > 8192:
                        continue
                    header = json.loads(data[4:4 + hlen].decode("utf-8"))
                    jpeg = data[4 + hlen:]
                except Exception:
                    continue
                if header.get("type") != "frame":
                    continue
                await _handle_frame(header.get("camera_id", ""), jpeg, header.get("name", ""))
                continue

            # ---- Text path (control + legacy frames) ----
            raw = event.get("text")
            if raw is None:
                continue
            try:
                msg = json.loads(raw) if raw else {}
            except Exception:
                await websocket.send_json({"type": "error", "message": "bad json"})
                continue
            mtype = msg.get("type")

            if mtype == "agent_register":
                claimed = msg.get("agent_id") or active_agent_id
                if claimed != active_agent_id:
                    await websocket.send_json({"type": "error", "message": "agent_id does not match the authenticated agent"})
                    continue
                await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                    "hostname": msg.get("hostname", ""),
                    "platform": msg.get("platform", "windows"), "version": msg.get("version", "1.0.0"),
                    "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
                }})
                await websocket.send_json({"type": "registered", "agent_id": active_agent_id})

            elif mtype == "camera_status":
                cam_id_raw = msg.get("camera_id")
                if not cam_id_raw:
                    continue
                cam_id = str(cam_id_raw).strip().upper()
                name = msg.get("name", cam_id)
                status = msg.get("status", "online")
                if status == "online":
                    camera_manager.register_agent_camera(cam_id, label=name, agent_id=active_agent_id or "")
                    logger.info("[CAMERA] agent camera registered camera_id=%s agent_id=%s", cam_id, active_agent_id or "?")
                else:
                    camera_manager.mark_agent_offline(cam_id, msg.get("error", "agent reported offline"))
                cameras_registered.add(cam_id)
                if active_agent_id:
                    await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                        "cameras": list(cameras_registered), "last_heartbeat": now_utc().isoformat(),
                    }})

            elif mtype == "heartbeat":
                update = {"last_heartbeat": now_utc().isoformat(), "status": "online"}
                health = msg.get("health")
                if isinstance(health, dict) and len(json.dumps(health, default=str)) <= 8192:
                    update["health"] = health
                await db.agents.update_one({"agent_id": active_agent_id}, {"$set": update})

            elif mtype == "ping":
                # App-level keep-alive from the agent — reply so the proxy sees traffic
                try:
                    await websocket.send_json({"type": "pong", "ts": msg.get("ts")})
                except Exception:
                    pass

            elif mtype == "frame":
                # Legacy base64 JSON path (backward compat)
                cam_id = msg.get("camera_id")
                b64 = msg.get("jpeg_b64", "")
                if not cam_id or not b64:
                    continue
                try:
                    jpeg = _b64.b64decode(b64)
                except Exception:
                    continue
                await _handle_frame(cam_id, jpeg, msg.get("name", ""))

            elif mtype == "plate_detected":
                # Local agent ran YOLO+OCR at the edge. Validate the plate and
                # either save it or put it in the Review queue for a person.
                cam_id = str(msg.get("camera_id", "")).strip().upper()
                raw_plate = (msg.get("plate") or "").upper().strip()  # "" = vehicle without a readable plate
                if not cam_id:
                    if msg.get("event_id"):  # answer, or the agent would re-send it forever
                        await websocket.send_json({"type": "plate_ack", "event_id": msg["event_id"],
                                                   "error": "camera_id missing"})
                    continue
                def _b64_or_empty(v):
                    try:
                        data = _b64.b64decode(v) if v else b""
                    except Exception:
                        return b""
                    return data if len(data) <= MAX_AGENT_JPEG_BYTES else b""
                event_id = str(msg.get("event_id") or "")[:64]
                if event_id:
                    prior = await _claim_agent_event(event_id)
                    if prior is not None:
                        if prior:  # already processed — the agent missed our ack; answer again
                            await websocket.send_json(prior)
                        continue
                try:
                    persistence = await route_detection(
                        db,
                        camera_id=cam_id,
                        raw_plate=raw_plate,
                        confidence=str(msg.get("confidence") or "medium"),
                        jpeg_frame=_b64_or_empty(msg.get("crop_jpeg_b64")),
                        plate_jpeg=_b64_or_empty(msg.get("plate_jpeg_b64")),
                        candidates=msg.get("candidates") or [],
                        reads=int(msg.get("reads") or 0),
                        support=float(msg.get("support") or 0),
                        seen_at=_agent_seen_at(msg.get("ts"), active_agent_id),
                    )
                    ack = {
                        "type": "plate_ack",
                        "event_id": event_id or None,
                        "plate": persistence.get("plate", raw_plate),
                        "queued_for_review": persistence.get("queued_for_review", False),
                        "review_id": persistence.get("review_id"),
                        "duplicate": persistence.get("duplicate", False),
                        "event": persistence.get("event"),
                        "owner_status": persistence.get("owner_status"),
                        "session_id": (persistence.get("session") or {}).get("id"),
                    }
                    if event_id:
                        await db.processed_events.update_one({"event_id": event_id},
                                                             {"$set": {"status": "done", "ack": ack}})
                    await websocket.send_json(ack)
                except Exception as e:
                    logger.exception("[agent] persistence failed for plate=%s", raw_plate)
                    if event_id:
                        await db.processed_events.delete_one({"event_id": event_id, "status": "processing"})
                        await websocket.send_json({"type": "plate_nack", "event_id": event_id,
                                                   "message": type(e).__name__})

            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.info("[agent %s] ws error: %s", active_agent_id or "?", e)
    finally:
        if active_agent_id:
            await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {"status": "offline"}})
        for cam_id in cameras_registered:
            camera_manager.mark_agent_offline(cam_id, "agent disconnected")
        logger.info("[agent %s] disconnected", active_agent_id or "?")



@app.websocket("/api/ws/events")
async def ws_events(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    """Broadcast channel for the live Dashboard — receives entry.recorded events
    published by the auto-detection pipeline."""
    await websocket.accept()
    cookie_token = websocket.cookies.get("access_token")
    payload = _authorize_ws(cookie_token) or _authorize_ws(token)
    if payload is None:
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    q = event_bus.subscribe()
    try:
        await websocket.send_json({"type": "hello", "user": payload.get("email")})
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.info("[ws events] disconnect: %s", e)
    finally:
        event_bus.unsubscribe(q)


@app.websocket("/api/ws/camera/{camera_id}")
async def ws_camera(websocket: WebSocket, camera_id: str, token: Optional[str] = Query(default=None)):
    # Accept then authenticate via cookie or ?token=
    await websocket.accept()

    # Try cookie first
    cookie_token = websocket.cookies.get("access_token")
    payload = _authorize_ws(cookie_token) or _authorize_ws(token)
    if payload is None:
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    if camera_manager.get(camera_id) is None:
        await websocket.send_json({
            "type": "error",
            "message": (
                f"camera '{camera_id}' not connected. "
                "If this is an agent-fed camera, make sure the Local Agent is running and streaming."
            ),
        })
        await websocket.close(code=4404)
        return

    logger.info("[STREAM] browser subscribed camera_id=%s user=%s", camera_id.strip().upper(), payload.get("email"))

    # Push initial status
    state = camera_manager.get(camera_id)
    if state:
        try:
            await websocket.send_json({"type": "status", "camera": state.public()})
        except Exception:
            return

    connected = {"v": True}

    def is_connected() -> bool:
        return connected["v"]

    async def send_bytes(data: bytes) -> None:
        try:
            await websocket.send_bytes(data)
        except Exception:
            connected["v"] = False

    # Status heartbeat every 2s + broadcast detected plates
    async def heartbeat():
        last_plate_sig = None
        while connected["v"]:
            await asyncio.sleep(2)
            st = camera_manager.get(camera_id)
            if st is None:
                break
            try:
                await websocket.send_json({"type": "status", "camera": st.public()})
            except Exception:
                connected["v"] = False
                break
            # Broadcast new plate detections through the same WebSocket
            plate = getattr(st, "latest_plate", None)
            if plate:
                sig = (plate.get("plate"), plate.get("detected_at"))
                if sig != last_plate_sig:
                    last_plate_sig = sig
                    try:
                        await websocket.send_json({"type": "plate", **plate})
                    except Exception:
                        connected["v"] = False
                        break

    hb_task = asyncio.create_task(heartbeat())
    try:
        await camera_manager.stream(camera_id, send_bytes, is_connected, fps=15)
    except WebSocketDisconnect:
        pass
    finally:
        connected["v"] = False
        hb_task.cancel()
        logger.info("[ws camera %s] client disconnected", camera_id)


# ===================== Labelled data (for measuring and retraining OCR) =====================
async def _save_ocr_label(review: dict, final_plate: Optional[str], user: dict) -> None:
    """Every human decision on a review is a labelled example: the plate crop + the true text
    (or None when the reviewer said it isn't a plate). Corrections are the most valuable ones."""
    try:
        await db.ocr_labels.insert_one({
            "id": str(uuid.uuid4()),
            "review_id": review.get("id"),
            "kind": review.get("kind", "plate"),
            "plate": final_plate,
            "model_plate": review.get("plate"),
            "raw": review.get("raw", ""),
            "candidates": review.get("candidates", []),
            "corrected": bool(final_plate) and final_plate != review.get("plate"),
            "camera_id": review.get("camera_id", ""),
            "plate_image": review.get("plate_image", ""),
            "image": review.get("image", ""),
            "seen_at": review.get("seen_at"),
            "labelled_by": user.get("email"),
            "at": now_utc().isoformat(),
        })
    except Exception:
        logger.exception("[labels] could not save label for review %s", review.get("id"))


async def _snapshot_bytes(url: str) -> Optional[bytes]:
    """Bytes of a /api/snapshots/<file> image from local disk or object storage."""
    if not url or not url.startswith("/api/snapshots/"):
        return None
    try:
        return await plate_pipeline.read_snapshot(os.path.basename(url))
    except Exception:
        return None


@api.get("/ocr-labels/stats")
async def ocr_label_stats(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    total = await db.ocr_labels.count_documents({})
    corrected = await db.ocr_labels.count_documents({"corrected": True})
    rejected = await db.ocr_labels.count_documents({"plate": None})
    return {"total": total, "corrected": corrected, "rejected": rejected, "confirmed": total - corrected - rejected}


@api.get("/ocr-labels/export")
async def export_ocr_labels(only_corrected: bool = False, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """ZIP of plate crops + labels.csv, for benchmark_ocr.py and model fine-tuning."""
    import zipfile
    q: Dict[str, Any] = {"plate_image": {"$ne": ""}}
    if only_corrected:
        q["corrected"] = True
    buf = io.BytesIO()
    rows = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["file", "plate", "model_plate", "raw", "corrected", "kind", "camera_id", "seen_at"])
        async for d in db.ocr_labels.find(q, {"_id": 0}).sort("at", 1):
            data = await _snapshot_bytes(d.get("plate_image", ""))
            if not data:
                continue
            truth = d.get("plate") or "NOTAPLATE"
            fname = f"images/{truth}_{d['id'][:8]}.jpg"  # benchmark_ocr.py reads the plate from the file name
            z.writestr(fname, data)
            w.writerow([fname, d.get("plate") or "", d.get("model_plate") or "", d.get("raw", ""),
                        int(bool(d.get("corrected"))), d.get("kind", "plate"), d.get("camera_id", ""), d.get("seen_at", "")])
            rows += 1
        z.writestr("labels.csv", out.getvalue())
    await security.audit(db, user, "ocr_labels.export", "", rows=rows, only_corrected=only_corrected)
    stamp = now_utc().strftime("%Y%m%d")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="plate-labels-{stamp}.zip"'})


# ===================== Known plates (demo, test-drive, trade, staff) =====================
class KnownPlateIn(BaseModel):
    plate: str
    label: Optional[str] = ""
    kind: Literal["demo", "test_drive", "trade", "staff", "other"] = "other"


@api.get("/known-plates")
async def list_known_plates(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    return await db.known_plates.find({}, {"_id": 0}).sort("plate", 1).to_list(2000)


@api.post("/known-plates")
async def add_known_plate(payload: KnownPlateIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Vehicles the showroom owns or expects. Exact reads of these are trusted more; trade /
    temporary plates listed here are accepted even though they don't follow the standard format."""
    fixed, _ = plate_rules.correct(payload.plate)
    plate = fixed or plate_rules.clean(payload.plate)
    if not 4 <= len(plate) <= 12:
        raise HTTPException(status_code=400, detail="Enter the plate as written (4–12 letters/digits)")
    doc = {"plate": plate, "label": payload.label or "", "kind": payload.kind, "standard": bool(fixed),
           "created_at": now_utc().isoformat(), "created_by": user.get("email")}
    try:
        await db.known_plates.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="That plate is already on the list")
    await security.audit(db, user, "known_plate.add", plate, kind=payload.kind)
    doc.pop("_id", None)
    return doc


@api.delete("/known-plates/{plate}")
async def delete_known_plate(plate: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    p = plate_rules.clean(plate)
    r = await db.known_plates.delete_one({"plate": p})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not on the list")
    await security.audit(db, user, "known_plate.delete", p)
    return {"ok": True}


# ===================== Plate review queue =====================
class ReviewAcceptIn(BaseModel):
    plate: str
    # Trade / temporary / foreign plates that don't follow the Indian format.
    nonstandard: bool = False


def _review_public(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


@api.get("/plate-reviews")
async def list_plate_reviews(status: str = "pending", limit: int = 100,
                             user: dict = Depends(require_roles(*ROLE_ALL))):
    q = {} if status == "all" else {"status": status}
    docs = await db.plate_reviews.find(q).sort("created_at", -1).to_list(min(max(limit, 1), 500))
    return [_review_public(d) for d in docs]


@api.get("/plate-reviews/count")
async def count_plate_reviews(user: dict = Depends(require_roles(*ROLE_ALL))):
    return {"pending": await db.plate_reviews.count_documents({"status": "pending"})}


@api.post("/plate-reviews/{rid}/accept")
async def accept_plate_review(rid: str, payload: ReviewAcceptIn, user: dict = Depends(require_roles(*ROLE_ALL))):
    review = await db.plate_reviews.find_one({"id": rid})
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Already {review.get('status')}")
    if payload.nonstandard:
        plate = plate_rules.clean(payload.plate)
        if not 4 <= len(plate) <= 12:
            raise HTTPException(status_code=400, detail="Enter the plate as written (4–12 letters/digits)")
    else:
        plate, _ = plate_rules.correct(payload.plate)
        if not plate:
            raise HTTPException(status_code=400, detail="That isn't a valid Indian number plate (e.g. KA05MG9999 or 22BH1234AA). "
                                                        "For trade or temporary plates tick 'Non-standard plate'.")
    # Claim the review first so two people can't accept it twice.
    claimed = await db.plate_reviews.update_one({"id": rid, "status": "pending"}, {"$set": {"status": "processing"}})
    if claimed.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else is handling this review")
    seen_at = datetime.fromisoformat(review["seen_at"]) if review.get("seen_at") else now_utc()
    try:
        result = await _persist_entry(db, review.get("camera_id", ""), plate, b"", plate_jpeg=b"",
                                      confidence="confirmed", at=seen_at, source="review", confirmed=True)
    except Exception:
        await db.plate_reviews.update_one({"id": rid}, {"$set": {"status": "pending"}})
        raise
    # Reuse the review's images on the visit it created / closed.
    sess = result.get("session") or {}
    if sess.get("id") and not result.get("duplicate"):
        prefix = "exit" if result.get("event") == "exit" else "entry"
        img_updates = {k: v for k, v in {f"{prefix}_image": review.get("image"),
                                         f"{prefix}_plate_image": review.get("plate_image")}.items() if v}
        if img_updates:
            await db.visit_sessions.update_one({"id": sess["id"]}, {"$set": img_updates})
    await security.audit(db, user, "review.accept", rid, plate=plate, original=review.get("plate"),
                         event=result.get("event"), duplicate=result.get("duplicate", False))
    await _save_ocr_label(review, plate, user)
    await db.plate_reviews.update_one({"id": rid}, {"$set": {
        "status": "accepted", "final_plate": plate, "result_event": result.get("event"),
        "duplicate": result.get("duplicate", False),
        "resolved_by": user.get("email"), "resolved_at": now_utc().isoformat(),
    }})
    return {"ok": True, "plate": plate, "event": result.get("event"), "duplicate": result.get("duplicate", False),
            "duplicate_reason": result.get("duplicate_reason")}


@api.post("/plate-reviews/{rid}/reject")
async def reject_plate_review(rid: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    r = await db.plate_reviews.update_one({"id": rid, "status": "pending"}, {"$set": {
        "status": "rejected", "resolved_by": user.get("email"), "resolved_at": now_utc().isoformat(),
    }})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pending review not found")
    await security.audit(db, user, "review.reject", rid)
    review = await db.plate_reviews.find_one({"id": rid}) or {}
    await _save_ocr_label(review, None, user)
    return {"ok": True}


@api.get("/health")
async def health():
    """Liveness: the process is up and serving (no dependencies checked)."""
    return {"ok": True}


@api.get("/")
async def root():
    return {"service": "RDX Car Showroom API", "ok": True}


# ---------- Auto-detection snapshots ----------
from fastapi.responses import FileResponse
from plate_pipeline import SNAPSHOTS_DIR as _SNAPSHOTS_DIR
from plate_pipeline import snapshot_disk_stats as _snapshot_disk_stats
import backup_utils


@api.get("/snapshots/{filename}")
async def get_snapshot(filename: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    safe = os.path.basename(filename)  # path-traversal guard
    fp = os.path.join(_SNAPSHOTS_DIR, safe)
    if os.path.isfile(fp):  # local disk — fast path
        return FileResponse(fp, media_type="image/jpeg")
    try:
        data = await plate_pipeline.read_snapshot(safe)
    except Exception as e:
        logger.warning("[snapshot] storage fetch failed for %s: %s", safe, e)
        raise HTTPException(status_code=502, detail="Snapshot storage unavailable")
    if data is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@api.get("/storage/snapshots")
async def get_storage_stats(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Real (not hardcoded) disk-usage stats for the snapshot volume."""
    return _snapshot_disk_stats()


# ===================== Startup =====================
async def seed_admin():
    """Create the first admin from .env on a fresh install — and only then.

    Once any admin exists the database owns the password: restarts never reset
    it, and ADMIN_PASSWORD in .env is ignored (change passwords in the app).
    """
    if await db.users.find_one({"role": "admin"}, {"_id": 1}):
        return
    problem = security.password_problem(ADMIN_PASSWORD)
    if problem:
        raise RuntimeError(f"Refusing to create the first admin: ADMIN_PASSWORD is not acceptable ({problem}).")
    if await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 1}) is None:
        await db.users.insert_one({
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "name": "Admin",
            "role": "admin",
            "status": "active",
            "created_at": now_utc().isoformat(),
            "last_login": now_utc().isoformat(),
        })
        logger.info("Seeded admin user: %s", ADMIN_EMAIL)
    else:
        logger.warning("No admin exists and %s is already a non-admin user — promote a user to admin manually.", ADMIN_EMAIL)


GOOGLE_ADMIN_EMAIL = os.environ.get("GOOGLE_ADMIN_EMAIL", "")
AGENT_BOOTSTRAP_ID = os.environ.get("AGENT_BOOTSTRAP_ID", "").strip()


async def seed_bootstrap_agent():
    """Local development: pre-register one agent that uses the shared VASHU_AGENT_SECRET."""
    if not AGENT_BOOTSTRAP_ID or not os.environ.get("VASHU_AGENT_SECRET", ""):
        return
    r = await db.agents.update_one({"agent_id": AGENT_BOOTSTRAP_ID},
                                   {"$setOnInsert": {"agent_id": AGENT_BOOTSTRAP_ID, "status": "offline",
                                                     "created_at": now_utc().isoformat()}}, upsert=True)
    if r.upserted_id:
        logger.info("Pre-registered development agent %s (shared secret)", AGENT_BOOTSTRAP_ID)


async def seed_google_admin():
    """Explicitly configured owner account that authenticates via Google only."""
    if not GOOGLE_ADMIN_EMAIL:
        return
    email = GOOGLE_ADMIN_EMAIL.strip().lower()
    if await db.users.find_one({"email": email}):
        return
    await db.users.insert_one({
        "email": email,
        # Random unknown password — this account signs in via Google only.
        "password_hash": hash_password(str(ObjectId())),
        "name": "Owner",
        "role": "admin",
        "status": "active",
        "auth_provider": "google",
        "created_at": now_utc().isoformat(),
        "last_login": None,
    })
    logger.info("Seeded Google admin user: %s", email)


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.vehicle_masters.create_index("vehicle_number", unique=True)
    await db.vehicle_masters.create_index("phone_number")
    await db.vehicle_masters.create_index("customer_id")
    await db.visit_sessions.create_index("vehicle_number")
    await db.visit_sessions.create_index("entry_time")
    await db.visit_sessions.create_index("visit_date")
    await db.visit_sessions.create_index("status")
    await db.visit_sessions.create_index([("vehicle_number", 1), ("entry_time", -1)])
    await db.plate_reviews.create_index([("status", 1), ("created_at", -1)])
    await db.plate_reviews.create_index("id", unique=True)
    await db.known_plates.create_index("plate", unique=True)
    await db.ocr_labels.create_index([("at", 1)])


@app.on_event("startup")
async def startup():
    config_check.check_or_exit()
    await ensure_indexes()
    await security.ensure_indexes(db)
    await plate_pipeline.ensure_session_indexes(db)
    await seed_admin()
    await seed_google_admin()
    await seed_bootstrap_agent()
    # Daily jobs (digest, backup, retention) — see scheduler.py.
    if SCHEDULER_ENABLED:
        app.state.scheduler = asyncio.create_task(scheduler.run_forever(db, _scheduled_jobs()))
    else:
        logger.info("[scheduler] disabled via SCHEDULER_ENABLED=false")


SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _scheduled_jobs() -> List["scheduler.Job"]:
    async def retention(run_id: str) -> None:
        res = await purge_expired_sessions()
        logger.info("[retention %s] %s", run_id, res)

    async def backup(run_id: str) -> None:
        await backup_utils.run_backup(db, mongo_url, os.environ["DB_NAME"], run_id)

    return [
        scheduler.Job("digest", os.environ.get("DIGEST_TIME", "09:00"), _run_digest_send,
                      enabled=bool(_email_provider())),
        scheduler.Job("backup", os.environ.get("BACKUP_TIME", "03:00"), backup,
                      enabled=os.environ.get("BACKUP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")),
        scheduler.Job("retention", os.environ.get("RETENTION_TIME", "03:30"), retention, enabled=RETENTION_ENABLED),
    ]


# ===================== Daily Digest Email (cron) =====================
from email_utils import send_email as _send_email, provider as _email_provider  # noqa: E402

WEBHOOK_CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "")
_digest_seen_run_ids: set[str] = set()


def _fmt_hms(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "0m"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


async def _collect_digest_stats() -> dict:
    """Compute yesterday's showroom summary in Asia/Kolkata."""
    tz = APP_TZ
    now_ist = datetime.now(tz)
    yday = (now_ist - timedelta(days=1)).date()
    day_start = datetime.combine(yday, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    lo, hi = day_start.isoformat(), day_end.isoformat()

    entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": lo, "$lt": hi}})
    exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": lo, "$lt": hi}})
    unique = len(await db.visit_sessions.distinct("vehicle_number", {"entry_time": {"$gte": lo, "$lt": hi}}))
    # Avg dwell time (completed sessions with entry yesterday)
    pipeline = [
        {"$match": {"entry_time": {"$gte": lo, "$lt": hi}, "status": "completed", "duration_seconds": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$duration_seconds"}, "max": {"$max": "$duration_seconds"}}},
    ]
    dwell = await db.visit_sessions.aggregate(pipeline).to_list(1)
    avg_dwell = dwell[0]["avg"] if dwell else 0
    max_dwell = dwell[0]["max"] if dwell else 0
    # Busiest camera by entries
    cam_pipeline = [
        {"$match": {"entry_time": {"$gte": lo, "$lt": hi}, "entry_camera": {"$ne": ""}}},
        {"$group": {"_id": "$entry_camera", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 1},
    ]
    busiest = await db.visit_sessions.aggregate(cam_pipeline).to_list(1)
    long_open = await plate_pipeline.long_open_sessions(db, MAX_VISIT_HOURS)
    exit_missed = await db.visit_sessions.count_documents({"status": "exit_missed", "exit_missed_at": {"$gte": lo, "$lt": hi}})
    entry_missed = await db.visit_sessions.count_documents({"status": "entry_missed", "exit_time": {"$gte": lo, "$lt": hi}})
    return {
        "date": yday.isoformat(),
        "entries": entries,
        "exits": exits,
        "unique_vehicles": unique,
        "avg_dwell": _fmt_hms(avg_dwell),
        "max_dwell": _fmt_hms(max_dwell),
        "busiest_camera": busiest[0]["_id"] if busiest else "—",
        "busiest_camera_count": busiest[0]["n"] if busiest else 0,
        "long_open": len(long_open),
        "long_open_plates": [x["vehicle_number"] for x in long_open[:10]],
        "missed_events": exit_missed + entry_missed,
    }


def _render_digest_html(s: dict) -> str:
    from html import escape as _esc
    date_str = _esc(s["date"])
    busiest = f"{s['busiest_camera']} ({s['busiest_camera_count']})"
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#0f172a;font-family:Arial,sans-serif;color:#e2e8f0">'
        '<tr><td align="center" style="padding:32px 12px">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#111827;border:1px solid #1f2937;border-radius:14px">'
        '<tr><td style="padding:24px 24px 8px 24px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#60a5fa;text-transform:uppercase">'
        'Vashu Hyundai &middot; Daily Digest</div>'
        f'<h1 style="margin:8px 0 4px 0;font-size:22px;color:#f8fafc">{date_str} &middot; Showroom Recap</h1>'
        '<p style="margin:0;color:#94a3b8;font-size:13px">Yesterday\'s vehicle traffic at a glance.</p>'
        '</td></tr>'
        '<tr><td style="padding:16px 24px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + _metric_cell("Entries", s["entries"]) + _metric_cell("Exits", s["exits"])
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Unique Vehicles", s["unique_vehicles"]) + _metric_cell("Avg Dwell", s["avg_dwell"])
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Longest Dwell", s["max_dwell"]) + _metric_cell("Busiest Camera", busiest)
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell(f"Open > {MAX_VISIT_HOURS:g}h", s.get("long_open", 0))
        + _metric_cell("Missed entry/exit", s.get("missed_events", 0))
        + '</table>'
        + ((f'<p style="margin:10px 6px 0;color:#fbbf24;font-size:12px">Still inside: '
            f'{_esc(", ".join(s["long_open_plates"]))}</p>') if s.get("long_open_plates") else '')
        + '</td></tr>'
        + '<tr><td style="padding:8px 24px 24px 24px;color:#64748b;font-size:11px;line-height:1.5">'
        + 'Sent by Vashu Hyundai &middot; AI Vehicle Management System. We never ask for your password or payment details by email.'
        + '</td></tr>'
        + '</table></td></tr></table>'
    )


def _metric_cell(label: str, value) -> str:
    from html import escape as _esc
    return (
        '<td width="50%" style="padding:6px" valign="top">'
        '<div style="background:#0b1220;border:1px solid #1e293b;border-radius:10px;padding:14px">'
        f'<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1.5px">{_esc(label)}</div>'
        f'<div style="font-size:22px;color:#f8fafc;font-weight:700;margin-top:4px">{_esc(str(value))}</div>'
        '</div></td>'
    )


async def _digest_recipients() -> List[str]:
    """Union of admin/manager user emails + settings.digest_extra_emails."""
    emails: set[str] = set()
    async for u in db.users.find({"role": {"$in": ["admin", "manager"]}, "status": {"$ne": "inactive"}}, {"email": 1}):
        e = (u.get("email") or "").strip().lower()
        if e:
            emails.add(e)
    doc = await db.settings.find_one({"key": "app"}) or {}
    for e in (doc.get("digest_extra_emails") or []):
        e = str(e or "").strip().lower()
        if e:
            emails.add(e)
    return sorted(emails)


async def _run_digest_send(run_id: str) -> None:
    try:
        stats = await _collect_digest_stats()
        recipients = await _digest_recipients()
        if not recipients:
            logger.info("[digest %s] no recipients — skipped", run_id)
            return
        html = _render_digest_html(stats)
        subject = f"Vashu Hyundai · Daily Digest — {stats['date']}"
        sent = 0
        for to_addr in recipients:
            try:
                await _send_email(to=to_addr, subject=subject, html=html)
                sent += 1
            except Exception as e:
                logger.warning("[digest %s] send to %s failed: %s", run_id, to_addr, e)
        logger.info("[digest %s] sent %d/%d for %s", run_id, sent, len(recipients), stats["date"])
        # WhatsApp companion — Meta Cloud API only when configured; never breaks email.
        if wa_meta.meta_whatsapp_enabled():
            try:
                cfg = await _twilio_config()  # reuses the same "Admin WhatsApp To" recipient
                wa_to = cfg["to"]
                if wa_to:
                    await wa_meta.send_text(db, to=wa_to, body=await _build_daily_summary_text(), by="cron", context="daily_digest")
                    logger.info("[digest %s] whatsapp report sent", run_id)
                else:
                    logger.info("[digest %s] whatsapp skipped — no recipient configured", run_id)
            except Exception as e:
                logger.warning("[digest %s] whatsapp send failed: %s", run_id, e)
    except Exception:
        logger.exception("[digest %s] failed", run_id)


@app.post("/api/cron/digest")
async def cron_digest(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("authorization", "")
    if not WEBHOOK_CRON_SECRET or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = auth.split(" ", 1)[1].strip()
    if not pysecrets.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        env = await request.json()
    except Exception:
        env = {}
    run_id = request.headers.get("x-webhook-id") or (env.get("run_id") if isinstance(env, dict) else "") or str(uuid.uuid4())
    # Idempotency
    if run_id in _digest_seen_run_ids:
        return {"ok": True, "duplicate": True, "run_id": run_id}
    _digest_seen_run_ids.add(run_id)
    if len(_digest_seen_run_ids) > 500:
        # avoid unbounded growth
        _digest_seen_run_ids.clear()
        _digest_seen_run_ids.add(run_id)
    asyncio.create_task(_run_digest_send(run_id))
    return {"ok": True, "queued": True, "run_id": run_id}


@app.post("/api/cron/digest/preview")
async def cron_digest_preview(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Admin-only manual trigger for testing the digest without waiting for the cron."""
    stats = await _collect_digest_stats()
    recipients = await _digest_recipients()
    return {"stats": stats, "recipients": recipients}


async def _run_backup_safe(run_id: str):
    """Wrapper: backup failures are logged + recorded, never raised — the
    running showroom app is unaffected and previous backups are kept."""
    try:
        await backup_utils.run_backup(db, mongo_url, os.environ["DB_NAME"], run_id)
    except Exception as e:
        logger.exception("[backup %s] failed: %s", run_id, e)
        try:
            await db.backups.insert_one({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "kind": "mongodb",
                "status": "failed",
                "error": str(e)[:400],
                "expired": False,
                "created_at": now_utc().isoformat(),
            })
        except Exception:
            pass


@app.post("/api/cron/backup")
async def cron_backup(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("authorization", "")
    if not WEBHOOK_CRON_SECRET or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = auth.split(" ", 1)[1].strip()
    if not pysecrets.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")
    run_id = request.headers.get("x-webhook-id") or str(uuid.uuid4())
    asyncio.create_task(_run_backup_safe(run_id))
    return {"ok": True, "queued": True, "run_id": run_id}


@api.get("/backups")
async def list_backups(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Backup history (metadata only — no secrets, no DB content)."""
    docs = await db.backups.find({"expired": False}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return {"backups": docs, "keep": backup_utils.BACKUP_KEEP}


@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "scheduler", None)
    if task:
        task.cancel()
    client.close()


# Mount router + CORS
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=security.cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)
