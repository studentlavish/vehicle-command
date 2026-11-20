"""Staffing and accuracy insights computed from visits and plate reviews.

All day/hour buckets use the showroom's timezone (APP_TIMEZONE), not UTC.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional

AUTO_CONFIDENCE = ("high", "medium", "low")  # reads saved without a person ("confirmed" = via Plate Review)


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def visit_insights(db, tz: tzinfo, days: int = 28, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Busy hours by weekday, repeat-visitor rate, and stay time for new vs returning visitors."""
    now = now or datetime.now(timezone.utc)
    lo = (now - timedelta(days=days)).isoformat()
    heat = [[0] * 24 for _ in range(7)]  # [weekday Mon=0][hour]
    visits: List[dict] = []
    async for s in db.visit_sessions.find({"entry_time": {"$gte": lo}},
                                          {"_id": 0, "vehicle_number": 1, "entry_time": 1, "duration_seconds": 1, "status": 1}):
        at = _parse(s.get("entry_time"))
        if not at:
            continue
        local = at.astimezone(tz)
        heat[local.weekday()][local.hour] += 1
        visits.append({**s, "_at": at})

    plates = sorted({v["vehicle_number"] for v in visits if v.get("vehicle_number")})
    first_seen: Dict[str, datetime] = {}
    if plates:
        pipeline = [{"$match": {"vehicle_number": {"$in": plates}, "entry_time": {"$nin": [None, ""]}}},
                    {"$group": {"_id": "$vehicle_number", "first": {"$min": "$entry_time"}}}]
        async for g in db.visit_sessions.aggregate(pipeline):
            first_seen[g["_id"]] = _parse(g["first"])

    returning_plates = set()
    dwell = {"new": [], "returning": []}
    for v in visits:
        vn = v.get("vehicle_number")
        first = first_seen.get(vn)
        is_return = bool(first and v["_at"] > first)
        if is_return:
            returning_plates.add(vn)
        d = v.get("duration_seconds")
        if v.get("status") == "completed" and d and d > 0:
            dwell["returning" if is_return else "new"].append(d)

    def avg(xs: List[float]) -> int:
        return int(sum(xs) / len(xs)) if xs else 0

    unique = len(plates)
    busiest = max(((wd, h, heat[wd][h]) for wd in range(7) for h in range(24)), key=lambda x: x[2], default=(0, 0, 0))
    return {
        "days": days,
        "heatmap": heat,
        "busiest": {"weekday": busiest[0], "hour": busiest[1], "entries": busiest[2]} if busiest[2] else None,
        "repeat": {"unique_vehicles": unique, "returning_vehicles": len(returning_plates),
                   "rate": round(len(returning_plates) / unique, 3) if unique else 0.0},
        "dwell": {"new_avg_seconds": avg(dwell["new"]), "new_visits": len(dwell["new"]),
                  "returning_avg_seconds": avg(dwell["returning"]), "returning_visits": len(dwell["returning"])},
    }


async def accuracy_by_camera(db, tz: tzinfo, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Per camera per day: reads saved automatically vs sent to Plate Review, and how often a
    reviewer had to correct the reader. A camera whose review or correction share climbs over
    time usually needs its lens cleaned or its angle fixed."""
    now = now or datetime.now(timezone.utc)
    start = (now.astimezone(tz) - timedelta(days=days - 1)).date()
    lo = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc).isoformat()
    blank = lambda: {"auto": 0, "reviewed": 0, "confirmed": 0, "corrected": 0, "rejected": 0}  # noqa: E731
    cells: Dict[tuple, Dict[str, int]] = defaultdict(blank)

    def day(ts: Any) -> Optional[str]:
        d = _parse(ts)
        return d.astimezone(tz).date().isoformat() if d else None

    async for s in db.visit_sessions.find({"entry_time": {"$gte": lo}, "entry_confidence": {"$in": list(AUTO_CONFIDENCE)}},
                                          {"_id": 0, "entry_camera": 1, "entry_time": 1}):
        if s.get("entry_camera") and day(s["entry_time"]):
            cells[(s["entry_camera"], day(s["entry_time"]))]["auto"] += 1
    async for s in db.visit_sessions.find({"exit_time": {"$gte": lo}, "exit_confidence": {"$in": list(AUTO_CONFIDENCE)}},
                                          {"_id": 0, "exit_camera": 1, "exit_time": 1}):
        if s.get("exit_camera") and day(s["exit_time"]):
            cells[(s["exit_camera"], day(s["exit_time"]))]["auto"] += 1
    async for r in db.plate_reviews.find({"seen_at": {"$gte": lo}},
                                         {"_id": 0, "camera_id": 1, "seen_at": 1, "status": 1, "plate": 1, "final_plate": 1}):
        cam, d = r.get("camera_id") or "—", day(r.get("seen_at"))
        if not d:
            continue
        c = cells[(cam, d)]
        c["reviewed"] += 1
        if r.get("status") == "rejected":
            c["rejected"] += 1
        elif r.get("status") == "accepted":
            if r.get("final_plate") and r.get("final_plate") == r.get("plate"):
                c["confirmed"] += 1
            else:
                c["corrected"] += 1

    cameras = sorted({cam for cam, _ in cells})
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    series = []
    for d in dates:
        row: Dict[str, Any] = {"date": d}
        for cam in cameras:
            c = cells.get((cam, d))
            total = (c["auto"] + c["reviewed"]) if c else 0
            row[cam] = round(100.0 * c["auto"] / total, 1) if total else None  # % saved automatically
        series.append(row)
    totals = []
    for cam in cameras:
        t = blank()
        for d in dates:
            for k, v in cells.get((cam, d), {}).items():
                t[k] += v
        reads = t["auto"] + t["reviewed"]
        resolved = t["confirmed"] + t["corrected"]
        totals.append({"camera": cam, **t, "reads": reads,
                       "auto_rate": round(t["auto"] / reads, 3) if reads else None,
                       "correction_rate": round(t["corrected"] / resolved, 3) if resolved else None})
    return {"days": days, "cameras": cameras, "series": series, "totals": totals}
