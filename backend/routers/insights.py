"""Dashboard numbers, analytics and report exports."""
import io
import csv
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse

import analytics
from core import (APP_TZ, RETENTION_DAYS, ROLE_ADMIN_MANAGER, apply_range_query, db, get_current_user, now_utc,
                  range_bounds, require_roles, serialize_session_with_master, start_of_day)
from routers.vehicles import normalize_plate

router = APIRouter(prefix="/api")


@router.get("/analytics/insights")
async def analytics_insights(days: int = Query(28, ge=7, le=180),
                             user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Busy hours by weekday, repeat-visitor rate, stay time for new vs returning visitors."""
    return await analytics.visit_insights(db, APP_TZ, days=days, now=now_utc())


@router.get("/analytics/accuracy")
async def analytics_accuracy(days: int = Query(30, ge=7, le=180),
                             user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Per camera per day: share of reads saved automatically, and reviewer corrections."""
    return await analytics.accuracy_by_camera(db, APP_TZ, days=days, now=now_utc())

# ===================== Dashboard =====================
@router.get("/dashboard/stats")
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
@router.get("/analytics/overview")
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


@router.get("/reports/export")
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


@router.get("/visits/export")
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


