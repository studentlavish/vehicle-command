"""Test drives: which showroom cars are out, for how long, and who is driving."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import plate_rules
import security
import test_drives
from core import APP_TZ, ROLE_ALL, db, now_utc, require_roles

router = APIRouter(prefix="/api")


class TripNoteIn(BaseModel):
    driver: Optional[str] = Field(default="", max_length=80)
    customer: Optional[str] = Field(default="", max_length=80)
    note: Optional[str] = Field(default="", max_length=200)


@router.get("/test-drives")
async def list_test_drives(user: dict = Depends(require_roles(*ROLE_ALL))):
    status = await test_drives.current_status(db, now_utc())
    day_start = datetime.combine(datetime.now(APP_TZ).date(), datetime.min.time(), tzinfo=APP_TZ)
    lo, hi = day_start.isoformat(), (day_start + timedelta(days=1)).isoformat()
    return {**status, "today": await test_drives.trips_between(db, lo, hi),
            "max_minutes": test_drives.TEST_DRIVE_MAX_MINUTES}


@router.put("/test-drives/{plate}/trip")
async def note_trip(plate: str, payload: TripNoteIn, user: dict = Depends(require_roles(*ROLE_ALL))):
    """Record who is driving the trip that is out right now."""
    p = plate_rules.clean(plate)
    status = await test_drives.current_status(db, now_utc())
    trip = next((t for t in status["out"] if t["plate"] == p), None)
    if not trip:
        raise HTTPException(status_code=409, detail="That car isn't out on a test drive right now")
    fields = {"driver": (payload.driver or "").strip(), "customer": (payload.customer or "").strip(),
              "note": (payload.note or "").strip(), "updated_by": user.get("email"), "updated_at": now_utc().isoformat()}
    await db.test_drives.update_one({"plate": p, "out_at": trip["out_at"]}, {"$set": fields}, upsert=True)
    await security.audit(db, user, "test_drive.note", p, driver=fields["driver"])
    return {**trip, **{k: fields[k] for k in ("driver", "customer", "note")}}
