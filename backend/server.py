from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import io
import csv
import uuid
import logging
import random
import secrets as pysecrets
from datetime import datetime, timezone, timedelta, date as ddate
from typing import List, Optional, Literal, Dict, Any, Tuple

import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

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

RETENTION_DAYS = 180

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


# ===================== Auth Routes =====================
@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    uid = str(user["_id"])
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": now_utc().isoformat()}})
    return {
        "id": uid,
        "email": email,
        "name": user.get("name", "Admin"),
        "role": user.get("role", "admin"),
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
        {"$match": {"vehicle_number": vn, "status": "completed", "entry_time": {"$gte": horizon_180.isoformat()}}},
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
        {"$match": {"status": "completed", "entry_time": {"$gte": horizon}, "duration_seconds": {"$ne": None}}},
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
    if period == "daily":
        start = start_of_day(n)
    elif period == "weekly":
        start = n - timedelta(days=7)
    elif period == "monthly":
        start = n - timedelta(days=30)
    else:
        start = n - timedelta(days=365)
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
async def list_users(user: dict = Depends(get_current_user)):
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
async def create_user(payload: UserIn, user: dict = Depends(get_current_user)):
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
async def delete_user(uid: str, user: dict = Depends(get_current_user)):
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
async def update_settings(payload: SettingsIn, user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    await db.settings.update_one({"key": "app"}, {"$set": updates}, upsert=True)
    doc = await db.settings.find_one({"key": "app"})
    doc.pop("_id", None)
    doc.pop("key", None)
    return {**DEFAULT_SETTINGS, **doc}


# ===================== Retention purge =====================
@api.post("/maintenance/purge-expired")
async def purge_expired(user: dict = Depends(get_current_user)):
    """Delete visit sessions older than RETENTION_DAYS."""
    cutoff = (now_utc() - timedelta(days=RETENTION_DAYS)).isoformat()
    r = await db.visit_sessions.delete_many({"entry_time": {"$lt": cutoff}})
    return {"deleted": r.deleted_count, "cutoff": cutoff}


# ===================== Health =====================
@api.get("/")
async def root():
    return {"service": "RDX Car Showroom API", "ok": True}


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
