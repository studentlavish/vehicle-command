"""Durable outbox for plate detections.

Every detection is written to a small SQLite file *before* it is sent, and is
removed only when the server acknowledges it (plate_ack with the same
event_id). A detection therefore survives network drops, server restarts and
agent crashes; it is simply re-sent until the server confirms it. The server
de-duplicates on event_id, so a re-send never records a car twice.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RETRY_AFTER_SECONDS = 15.0


class Outbox:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS outbox (
            event_id   TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            last_sent  REAL,
            attempts   INTEGER NOT NULL DEFAULT 0,
            payload    TEXT NOT NULL
        )""")

    def add(self, event_id: str, payload: Dict[str, Any], now: Optional[float] = None) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO outbox (event_id, created_at, payload) VALUES (?, ?, ?)",
                             (event_id, now if now is not None else time.time(), json.dumps(payload)))

    def due(self, now: Optional[float] = None, retry_after: float = RETRY_AFTER_SECONDS,
            limit: int = 20) -> List[Tuple[str, Dict[str, Any]]]:
        """Events never sent, or sent more than `retry_after` seconds ago without an ack (oldest first)."""
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id, payload FROM outbox WHERE last_sent IS NULL OR last_sent <= ? "
                "ORDER BY created_at LIMIT ?", (now - retry_after, limit)).fetchall()
        return [(eid, json.loads(p)) for eid, p in rows]

    def mark_sent(self, event_id: str, now: Optional[float] = None) -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET last_sent = ?, attempts = attempts + 1 WHERE event_id = ?",
                             (now if now is not None else time.time(), event_id))

    def ack(self, event_id: str) -> bool:
        with self._lock:
            return self._db.execute("DELETE FROM outbox WHERE event_id = ?", (event_id,)).rowcount > 0

    def release(self, event_id: str) -> None:
        """Server could not process it (plate_nack) — make it due again right away."""
        with self._lock:
            self._db.execute("UPDATE outbox SET last_sent = NULL WHERE event_id = ?", (event_id,))

    def release_all(self) -> None:
        """After a reconnect, everything unacknowledged is due again."""
        with self._lock:
            self._db.execute("UPDATE outbox SET last_sent = NULL")

    def depth(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def oldest_age(self, now: Optional[float] = None) -> Optional[float]:
        with self._lock:
            row = self._db.execute("SELECT MIN(created_at) FROM outbox").fetchone()
        if not row or row[0] is None:
            return None
        return (now if now is not None else time.time()) - row[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
