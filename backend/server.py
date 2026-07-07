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
import asyncio
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

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

app = FastAPI(title="RDX Car Showroom API")
api = APIRouter(prefix="/api")


# --------- Helpers ---------
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


def serialize_vehicle(doc: dict) -> dict:
    return {
        "id": doc.get("id") or str(doc.get("_id", "")),
        "vehicle_number": doc["vehicle_number"],
        "owner_name": doc["owner_name"],
        "contact_number": doc.get("contact_number", ""),
        "vehicle_model": doc.get("vehicle_model", ""),
        "entry_time": doc.get("entry_time"),
        "exit_time": doc.get("exit_time"),
        "status": doc.get("status", "inside"),
        "created_at": doc.get("created_at"),
        "notes": doc.get("notes", ""),
    }


# --------- Models ---------
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    remember_me: Optional[bool] = False


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


# --------- Auth Routes ---------
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


# --------- Dashboard ---------
def _today_bounds():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


@api.get("/dashboard/stats")
async def dashboard_stats(user: dict = Depends(get_current_user)):
    start_today, end_today = _today_bounds()
    start_yday = (datetime.fromisoformat(start_today) - timedelta(days=1)).isoformat()

    today_entries = await db.vehicles.count_documents({"entry_time": {"$gte": start_today, "$lt": end_today}})
    today_exits = await db.vehicles.count_documents({"exit_time": {"$gte": start_today, "$lt": end_today}})
    yday_entries = await db.vehicles.count_documents({"entry_time": {"$gte": start_yday, "$lt": start_today}})
    yday_exits = await db.vehicles.count_documents({"exit_time": {"$gte": start_yday, "$lt": start_today}})

    cars_inside = await db.vehicles.count_documents({"status": "inside"})
    total_vehicles = await db.vehicles.count_documents({})

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    monthly_visitors = await db.vehicles.count_documents({"entry_time": {"$gte": month_start}})

    # 7-day trend for each metric
    async def daily_series(field: str, days: int = 7):
        out = []
        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        for i in range(days, 0, -1):
            day_start = end - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            c = await db.vehicles.count_documents({field: {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            out.append({"label": day_start.strftime("%a"), "value": c})
        return out

    entry_trend = await daily_series("entry_time")
    exit_trend = await daily_series("exit_time")

    def pct(cur, prev):
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


# --------- Vehicles ---------
@api.get("/vehicles")
async def list_vehicles(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 200,
    user: dict = Depends(get_current_user),
):
    query: dict = {}
    if q:
        query["$or"] = [
            {"vehicle_number": {"$regex": q, "$options": "i"}},
            {"owner_name": {"$regex": q, "$options": "i"}},
            {"contact_number": {"$regex": q, "$options": "i"}},
        ]
    if status and status != "all":
        query["status"] = status
    if date:
        day_start = datetime.fromisoformat(date).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        query["entry_time"] = {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}

    docs = await db.vehicles.find(query).sort("created_at", -1).to_list(limit)
    return [serialize_vehicle(d) for d in docs]


@api.post("/vehicles")
async def create_vehicle(payload: VehicleIn, user: dict = Depends(get_current_user)):
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = payload.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["entry_time"] = doc.get("entry_time") or now_iso
    doc["created_at"] = now_iso
    await db.vehicles.insert_one(doc)
    return serialize_vehicle(doc)


@api.get("/vehicles/{vid}")
async def get_vehicle(vid: str, user: dict = Depends(get_current_user)):
    doc = await db.vehicles.find_one({"id": vid})
    if not doc:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return serialize_vehicle(doc)


@api.patch("/vehicles/{vid}")
async def update_vehicle(vid: str, payload: VehiclePatch, user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if updates.get("status") == "exited" and "exit_time" not in updates:
        updates["exit_time"] = datetime.now(timezone.utc).isoformat()
    result = await db.vehicles.update_one({"id": vid}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    doc = await db.vehicles.find_one({"id": vid})
    return serialize_vehicle(doc)


@api.delete("/vehicles/{vid}")
async def delete_vehicle(vid: str, user: dict = Depends(get_current_user)):
    result = await db.vehicles.delete_one({"id": vid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return {"ok": True}


# --------- Analytics ---------
@api.get("/analytics/overview")
async def analytics_overview(user: dict = Depends(get_current_user)):
    async def series(days: int, fmt: str, label_fmt: str):
        out = []
        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        for i in range(days, 0, -1):
            day_start = end - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            entries = await db.vehicles.count_documents({"entry_time": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            exits = await db.vehicles.count_documents({"exit_time": {"$gte": day_start.isoformat(), "$lt": day_end.isoformat()}})
            out.append({"label": day_start.strftime(label_fmt), "entries": entries, "exits": exits})
        return out

    daily = await series(7, "%Y-%m-%d", "%a")
    weekly = await series(28, "%Y-%m-%d", "%d %b")
    monthly = []
    now = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for i in range(11, -1, -1):
        month_start = (now - timedelta(days=30 * i)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        entries = await db.vehicles.count_documents({"entry_time": {"$gte": month_start.isoformat(), "$lt": next_month.isoformat()}})
        exits = await db.vehicles.count_documents({"exit_time": {"$gte": month_start.isoformat(), "$lt": next_month.isoformat()}})
        monthly.append({"label": month_start.strftime("%b"), "entries": entries, "exits": exits})

    total_entries = await db.vehicles.count_documents({"entry_time": {"$ne": None}})
    total_exits = await db.vehicles.count_documents({"exit_time": {"$ne": None}})

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "pie": [
            {"name": "Entries", "value": total_entries},
            {"name": "Exits", "value": total_exits},
        ],
    }


# --------- Reports (CSV/Excel/PDF) ---------
def _report_query(period: str):
    now = datetime.now(timezone.utc)
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        start = now - timedelta(days=7)
    elif period == "monthly":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=365)
    return {"created_at": {"$gte": start.isoformat()}}


@api.get("/reports/export")
async def export_report(
    period: str = "daily",
    format: str = "csv",
    user: dict = Depends(get_current_user),
):
    docs = await db.vehicles.find(_report_query(period)).sort("created_at", -1).to_list(5000)
    rows = [serialize_vehicle(d) for d in docs]

    filename_base = f"rdx_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Vehicle Number", "Owner Name", "Contact", "Model", "Entry Time", "Exit Time", "Status"])
        for r in rows:
            writer.writerow([r["vehicle_number"], r["owner_name"], r["contact_number"], r["vehicle_model"], r["entry_time"] or "", r["exit_time"] or "", r["status"]])
        content = buf.getvalue().encode()
        return StreamingResponse(io.BytesIO(content), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename_base}.csv"})

    if format == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = f"{period.capitalize()} Report"
        ws.append(["Vehicle Number", "Owner Name", "Contact", "Model", "Entry Time", "Exit Time", "Status"])
        for r in rows:
            ws.append([r["vehicle_number"], r["owner_name"], r["contact_number"], r["vehicle_model"], r["entry_time"] or "", r["exit_time"] or "", r["status"]])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={filename_base}.xlsx"})

    if format == "pdf":
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
        styles = getSampleStyleSheet()
        title = Paragraph(f"RDX Car Showroom — {period.capitalize()} Report", styles["Title"])
        data = [["Vehicle #", "Owner", "Contact", "Model", "Entry", "Exit", "Status"]]
        for r in rows:
            data.append([r["vehicle_number"], r["owner_name"], r["contact_number"], r["vehicle_model"], (r["entry_time"] or "")[:16], (r["exit_time"] or "")[:16], r["status"]])
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ]))
        doc.build([title, Spacer(1, 12), tbl])
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename_base}.pdf"})

    raise HTTPException(status_code=400, detail="Invalid format. Use csv, xlsx, or pdf.")


# --------- Users ---------
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
        "password_hash": hash_password(secrets.token_urlsafe(12)),
        "created_at": datetime.now(timezone.utc).isoformat(),
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


# --------- Settings ---------
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


# --------- Health ---------
@api.get("/")
async def root():
    return {"service": "RDX Car Showroom API", "ok": True}


# --------- Startup: seed admin, indexes, demo data ---------
async def seed_admin():
    existing = await db.users.find_one({"email": ADMIN_EMAIL})
    if existing is None:
        await db.users.insert_one({
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "name": "Admin",
            "role": "admin",
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_login": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Seeded admin user: %s", ADMIN_EMAIL)
    elif not verify_password(ADMIN_PASSWORD, existing["password_hash"]):
        await db.users.update_one({"email": ADMIN_EMAIL}, {"$set": {"password_hash": hash_password(ADMIN_PASSWORD), "role": "admin"}})
        logger.info("Rehashed admin password: %s", ADMIN_EMAIL)


async def seed_users_and_vehicles():
    # Additional demo users
    demo_users = [
        {"name": "Rakesh Kumar", "email": "manager@rdx.com", "role": "manager", "status": "active"},
        {"name": "Priya Sharma", "email": "security@rdx.com", "role": "security", "status": "active"},
        {"name": "Aditya Patel", "email": "security2@rdx.com", "role": "security", "status": "inactive"},
    ]
    for u in demo_users:
        existing = await db.users.find_one({"email": u["email"]})
        if not existing:
            await db.users.insert_one({
                **u,
                "password_hash": hash_password("demo1234"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": (datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 200))).isoformat(),
            })

    # Vehicles demo data
    count = await db.vehicles.count_documents({})
    if count >= 30:
        return

    models = ["Hyundai Creta", "Hyundai Verna", "Hyundai i20", "Hyundai Tucson", "Hyundai Venue", "Hyundai Kona EV", "Hyundai Alcazar", "Hyundai Santro"]
    first = ["Arjun", "Neha", "Rohit", "Kavya", "Vikram", "Sanya", "Aman", "Ishita", "Karan", "Meera", "Dev", "Anaya", "Raj", "Zara", "Yash"]
    last = ["Sharma", "Verma", "Patel", "Singh", "Nair", "Iyer", "Khan", "Gupta", "Bose", "Chopra"]

    docs = []
    now = datetime.now(timezone.utc)
    for i in range(42):
        entry = now - timedelta(days=random.randint(0, 25), hours=random.randint(0, 23), minutes=random.randint(0, 59))
        status = random.choices(["inside", "exited", "pending"], weights=[3, 6, 1])[0]
        exit_time = None
        if status == "exited":
            exit_time = (entry + timedelta(minutes=random.randint(20, 300))).isoformat()
        docs.append({
            "id": str(uuid.uuid4()),
            "vehicle_number": f"KA{random.randint(1, 99):02d}{random.choice(['AB','MH','KL','TN','DL','HR'])}{random.randint(1000, 9999)}",
            "owner_name": f"{random.choice(first)} {random.choice(last)}",
            "contact_number": f"+91 {random.randint(70, 99)}{random.randint(10000000, 99999999)}",
            "vehicle_model": random.choice(models),
            "entry_time": entry.isoformat(),
            "exit_time": exit_time,
            "status": status,
            "notes": "",
            "created_at": entry.isoformat(),
        })
    if docs:
        await db.vehicles.insert_many(docs)
        logger.info("Seeded %d demo vehicles", len(docs))


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.vehicles.create_index("vehicle_number")
    await db.vehicles.create_index("created_at")
    await db.vehicles.create_index("status")


@app.on_event("startup")
async def startup():
    await ensure_indexes()
    await seed_admin()
    await seed_users_and_vehicles()


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
