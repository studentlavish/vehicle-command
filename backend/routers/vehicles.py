"""Visits and vehicles: manual entry/exit, vehicle search and detail, visit listing, legacy CRUD."""
import os
import re
import uuid
import secrets as pysecrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends

from plate_pipeline import _persist_lock, local_date
import plate_pipeline
from pymongo.errors import DuplicateKeyError
import security


from core import EntryIn, ExitIn, MasterPatch, RETENTION_DAYS, ROLE_ADMIN_MANAGER, ROLE_ALL, VehicleIn, VehiclePatch, _batch_master_map, apply_range_query, db, get_current_user, now_utc, range_bounds, require_roles, serialize_master, serialize_session, serialize_session_with_master, start_of_day

router = APIRouter(prefix="/api")

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


@router.post("/vehicles/entry")
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


@router.post("/vehicles/exit")
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
@router.get("/vehicles/search")
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


@router.get("/vehicles/master/{vehicle_number}")
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


@router.get("/vehicles/master/{vehicle_number}/sessions")
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


@router.get("/vehicles/master/{vehicle_number}/sessions/paginated")
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


@router.patch("/vehicles/master/{vehicle_number}")
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


@router.get("/visit-sessions/long-open")
async def list_long_open(hours: Optional[float] = None, user: dict = Depends(require_roles(*ROLE_ALL))):
    """Visits still open after MAX_VISIT_HOURS — a missed exit or a car kept overnight. Never auto-closed."""
    h = hours if hours and hours > 0 else MAX_VISIT_HOURS
    return {"hours": h, "sessions": await plate_pipeline.long_open_sessions(db, h)}


@router.get("/visit-sessions")
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
@router.get("/vehicles")
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


@router.post("/vehicles")
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


@router.get("/vehicles/{vid}")
async def get_vehicle(vid: str, user: dict = Depends(get_current_user)):
    doc = await db.visit_sessions.find_one({"id": vid})
    if not doc:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
    return await serialize_session_with_master(doc)


@router.patch("/vehicles/{vid}")
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


@router.delete("/vehicles/{vid}")
async def delete_vehicle(vid: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    doc = await db.visit_sessions.find_one({"id": vid}, {"_id": 0, "vehicle_number": 1, "entry_time": 1, "status": 1})
    result = await db.visit_sessions.delete_one({"id": vid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Vehicle session not found")
    await security.audit(db, user, "visit.delete", vid, **(doc or {}))
    return {"ok": True}


