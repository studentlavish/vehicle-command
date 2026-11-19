"""One-off maintenance tasks for the Vashu vehicle-command database.

Run from the backend folder with the backend's virtualenv:

    .venv/bin/python maintenance.py remove-demo-data            # dry run: shows what would go
    .venv/bin/python maintenance.py remove-demo-data --apply    # actually deletes
    .venv/bin/python maintenance.py fix-visit-dates --apply     # recompute visit_date in IST
    .venv/bin/python maintenance.py close-duplicate-active      # several open visits for one plate

Every command is a dry run unless you add --apply.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent / ".env")

DEMO_USER_EMAILS = ["manager@rdx.com", "security@rdx.com", "security2@rdx.com"]
DEMO_IMAGE_PREFIX = "https://images.unsplash.com/"
DEMO_SHOWCASE_PLATE = "UP21AB1234"


def db():
    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    return client[os.environ["DB_NAME"]]


def remove_demo_data(apply: bool) -> None:
    d = db()
    # Fake vehicles were created with stock Unsplash photos; real ones never are.
    fake = list(d.vehicle_masters.find(
        {"$or": [{"vehicle_image": {"$regex": f"^{DEMO_IMAGE_PREFIX}"}}, {"customer_id": "CUS-DEMO01"}]},
        {"vehicle_number": 1, "owner_name": 1},
    ))
    plates = sorted({m["vehicle_number"] for m in fake})
    sessions = d.visit_sessions.count_documents({"vehicle_number": {"$in": plates}}) if plates else 0
    users = list(d.users.find({"email": {"$in": DEMO_USER_EMAILS}}, {"email": 1}))

    print(f"Fake vehicles:      {len(plates)}")
    for m in fake[:10]:
        print(f"   {m['vehicle_number']:<12} {m.get('owner_name', '')}")
    if len(fake) > 10:
        print(f"   … and {len(fake) - 10} more")
    print(f"Their fake visits:  {sessions}")
    print(f"Demo user accounts: {', '.join(u['email'] for u in users) or 'none'}")

    if not apply:
        print("\nDry run — nothing deleted. Re-run with --apply to delete the above.")
        return
    if plates:
        r1 = d.visit_sessions.delete_many({"vehicle_number": {"$in": plates}})
        r2 = d.vehicle_masters.delete_many({"vehicle_number": {"$in": plates}})
        print(f"\nDeleted {r2.deleted_count} vehicles and {r1.deleted_count} visits.")
    if users:
        r3 = d.users.delete_many({"email": {"$in": DEMO_USER_EMAILS}})
        print(f"Deleted {r3.deleted_count} demo user accounts.")


def fix_visit_dates(apply: bool) -> None:
    tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))
    d = db()
    changed = 0
    for s in d.visit_sessions.find({"entry_time": {"$ne": None}}, {"entry_time": 1, "visit_date": 1}):
        try:
            local = datetime.fromisoformat(s["entry_time"]).astimezone(tz).strftime("%Y-%m-%d")
        except Exception:
            continue
        if local != s.get("visit_date"):
            changed += 1
            if apply:
                d.visit_sessions.update_one({"_id": s["_id"]}, {"$set": {"visit_date": local}})
    verb = "Updated" if apply else "Would update"
    print(f"{verb} visit_date on {changed} visits (timezone {tz.key}).")
    if not apply and changed:
        print("Dry run — re-run with --apply to save.")


def close_duplicate_active(apply: bool) -> None:
    """Keep the newest open visit per plate; mark older open ones exit_missed.

    Needed once on databases created before the one-open-visit-per-plate index.
    """
    d = db()
    dupes = list(d.visit_sessions.aggregate([
        {"$match": {"status": "active"}},
        {"$sort": {"entry_time": -1}},
        {"$group": {"_id": "$vehicle_number", "ids": {"$push": "$id"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ]))
    older = [i for g in dupes for i in g["ids"][1:]]
    for g in dupes:
        print(f"   {g['_id']:<12} {g['n']} open visits")
    print(f"{'Closing' if apply else 'Would close'} {len(older)} older open visit(s) as exit_missed.")
    if apply and older:
        stamp = datetime.now(ZoneInfo("UTC")).isoformat()
        d.visit_sessions.update_many({"id": {"$in": older}, "status": "active"}, {"$set": {
            "status": "exit_missed", "exit_missed_at": stamp, "notes": "Closed: duplicate open visit"}})
    elif older:
        print("Dry run — re-run with --apply to save.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["remove-demo-data", "fix-visit-dates", "close-duplicate-active"])
    ap.add_argument("--apply", action="store_true", help="actually make the changes (default is a dry run)")
    args = ap.parse_args()
    {"remove-demo-data": remove_demo_data, "fix-visit-dates": fix_visit_dates,
     "close-duplicate-active": close_duplicate_active}[args.command](args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
