"""Shared setup for the API: database, settings, auth helpers, time helpers, serializers, models."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal, Dict, Any, Tuple

import bcrypt
import jwt
from bson import ObjectId
from fastapi import HTTPException, Request, Response, Depends
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr

from zoneinfo import ZoneInfo
import security
import logging_setup


# --------- Setup ---------
logging_setup.configure()
logger = logging.getLogger("rdx")
SERVER_STARTED_AT = datetime.now(timezone.utc)

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


