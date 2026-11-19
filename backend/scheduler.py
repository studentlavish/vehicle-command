"""Built-in daily job scheduler (replaces the Emergent cron service).

Each job runs once per day at a local time (APP_TIMEZONE). A run is claimed in
MongoDB (`job_runs`, unique per job + date) before it starts, so a job never
runs twice for the same day — not after a restart, and not with several server
processes. If the server was down at the scheduled time, the job still runs
when it comes back, as long as that is within CATCHUP_HOURS; later than that
the day is skipped (and logged) rather than, say, sending yesterday's digest at
midnight.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Optional
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("rdx.scheduler")

APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))
CATCHUP_HOURS = float(os.environ.get("SCHEDULER_CATCHUP_HOURS", "3"))
CHECK_SECONDS = 30


@dataclass
class Job:
    name: str
    at: str                                   # "HH:MM" local time
    run: Callable[[str], Awaitable[None]]     # receives a run id
    enabled: bool = True


def _scheduled_for(job: Job, now_local: datetime) -> datetime:
    hh, mm = (int(x) for x in job.at.split(":"))
    return now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)


async def ensure_indexes(db) -> None:
    await db.job_runs.create_index([("job", 1), ("date", 1)], unique=True)
    await db.job_runs.create_index("started_at", expireAfterSeconds=90 * 24 * 3600)


async def run_due(db, jobs: List[Job], now: Optional[datetime] = None) -> List[str]:
    """Run every job whose time has come today and that hasn't run today. Returns their names."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(APP_TZ)
    ran = []
    for job in jobs:
        if not job.enabled:
            continue
        due = _scheduled_for(job, local)
        if local < due:
            continue
        date = local.strftime("%Y-%m-%d")
        late = local - due
        try:
            await db.job_runs.insert_one({"job": job.name, "date": date, "status": "running",
                                          "started_at": now, "late_seconds": int(late.total_seconds())})
        except DuplicateKeyError:
            continue  # already ran (or skipped) today
        if late > timedelta(hours=CATCHUP_HOURS):
            await db.job_runs.update_one({"job": job.name, "date": date}, {"$set": {"status": "skipped"}})
            logger.warning("[scheduler] %s for %s skipped — server was down at %s and it's now %s",
                           job.name, date, job.at, local.strftime("%H:%M"))
            continue
        run_id = f"{job.name}-{date}"
        try:
            await job.run(run_id)
            await db.job_runs.update_one({"job": job.name, "date": date},
                                         {"$set": {"status": "done", "finished_at": datetime.now(timezone.utc)}})
            logger.info("[scheduler] %s done (%s)", job.name, run_id)
        except Exception as e:
            await db.job_runs.update_one({"job": job.name, "date": date},
                                         {"$set": {"status": "failed", "error": str(e)[:400],
                                                   "finished_at": datetime.now(timezone.utc)}})
            logger.exception("[scheduler] %s failed", job.name)
        ran.append(job.name)
    return ran


async def run_forever(db, jobs: List[Job]) -> None:
    await ensure_indexes(db)
    logger.info("[scheduler] jobs: %s", ", ".join(f"{j.name}@{j.at}" for j in jobs if j.enabled) or "none")
    while True:
        try:
            await run_due(db, jobs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[scheduler] loop error")
        await asyncio.sleep(CHECK_SECONDS)
