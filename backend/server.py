from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import io
import csv
import json
import uuid
import logging
import random
import asyncio
import secrets as pysecrets
from datetime import datetime, timezone, timedelta, date as ddate
from typing import List, Optional, Literal, Dict, Any, Tuple

import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from camera_manager import camera_manager
from plate_pipeline import start_auto_detection_loop
from event_bus import event_bus

# Optional integrations
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

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
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
TWILIO_TO_DEFAULT = os.environ.get("TWILIO_WHATSAPP_TO", "")

RETENTION_DAYS = 180

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
    response.set_cookie("access_token", access, httponly=True, secure=False, samesite="lax", max_age=43200, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=False, samesite="lax", max_age=604800, path="/")


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


def start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


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
        start = datetime.fromisoformat(from_str).replace(tzinfo=timezone.utc) if from_str else None
        end = (datetime.fromisoformat(to_str).replace(tzinfo=timezone.utc) + timedelta(days=1)) if to_str else None
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
async def serialize_session_with_master(doc: dict) -> dict:
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


# ===================== Auth Routes =====================
@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
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


@api.post("/auth/logout")
async def logout(response: Response):
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
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(str(user["_id"]), user["email"])
        response.set_cookie("access_token", access, httponly=True, secure=False, samesite="lax", max_age=43200, path="/")
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


@api.post("/vehicles/entry")
async def record_entry(payload: EntryIn, user: dict = Depends(get_current_user)):
    vn = normalize_plate(payload.vehicle_number)
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
        "visit_date": now.strftime("%Y-%m-%d"),
        "duration_seconds": None,
        "entry_camera": payload.entry_camera or "CAM 01",
        "exit_camera": "",
        "entry_image": payload.entry_image or "",
        "exit_image": "",
        "status": "active",
        "created_at": now.isoformat(),
    }
    await db.visit_sessions.insert_one(session)
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
            {"vehicle_number": {"$regex": normalize_plate(q_clean), "$options": "i"}},
            {"phone_number": {"$regex": q_clean, "$options": "i"}},
            {"owner_name": {"$regex": q_clean, "$options": "i"}},
            {"customer_id": {"$regex": up, "$options": "i"}},
        ]
    }
    docs = await db.vehicle_masters.find(query).sort("updated_at", -1).to_list(limit)
    out = []
    for m in docs:
        total = await db.visit_sessions.count_documents({"vehicle_number": m["vehicle_number"]})
        active = await db.visit_sessions.count_documents({"vehicle_number": m["vehicle_number"], "status": "active"})
        out.append({**serialize_master(m), "total_visits": total, "active_visits": active})
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


@api.patch("/vehicles/master/{vehicle_number}")
async def patch_vehicle_master(vehicle_number: str, payload: MasterPatch, user: dict = Depends(get_current_user)):
    vn = normalize_plate(vehicle_number)
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = now_utc().isoformat()
    result = await db.vehicle_masters.update_one({"vehicle_number": vn}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    doc = await db.vehicle_masters.find_one({"vehicle_number": vn})
    return serialize_master(doc)


# ---------- Global visit session listing ----------
@api.get("/visit-sessions")
async def list_visit_sessions(
    q: Optional[str] = None,
    status: Optional[str] = None,
    range: str = "all",
    from_: Optional[str] = None,
    to: Optional[str] = None,
    limit: int = 500,
    user: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if q:
        query["vehicle_number"] = {"$regex": normalize_plate(q), "$options": "i"}
    if status and status != "all":
        # accept both legacy ("inside","exited") and new ("active","completed")
        mapping = {"inside": "active", "exited": "completed", "pending": "pending"}
        query["status"] = mapping.get(status, status)
    start, end = range_bounds(range, from_, to)
    apply_range_query(query, start, end, field="entry_time")
    docs = await db.visit_sessions.find(query).sort("entry_time", -1).to_list(limit)
    out = []
    for d in docs:
        out.append(await serialize_session_with_master(d))
    return out


# ===================== Legacy /api/vehicles CRUD (backward compat) =====================
@api.get("/vehicles")
async def list_vehicles(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 500,
    user: dict = Depends(get_current_user),
):
    query: Dict[str, Any] = {}
    if q:
        # search sessions by number OR join with master by owner/phone
        matching_masters = await db.vehicle_masters.find({
            "$or": [
                {"vehicle_number": {"$regex": normalize_plate(q), "$options": "i"}},
                {"owner_name": {"$regex": q, "$options": "i"}},
                {"phone_number": {"$regex": q, "$options": "i"}},
                {"customer_id": {"$regex": q.upper(), "$options": "i"}},
            ]
        }).to_list(1000)
        numbers = [m["vehicle_number"] for m in matching_masters]
        query["vehicle_number"] = {"$in": numbers} if numbers else {"$regex": normalize_plate(q), "$options": "i"}
    if status and status != "all":
        mapping = {"inside": "active", "exited": "completed", "pending": "pending"}
        query["status"] = mapping.get(status, status)
    if date:
        day = date[:10]
        query["visit_date"] = day
    docs = await db.visit_sessions.find(query).sort("entry_time", -1).to_list(limit)
    out = []
    for d in docs:
        out.append(await serialize_session_with_master(d))
    return out


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
        "visit_date": entry_dt.strftime("%Y-%m-%d"),
        "duration_seconds": duration,
        "entry_camera": "CAM 01",
        "exit_camera": "CAM 02" if exit_dt else "",
        "entry_image": "",
        "exit_image": "",
        "status": new_status,
        "notes": payload.notes or "",
        "created_at": now_utc().isoformat(),
    }
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
async def update_vehicle(vid: str, payload: VehiclePatch, user: dict = Depends(get_current_user)):
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
    doc = await db.visit_sessions.find_one({"id": vid})
    if master_updates:
        master_updates["updated_at"] = now_utc().isoformat()
        await db.vehicle_masters.update_one({"vehicle_number": doc["vehicle_number"]}, {"$set": master_updates})
    return await serialize_session_with_master(doc)


@api.delete("/vehicles/{vid}")
async def delete_vehicle(vid: str, user: dict = Depends(get_current_user)):
    result = await db.visit_sessions.delete_one({"id": vid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
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
    docs = await db.visit_sessions.find(_report_query(period)).sort("entry_time", -1).to_list(5000)
    rows = []
    for d in docs:
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
    docs = await db.visit_sessions.find(query).sort("entry_time", -1).to_list(10000)
    rows = []
    for d in docs:
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
    doc = {
        "name": payload.name,
        "email": payload.email.lower(),
        "role": payload.role,
        "status": payload.status,
        "password_hash": hash_password(pysecrets.token_urlsafe(12)),
        "created_at": now_utc().isoformat(),
        "last_login": None,
    }
    r = await db.users.insert_one(doc)
    return {"id": str(r.inserted_id), **{k: v for k, v in doc.items() if k not in ("password_hash", "_id")}}


@api.delete("/users/{uid}")
async def delete_user(uid: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    if uid == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    result = await db.users.delete_one({"_id": ObjectId(uid)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
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
}


@api.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    doc = await db.settings.find_one({"key": "app"})
    if not doc:
        return DEFAULT_SETTINGS
    doc.pop("_id", None)
    doc.pop("key", None)
    return {**DEFAULT_SETTINGS, **doc}


@api.put("/settings")
async def update_settings(payload: SettingsIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    await db.settings.update_one({"key": "app"}, {"$set": updates}, upsert=True)
    doc = await db.settings.find_one({"key": "app"})
    doc.pop("_id", None)
    doc.pop("key", None)
    return {**DEFAULT_SETTINGS, **doc}


# ===================== Retention purge =====================
async def _retention_loop(interval_hours: float = 24.0) -> None:
    """Background job: prune visit_sessions older than RETENTION_DAYS (180d).

    Runs once immediately on startup, then every `interval_hours` hours.
    Also removes the corresponding snapshot files from disk.
    """
    while True:
        try:
            cutoff_dt = now_utc() - timedelta(days=RETENTION_DAYS)
            cutoff = cutoff_dt.isoformat()
            # Collect snapshot file names first so we can unlink from disk after DB purge.
            snapshots_to_delete: list[str] = []
            async for doc in db.visit_sessions.find(
                {"entry_time": {"$lt": cutoff}},
                {"entry_image": 1, "exit_image": 1},
            ):
                for k in ("entry_image", "exit_image"):
                    url = (doc.get(k) or "").strip()
                    if url.startswith("/api/snapshots/"):
                        snapshots_to_delete.append(url.rsplit("/", 1)[-1])
            r = await db.visit_sessions.delete_many({"entry_time": {"$lt": cutoff}})
            removed_files = 0
            snap_dir = os.path.join(os.path.dirname(__file__), "snapshots")
            for name in snapshots_to_delete:
                fpath = os.path.join(snap_dir, name)
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        removed_files += 1
                except Exception:
                    pass
            if r.deleted_count or removed_files:
                logger.info(
                    "[retention] purged %d visit_sessions and %d snapshot files older than %sd",
                    r.deleted_count, removed_files, RETENTION_DAYS,
                )
        except Exception:
            logger.exception("[retention] loop error")
        await asyncio.sleep(interval_hours * 3600)


@api.post("/maintenance/purge-expired")
async def purge_expired(user: dict = Depends(get_current_user)):
    """Delete visit sessions older than RETENTION_DAYS."""
    cutoff = (now_utc() - timedelta(days=RETENTION_DAYS)).isoformat()
    r = await db.visit_sessions.delete_many({"entry_time": {"$lt": cutoff}})
    return {"deleted": r.deleted_count, "cutoff": cutoff}


# ===================== AI Number Plate Recognition =====================
import re as _re
import base64 as _base64

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
    if not HAS_LLM or not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=503, detail="AI plate recognition is not configured. Set EMERGENT_LLM_KEY.")

    b64 = _strip_data_url(payload.image_base64).strip()
    if not b64:
        raise HTTPException(status_code=400, detail="image_base64 is required")

    prompt = (
        "You are an OCR system for vehicle license plates. "
        "Read ONLY the license plate text from this image. "
        "Return ONLY the plate as continuous alphanumeric characters (no spaces, no dashes). "
        "If no plate is visible or you are unsure, respond with the single word: UNKNOWN. "
        "Do not add any explanation or extra words. Example valid output: UP21AB1234"
    )
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"plate-{uuid.uuid4()}",
            system_message="You are a precise OCR engine. Output only the requested text.",
        ).with_model("gemini", "gemini-3-flash-preview")

        image = ImageContent(image_base64=b64)
        msg = UserMessage(text=prompt, file_contents=[image])
        raw = ""
        try:
            resp = await chat.send_message(msg)
            raw = resp if isinstance(resp, str) else str(resp)
        except Exception:
            # streaming fallback
            from emergentintegrations.llm.chat import TextDelta, StreamDone
            async for ev in chat.stream_message(msg):
                if isinstance(ev, TextDelta):
                    raw += ev.content
                elif isinstance(ev, StreamDone):
                    break

        cleaned = (raw or "").strip().upper()
        if cleaned == "UNKNOWN" or not cleaned:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None}

        plate = _extract_plate(cleaned) or "".join(c for c in cleaned if c.isalnum())
        if not plate or len(plate) < 5:
            return {"plate": None, "confidence": "low", "raw": raw, "action": None}

        # Optional auto entry/exit
        action_result: Optional[Dict[str, Any]] = None
        action = payload.auto_action or "none"
        cam = payload.camera or "AI-CAM"
        if action == "entry":
            await upsert_master(vehicle_number=plate)
            now = now_utc()
            session = {
                "id": str(uuid.uuid4()),
                "vehicle_number": normalize_plate(plate),
                "entry_time": now.isoformat(),
                "exit_time": None,
                "visit_date": now.strftime("%Y-%m-%d"),
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

        return {"plate": plate, "confidence": "high", "raw": raw, "action": action_result}

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

    if not (HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and to):
        # Dry-run mode — return preview so the UI can display it
        return {
            "delivered": False,
            "mode": "dry-run",
            "reason": "Twilio credentials not configured (set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM in .env, plus the recipient number).",
            "preview": {"to": to or "(missing)", "body": body},
        }

    try:
        cli = TwilioClient(cfg["sid"], cfg["token"])
        message = cli.messages.create(from_=cfg["from"], to=to, body=body)
        await db.whatsapp_log.insert_one({
            "sid": message.sid,
            "to": to,
            "body": body,
            "sent_at": now_utc().isoformat(),
            "by": user["email"],
        })
        return {"delivered": True, "mode": "live", "sid": message.sid, "preview": {"to": to, "body": body}}
    except Exception as e:
        logger.exception("Twilio send failed")
        raise HTTPException(status_code=500, detail=f"WhatsApp send failed: {e}")


@api.get("/whatsapp/preview")
async def whatsapp_preview(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    cfg = await _twilio_config()
    body = await _build_daily_summary_text()
    configured = bool(HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and cfg["to"])
    return {
        "configured": configured,
        "to": cfg["to"] or "",
        "from_": cfg["from"] or "",
        "body": body,
    }


# ===================== Health =====================
# ---------- Cameras (USB / IP Webcam / RTSP) ----------
class CameraConnectIn(BaseModel):
    camera_id: str
    source: str  # int-as-string for USB, or http/rtsp URL
    label: Optional[str] = ""


@api.get("/cameras")
async def list_cameras(user: dict = Depends(require_roles(*ROLE_ALL))):
    return camera_manager.list()


@api.get("/cameras/{camera_id}")
async def get_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    state = camera_manager.get(camera_id)
    if not state:
        raise HTTPException(status_code=404, detail="Camera not found")
    return state.public()


@api.post("/cameras/connect")
async def connect_camera(payload: CameraConnectIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    try:
        info = camera_manager.connect(payload.camera_id, payload.source, payload.label or "")
        return info
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Camera connect failed: {e}")


@api.post("/cameras/{camera_id}/disconnect")
async def disconnect_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    if not camera_manager.disconnect(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"ok": True, "camera_id": camera_id}


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


def _authorize_agent(secret: Optional[str]) -> bool:
    if not VASHU_AGENT_SECRET:
        return False
    return bool(secret) and secret == VASHU_AGENT_SECRET


@api.get("/agent/status")
async def agent_status(user: dict = Depends(require_roles(*ROLE_ALL))):
    docs = await db.agents.find({}).to_list(200)
    return [{
        "agent_id": d.get("agent_id"),
        "hostname": d.get("hostname", ""),
        "platform": d.get("platform", ""),
        "version": d.get("version", ""),
        "status": d.get("status", "offline"),
        "last_heartbeat": d.get("last_heartbeat"),
        "connected_at": d.get("connected_at"),
        "cameras": d.get("cameras", []),
    } for d in docs]


@api.post("/agent/register")
async def agent_register_http(payload: AgentRegisterIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    now_iso = now_utc().isoformat()
    await db.agents.update_one({"agent_id": payload.agent_id}, {"$set": {
        "agent_id": payload.agent_id, "hostname": payload.hostname, "platform": payload.platform,
        "version": payload.version, "created_at": now_iso, "status": "offline",
    }}, upsert=True)
    return {"ok": True, "agent_id": payload.agent_id}


@api.post("/agent/{agent_id}/disconnect")
async def agent_disconnect_http(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    await db.agents.update_one({"agent_id": agent_id}, {"$set": {"status": "offline"}})
    doc = await db.agents.find_one({"agent_id": agent_id}) or {}
    for cam_id in doc.get("cameras", []):
        camera_manager.mark_agent_offline(cam_id, "agent disconnect requested")
    return {"ok": True}


@app.websocket("/api/agent/ws")
async def ws_agent(websocket: WebSocket, agent_id: Optional[str] = Query(default=None), secret: Optional[str] = Query(default=None)):
    """Outbound WSS endpoint for the Windows Local Camera Agent.

    Accepts two frame delivery modes for Cloudflare-compatibility:
      1. Legacy JSON text frame:  {"type":"frame","camera_id":..,"jpeg_b64":".."}
      2. Binary frame (preferred): [4-byte BE header-len][utf-8 JSON header][raw JPEG]
         header must include {"type":"frame","camera_id":..,"name":..,"seq":..,"ts":..}
    """
    await websocket.accept()
    hdr_secret = websocket.headers.get("x-agent-secret", "")
    if not _authorize_agent(secret or hdr_secret):
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    active_agent_id: Optional[str] = agent_id
    cameras_registered: set = set()
    if active_agent_id:
        await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
            "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
        }}, upsert=True)

    await websocket.send_json({"type": "hello", "ok": True})
    logger.info("[agent %s] connected", active_agent_id or "?")

    import base64 as _b64
    import struct as _struct

    async def _handle_frame(cam_id: str, jpeg: bytes, name: str) -> None:
        if not cam_id or not jpeg:
            return
        if cam_id not in cameras_registered:
            camera_manager.register_agent_camera(cam_id, label=name or cam_id, agent_id=active_agent_id or "")
            cameras_registered.add(cam_id)
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
                active_agent_id = msg.get("agent_id") or active_agent_id
                if not active_agent_id:
                    await websocket.send_json({"type": "error", "message": "agent_id required"})
                    continue
                await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                    "agent_id": active_agent_id, "hostname": msg.get("hostname", ""),
                    "platform": msg.get("platform", "windows"), "version": msg.get("version", "1.0.0"),
                    "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
                }}, upsert=True)
                await websocket.send_json({"type": "registered", "agent_id": active_agent_id})

            elif mtype == "camera_status":
                cam_id = msg.get("camera_id")
                if not cam_id:
                    continue
                name = msg.get("name", cam_id)
                status = msg.get("status", "online")
                if status == "online":
                    camera_manager.register_agent_camera(cam_id, label=name, agent_id=active_agent_id or "")
                else:
                    camera_manager.mark_agent_offline(cam_id, msg.get("error", "agent reported offline"))
                cameras_registered.add(cam_id)
                if active_agent_id:
                    await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                        "cameras": list(cameras_registered), "last_heartbeat": now_utc().isoformat(),
                    }}, upsert=True)

            elif mtype == "heartbeat":
                if active_agent_id:
                    await db.agents.update_one({"agent_id": active_agent_id},
                                               {"$set": {"last_heartbeat": now_utc().isoformat(), "status": "online"}})

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
        await websocket.send_json({"type": "error", "message": f"camera '{camera_id}' not connected. Call /api/cameras/connect first."})
        await websocket.close(code=4404)
        return

    logger.info("[ws camera %s] client connected user=%s", camera_id, payload.get("email"))

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


@api.get("/")
async def root():
    return {"service": "RDX Car Showroom API", "ok": True}


# ---------- Auto-detection snapshots ----------
from fastapi.responses import FileResponse
from plate_pipeline import SNAPSHOTS_DIR as _SNAPSHOTS_DIR


@api.get("/snapshots/{filename}")
async def get_snapshot(filename: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    # basic path-traversal guard
    safe = os.path.basename(filename)
    fp = os.path.join(_SNAPSHOTS_DIR, safe)
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(fp, media_type="image/jpeg")


# ===================== Startup =====================
async def seed_admin():
    existing = await db.users.find_one({"email": ADMIN_EMAIL})
    if existing is None:
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
    elif not verify_password(ADMIN_PASSWORD, existing["password_hash"]):
        await db.users.update_one({"email": ADMIN_EMAIL},
                                  {"$set": {"password_hash": hash_password(ADMIN_PASSWORD), "role": "admin"}})


async def seed_demo():
    # Seed additional demo users
    demo_users = [
        {"name": "Rakesh Kumar", "email": "manager@rdx.com", "role": "manager"},
        {"name": "Priya Sharma", "email": "security@rdx.com", "role": "security"},
        {"name": "Aditya Patel", "email": "security2@rdx.com", "role": "security", "status": "inactive"},
    ]
    for u in demo_users:
        if not await db.users.find_one({"email": u["email"]}):
            await db.users.insert_one({
                "name": u["name"],
                "email": u["email"],
                "role": u["role"],
                "status": u.get("status", "active"),
                "password_hash": hash_password("demo1234"),
                "created_at": now_utc().isoformat(),
                "last_login": (now_utc() - timedelta(hours=random.randint(1, 200))).isoformat(),
            })
        else:
            # Ensure demo users always have the known 'demo1234' password (idempotent seed refresh)
            existing = await db.users.find_one({"email": u["email"]})
            if not verify_password("demo1234", existing["password_hash"]):
                await db.users.update_one({"_id": existing["_id"]}, {"$set": {"password_hash": hash_password("demo1234")}})

    # Drop legacy 'vehicles' collection from initial MVP if present
    try:
        if "vehicles" in await db.list_collection_names():
            await db.drop_collection("vehicles")
            logger.info("Dropped legacy 'vehicles' collection")
    except Exception:
        pass

    if await db.vehicle_masters.count_documents({}) >= 15:
        return

    # Fresh seed of masters + rich session history
    car_images = [
        "https://images.unsplash.com/photo-1580273916550-e323be2ae537?auto=format&fit=crop&w=600&q=70",
        "https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=600&q=70",
        "https://images.unsplash.com/photo-1544636331-e26879cd4d9b?auto=format&fit=crop&w=600&q=70",
        "https://images.unsplash.com/photo-1550355291-bbee04a92027?auto=format&fit=crop&w=600&q=70",
        "https://images.unsplash.com/photo-1494976388531-d1058494cdd8?auto=format&fit=crop&w=600&q=70",
        "https://images.unsplash.com/photo-1592840062661-eb6a4ef00cb4?auto=format&fit=crop&w=600&q=70",
    ]
    models = ["Hyundai Creta", "Hyundai Verna", "Hyundai i20", "Hyundai Tucson", "Hyundai Venue", "Hyundai Kona EV", "Hyundai Alcazar", "Hyundai Santro"]
    first = ["Arjun", "Neha", "Rohit", "Kavya", "Vikram", "Sanya", "Aman", "Ishita", "Karan", "Meera", "Dev", "Anaya", "Raj", "Zara", "Yash"]
    last = ["Sharma", "Verma", "Patel", "Singh", "Nair", "Iyer", "Khan", "Gupta", "Bose", "Chopra"]
    states = ["KA", "MH", "DL", "TN", "UP", "HR", "KL", "GJ"]
    cameras = ["CAM 01", "CAM 02", "CAM 03", "CAM 04"]

    masters: List[Dict[str, Any]] = []
    plates_seen = set()
    for _ in range(22):
        while True:
            plate = f"{random.choice(states)}{random.randint(1, 99):02d}{random.choice(['AB','MH','KL','TN','DL','HR'])}{random.randint(1000, 9999)}"
            if plate not in plates_seen:
                plates_seen.add(plate)
                break
        masters.append({
            "id": str(uuid.uuid4()),
            "vehicle_number": plate,
            "owner_name": f"{random.choice(first)} {random.choice(last)}",
            "phone_number": f"+91 {random.randint(70, 99)}{random.randint(10000000, 99999999)}",
            "vehicle_model": random.choice(models),
            "vehicle_image": random.choice(car_images),
            "customer_id": f"CUS-{pysecrets.token_hex(3).upper()}",
            "created_at": (now_utc() - timedelta(days=random.randint(30, 170))).isoformat(),
            "updated_at": now_utc().isoformat(),
        })
    # Include the demo plate from the problem statement (UP21AB1234) with the exact same schedule
    showcase_number = "UP21AB1234"
    if showcase_number not in plates_seen:
        masters.insert(0, {
            "id": str(uuid.uuid4()),
            "vehicle_number": showcase_number,
            "owner_name": "Aarav Malhotra",
            "phone_number": "+91 9876543210",
            "vehicle_model": "Hyundai Creta",
            "vehicle_image": car_images[0],
            "customer_id": "CUS-DEMO01",
            "created_at": (now_utc() - timedelta(days=120)).isoformat(),
            "updated_at": now_utc().isoformat(),
        })

    await db.vehicle_masters.insert_many(masters)

    # Generate sessions: each master gets 3-25 completed sessions plus optionally an active session today
    sessions: List[Dict[str, Any]] = []
    today = start_of_day(now_utc())

    for m in masters:
        vn = m["vehicle_number"]
        if vn == showcase_number:
            # Exact schedule from the problem statement — 4 completed sessions today
            plan = [
                (8, 30, 9, 15),
                (10, 20, 11, 10),
                (13, 45, 15, 0),
                (17, 10, 18, 30),
            ]
            for eh, em, xh, xm in plan:
                e = today + timedelta(hours=eh, minutes=em)
                x = today + timedelta(hours=xh, minutes=xm)
                sessions.append(_make_session(vn, e, x, cameras))
            # extra history
            for d in range(1, 45):
                base = today - timedelta(days=d)
                for _ in range(random.randint(0, 3)):
                    eh = random.randint(8, 18)
                    em = random.randint(0, 59)
                    length = random.randint(15, 180)
                    e = base + timedelta(hours=eh, minutes=em)
                    x = e + timedelta(minutes=length)
                    sessions.append(_make_session(vn, e, x, cameras))
            continue

        # regular vehicles
        history_days = random.randint(30, 170)
        for d in range(0, history_days):
            base = today - timedelta(days=d)
            visits_this_day = random.choices([0, 1, 2, 3, 4], weights=[5, 4, 2, 1, 1])[0]
            last_exit = None
            for _ in range(visits_this_day):
                eh = random.randint(8, 19) if last_exit is None else min(19, last_exit.hour + random.randint(1, 3))
                em = random.randint(0, 59)
                length = random.randint(10, 180)
                e = base + timedelta(hours=eh, minutes=em)
                x = e + timedelta(minutes=length)
                last_exit = x
                sessions.append(_make_session(vn, e, x, cameras))

        # 20% chance of an active session today
        if random.random() < 0.2:
            e = today + timedelta(hours=random.randint(9, 17), minutes=random.randint(0, 59))
            sessions.append(_make_session(vn, e, None, cameras))

    # bulk insert in chunks
    BATCH = 500
    for i in range(0, len(sessions), BATCH):
        await db.visit_sessions.insert_many(sessions[i:i + BATCH])
    logger.info("Seeded %d masters and %d visit sessions", len(masters), len(sessions))


def _make_session(vehicle_number: str, entry: datetime, exit_: Optional[datetime], cameras: List[str]) -> Dict[str, Any]:
    duration = int((exit_ - entry).total_seconds()) if exit_ else None
    return {
        "id": str(uuid.uuid4()),
        "vehicle_number": vehicle_number,
        "entry_time": entry.isoformat(),
        "exit_time": exit_.isoformat() if exit_ else None,
        "visit_date": entry.strftime("%Y-%m-%d"),
        "duration_seconds": duration,
        "entry_camera": random.choice(cameras),
        "exit_camera": random.choice(cameras) if exit_ else "",
        "entry_image": "",
        "exit_image": "",
        "status": "completed" if exit_ else "active",
        "created_at": entry.isoformat(),
    }


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


@app.on_event("startup")
async def startup():
    await ensure_indexes()
    await seed_admin()
    await seed_demo()
    # Kick off automatic YOLO+OCR pipeline in the background
    asyncio.create_task(start_auto_detection_loop(camera_manager, db=db, interval_seconds=3.0))
    # 180-day retention cleanup — runs immediately + every 24h
    asyncio.create_task(_retention_loop(interval_hours=24.0))


@app.on_event("shutdown")
async def shutdown():
    client.close()


# Mount router + CORS
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
