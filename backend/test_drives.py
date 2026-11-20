"""Test-drive tracking for showroom cars tagged `test_drive` in Known plates.

Seen from the gate, a test-drive car is "in" while it has an active visit (parked
at the showroom) and "out" from the moment it exits until it enters again. Staff
can note who is driving the current trip; the note is kept per trip (keyed by the
exit time that started it) in the `test_drives` collection.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TEST_DRIVE_MAX_MINUTES = float(os.environ.get("TEST_DRIVE_MAX_MINUTES", "60"))


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def test_drive_plates(db) -> Dict[str, str]:
    """{plate: label} of every car tagged as a test-drive car."""
    return {d["plate"]: d.get("label") or "" async for d in db.known_plates.find({"kind": "test_drive"}, {"plate": 1, "label": 1})}


async def current_status(db, now: Optional[datetime] = None) -> Dict[str, List[dict]]:
    """Which test-drive cars are out (and for how long) and which are at the showroom."""
    now = now or datetime.now(timezone.utc)
    plates = await test_drive_plates(db)
    out: List[dict] = []
    inside: List[dict] = []
    unknown: List[dict] = []
    for plate, label in sorted(plates.items()):
        active = await db.visit_sessions.find_one({"vehicle_number": plate, "status": "active"},
                                                  {"_id": 0, "entry_time": 1})
        if active:
            inside.append({"plate": plate, "label": label, "since": active.get("entry_time")})
            continue
        last = await db.visit_sessions.find_one({"vehicle_number": plate, "exit_time": {"$nin": [None, ""]}},
                                                {"_id": 0, "exit_time": 1, "exit_camera": 1},
                                                sort=[("exit_time", -1)])
        left = _parse((last or {}).get("exit_time"))
        if not left:
            unknown.append({"plate": plate, "label": label})
            continue
        minutes = max(0, int((now - left).total_seconds() // 60))
        trip = await db.test_drives.find_one({"plate": plate, "out_at": last["exit_time"]}, {"_id": 0}) or {}
        out.append({
            "plate": plate, "label": label, "out_at": last["exit_time"], "minutes_out": minutes,
            "overdue": minutes > TEST_DRIVE_MAX_MINUTES,
            "driver": trip.get("driver", ""), "customer": trip.get("customer", ""), "note": trip.get("note", ""),
        })
    out.sort(key=lambda r: -r["minutes_out"])
    return {"out": out, "in": inside, "unknown": unknown}


async def trips_between(db, lo: str, hi: str) -> List[dict]:
    """Finished trips (exit → next entry) whose return falls in [lo, hi)."""
    plates = await test_drive_plates(db)
    trips: List[dict] = []
    for plate, label in plates.items():
        returns = db.visit_sessions.find({"vehicle_number": plate, "entry_time": {"$gte": lo, "$lt": hi}},
                                         {"_id": 0, "entry_time": 1}).sort("entry_time", 1)
        async for ret in returns:
            prev = await db.visit_sessions.find_one(
                {"vehicle_number": plate, "exit_time": {"$nin": [None, ""], "$lte": ret["entry_time"]}},
                {"_id": 0, "exit_time": 1}, sort=[("exit_time", -1)])
            if not prev:
                continue
            a, b = _parse(prev["exit_time"]), _parse(ret["entry_time"])
            note = await db.test_drives.find_one({"plate": plate, "out_at": prev["exit_time"]}, {"_id": 0}) or {}
            trips.append({"plate": plate, "label": label, "out_at": prev["exit_time"], "back_at": ret["entry_time"],
                          "minutes": int((b - a).total_seconds() // 60) if a and b else None,
                          "driver": note.get("driver", ""), "customer": note.get("customer", "")})
    trips.sort(key=lambda t: t["back_at"], reverse=True)
    return trips
