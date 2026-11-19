"""Alerts — tell a person when the gate needs attention, so nobody has to watch it.

Rules (thresholds via env):
    camera_offline   an active camera sent no frame for CAMERA_OFFLINE_SECONDS (120)
    agent_offline    a registered agent sent no heartbeat for AGENT_OFFLINE_SECONDS (120)
    outbox_backlog   an agent holds more than ALERT_OUTBOX_DEPTH (20) undelivered detections,
                     or one older than ALERT_OUTBOX_AGE_SECONDS (600)
    review_backlog   more than ALERT_REVIEW_PENDING (10) reads wait in Plate Review, or one
                     has waited longer than ALERT_REVIEW_AGE_MINUTES (60)
    storage_low      the snapshot disk is below SNAPSHOT_MIN_FREE_MB
    backup_failed    the last backup failed, or none succeeded in BACKUP_MAX_AGE_HOURS (36)
    long_open        cars recorded inside for longer than MAX_VISIT_HOURS

Each alert is stored in `alerts` and notified when it starts firing, again every
ALERT_REPEAT_HOURS (6) while it keeps firing, and once when it resolves.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("rdx.alerts")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


CAMERA_OFFLINE_SECONDS = _f("CAMERA_OFFLINE_SECONDS", 120)
AGENT_OFFLINE_SECONDS = _f("AGENT_OFFLINE_SECONDS", 120)
OUTBOX_DEPTH = _f("ALERT_OUTBOX_DEPTH", 20)
OUTBOX_AGE_SECONDS = _f("ALERT_OUTBOX_AGE_SECONDS", 600)
REVIEW_PENDING = _f("ALERT_REVIEW_PENDING", 10)
REVIEW_AGE_MINUTES = _f("ALERT_REVIEW_AGE_MINUTES", 60)
BACKUP_MAX_AGE_HOURS = _f("BACKUP_MAX_AGE_HOURS", 36)
REPEAT_HOURS = _f("ALERT_REPEAT_HOURS", 6)
STARTUP_GRACE_SECONDS = _f("ALERT_STARTUP_GRACE_SECONDS", 180)


@dataclass
class Finding:
    key: str          # unique per problem, e.g. "camera_offline:GATE-IN"
    rule: str
    severity: str     # critical | warning
    message: str


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(ts))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ago(seconds: float) -> str:
    s = int(seconds)
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    return f"{s // 3600} h"


async def evaluate(db, now: datetime, *, cameras: Dict[str, Dict[str, Any]], disk: Dict[str, Any],
                   started_at: datetime, backups_enabled: bool, long_open: List[dict]) -> List[Finding]:
    """Current problems. `cameras` is camera_manager.list(); `disk` is snapshot_disk_stats()."""
    out: List[Finding] = []
    warm = (now - started_at).total_seconds() >= STARTUP_GRACE_SECONDS  # let agents reconnect after a restart

    # Cameras that are expected to stream: active configured ones + any an agent reported.
    expected = {d["camera_id"] async for d in db.camera_config.find({"enabled": {"$ne": False}}, {"camera_id": 1})}
    disabled = {d["camera_id"] async for d in db.camera_config.find({"enabled": False}, {"camera_id": 1})}
    async for a in db.agents.find({}, {"cameras": 1}):
        expected.update(c.strip().upper() for c in a.get("cameras", []) if c)
    for cid in sorted(expected - disabled):
        state = cameras.get(cid) or {}
        last = _parse(state.get("last_frame_at"))
        age = (now - last).total_seconds() if last else None
        if warm and (age is None or age > CAMERA_OFFLINE_SECONDS):
            out.append(Finding(f"camera_offline:{cid}", "camera_offline", "critical",
                               f"Camera {cid} is offline — " + (f"no picture for {_ago(age)}" if age is not None
                                                                else "no picture since the server started")))

    async for a in db.agents.find({}):
        aid = a.get("agent_id", "?")
        hb = _parse(a.get("last_heartbeat"))
        age = (now - hb).total_seconds() if hb else None
        if warm and (age is None or age > AGENT_OFFLINE_SECONDS):
            out.append(Finding(f"agent_offline:{aid}", "agent_offline", "critical",
                               f"Camera PC {aid} is not connected — " + (f"last heard {_ago(age)} ago" if age is not None
                                                                         else "it has never connected")))
            continue
        health = a.get("health") or {}
        depth = health.get("outbox_depth") or 0
        oldest = health.get("outbox_oldest_seconds") or 0
        if depth > OUTBOX_DEPTH or oldest > OUTBOX_AGE_SECONDS:
            out.append(Finding(f"outbox_backlog:{aid}", "outbox_backlog", "warning",
                               f"Camera PC {aid} has {depth} plate reads waiting to upload (oldest {_ago(oldest)})"))

    pending = await db.plate_reviews.count_documents({"status": "pending"})
    oldest_review = await db.plate_reviews.find_one({"status": "pending"}, {"created_at": 1}, sort=[("created_at", 1)])
    oldest_at = _parse((oldest_review or {}).get("created_at"))
    waited = (now - oldest_at).total_seconds() if oldest_at else 0
    if pending > REVIEW_PENDING or waited > REVIEW_AGE_MINUTES * 60:
        out.append(Finding("review_backlog", "review_backlog", "warning",
                           f"{pending} plate read{'s' if pending != 1 else ''} waiting in Plate Review "
                           f"(oldest {_ago(waited)})"))

    if disk and disk.get("healthy") is False:
        free_mb = int((disk.get("disk_free_bytes") or 0) / 1024 / 1024)
        out.append(Finding("storage_low", "storage_low", "critical",
                           f"Snapshot disk almost full — {free_mb} MB free; new snapshots are being skipped"))

    if backups_enabled:
        last = await db.backups.find_one({"kind": "mongodb"}, sort=[("created_at", -1)])
        last_ok = await db.backups.find_one({"kind": "mongodb", "status": "success"}, sort=[("created_at", -1)])
        ok_at = _parse((last_ok or {}).get("created_at"))
        if last and last.get("status") == "failed":
            out.append(Finding("backup_failed", "backup_failed", "critical",
                               f"Last database backup failed: {str(last.get('error', ''))[:160]}"))
        elif ok_at and (now - ok_at).total_seconds() > BACKUP_MAX_AGE_HOURS * 3600:
            out.append(Finding("backup_failed", "backup_failed", "critical",
                               f"No successful database backup for {_ago((now - ok_at).total_seconds())}"))
        elif not ok_at and (now - started_at).total_seconds() > BACKUP_MAX_AGE_HOURS * 3600:
            out.append(Finding("backup_failed", "backup_failed", "critical", "No database backup has ever succeeded"))

    if long_open:
        plates = ", ".join(s["vehicle_number"] for s in long_open[:8]) + (" …" if len(long_open) > 8 else "")
        out.append(Finding("long_open", "long_open", "warning",
                           f"{len(long_open)} car{'s' if len(long_open) != 1 else ''} recorded inside for a long time "
                           f"(missed exit?): {plates}"))
    return out


Notify = Callable[[str, dict], Awaitable[None]]  # (kind: fired|repeat|resolved, alert doc)


async def reconcile(db, findings: List[Finding], now: datetime, notify: Notify) -> Dict[str, List[str]]:
    """Store findings as alerts; notify on start, every REPEAT_HOURS, and on resolution."""
    done: Dict[str, List[str]] = {"fired": [], "repeat": [], "resolved": []}
    firing = {d["key"]: d async for d in db.alerts.find({"status": "firing"})}
    seen = set()
    for f in findings:
        seen.add(f.key)
        doc = firing.get(f.key)
        if doc is None:
            doc = {"key": f.key, "rule": f.rule, "severity": f.severity, "message": f.message, "status": "firing",
                   "first_at": now, "last_seen_at": now, "last_notified_at": now}
            await db.alerts.insert_one(doc)
            await _safe_notify(notify, "fired", doc)
            done["fired"].append(f.key)
            continue
        update = {"message": f.message, "severity": f.severity, "last_seen_at": now}
        last = _parse(doc.get("last_notified_at")) or now
        if (now - last) >= timedelta(hours=REPEAT_HOURS):
            update["last_notified_at"] = now
            await _safe_notify(notify, "repeat", {**doc, **update})
            done["repeat"].append(f.key)
        await db.alerts.update_one({"_id": doc["_id"]}, {"$set": update})
    for key, doc in firing.items():
        if key in seen:
            continue
        await db.alerts.update_one({"_id": doc["_id"]}, {"$set": {"status": "resolved", "resolved_at": now}})
        await _safe_notify(notify, "resolved", {**doc, "status": "resolved", "resolved_at": now})
        done["resolved"].append(key)
    return done


async def _safe_notify(notify: Notify, kind: str, doc: dict) -> None:
    try:
        await notify(kind, doc)
    except Exception:
        logger.exception("[alerts] notification failed for %s", doc.get("key"))


def public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    for k in ("first_at", "last_seen_at", "last_notified_at", "resolved_at"):
        if isinstance(out.get(k), datetime):
            out[k] = _parse(out[k]).isoformat()
    return out
