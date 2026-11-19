"""Shadow-run comparison: the system's records vs. the guard's manual log.

During the shadow run (docs/go-live.md) the guard keeps logging by hand while
the system runs on its own. Each day, compare the two:

    python maintenance.py shadow-compare --log gate-2026-12-01.csv

Manual log CSV columns (header required):  time, plate, direction
    time       local time, "YYYY-MM-DD HH:MM"
    plate      as written on the car (spaces/dashes are ignored)
    direction  in | out

A manual event matches a system event with the same direction within
--window minutes (default 10): exactly the same plate is a hit; a plate that
differs by one or two characters is a *misread*.

Reported:
    precision  share of automatically saved events that match the manual log
               exactly (wrong or invented records lower it — target ≥ 99.5 %)
    recall     share of manual events the system recorded correctly (any route)
    missed     cars in the manual log the system has no record of
    extra      system events the manual log doesn't have (check them: either a
               guard omission or a false record)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Dict, Iterable, List, Optional

import plate_rules


@dataclass
class Event:
    time: datetime
    plate: str
    direction: str            # in | out
    auto: bool = True         # saved without a person (False = confirmed in Plate Review / manual)
    ref: str = ""             # session id, for following up


@dataclass
class Report:
    manual: int
    system: int
    matched: List[tuple] = field(default_factory=list)      # (manual, system)
    misread: List[tuple] = field(default_factory=list)      # (manual, system)
    missed: List[Event] = field(default_factory=list)
    extra: List[Event] = field(default_factory=list)

    @property
    def auto_events(self) -> int:
        return sum(1 for _, s in self.matched + self.misread if s.auto) + sum(1 for s in self.extra if s.auto)

    @property
    def precision(self) -> Optional[float]:
        auto_ok = sum(1 for _, s in self.matched if s.auto)
        return round(100.0 * auto_ok / self.auto_events, 2) if self.auto_events else None

    @property
    def recall(self) -> Optional[float]:
        return round(100.0 * len(self.matched) / self.manual, 2) if self.manual else None

    def summary(self) -> Dict[str, object]:
        return {"manual_events": self.manual, "system_events": self.system, "matched": len(self.matched),
                "misread": len(self.misread), "missed": len(self.missed), "extra": len(self.extra),
                "auto_events": self.auto_events, "precision": self.precision, "recall": self.recall}


def read_manual_log(path: str, tz: tzinfo) -> List[Event]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), 2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            direction = row.get("direction", "").lower()
            direction = {"entry": "in", "exit": "out"}.get(direction, direction)
            if direction not in ("in", "out"):
                raise ValueError(f"line {n}: direction must be in or out, got {row.get('direction')!r}")
            try:
                t = datetime.strptime(row["time"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except (KeyError, ValueError):
                raise ValueError(f"line {n}: time must be YYYY-MM-DD HH:MM, got {row.get('time')!r}")
            out.append(Event(t, plate_rules.clean(row.get("plate")), direction, auto=False))
    return out


def system_events(sessions: Iterable[dict]) -> List[Event]:
    """visit_sessions documents → entry/exit events."""
    out = []
    for s in sessions:
        if s.get("entry_time"):
            out.append(Event(datetime.fromisoformat(s["entry_time"]), s["vehicle_number"], "in",
                             auto=s.get("detected_by") == "agent_yolo", ref=s.get("id", "")))
        if s.get("exit_time"):
            out.append(Event(datetime.fromisoformat(s["exit_time"]), s["vehicle_number"], "out",
                             auto=s.get("exit_confidence") in ("high", "medium"), ref=s.get("id", "")))
    return out


def compare(manual: List[Event], system: List[Event], window_minutes: float = 10) -> Report:
    window = timedelta(minutes=window_minutes)
    rep = Report(manual=len(manual), system=len(system))
    free = sorted(system, key=lambda e: e.time)
    used = set()

    def closest(m: Event, ok) -> Optional[int]:
        best, best_gap = None, None
        for i, s in enumerate(free):
            if i in used or s.direction != m.direction or not ok(s):
                continue
            gap = abs(s.time - m.time)
            if gap <= window and (best_gap is None or gap < best_gap):
                best, best_gap = i, gap
        return best

    unmatched = []
    for m in sorted(manual, key=lambda e: e.time):        # exact plates first
        i = closest(m, lambda s: s.plate == m.plate)
        if i is None:
            unmatched.append(m)
            continue
        used.add(i)
        rep.matched.append((m, free[i]))
    for m in unmatched:                                    # then near plates = misreads
        i = closest(m, lambda s: plate_rules.distance(s.plate, m.plate) <= 2)
        if i is None:
            rep.missed.append(m)
            continue
        used.add(i)
        rep.misread.append((m, free[i]))
    rep.extra = [s for i, s in enumerate(free) if i not in used]
    return rep


def format_report(rep: Report, tz: tzinfo) -> str:
    def t(e: Event) -> str:
        return e.time.astimezone(tz).strftime("%d %b %H:%M")
    lines = [
        f"Manual events {rep.manual}   system events {rep.system}   matched {len(rep.matched)}",
        f"Precision (auto-saved records that are right): {rep.precision}%   target ≥ 99.5%",
        f"Recall (manual events recorded correctly):     {rep.recall}%",
    ]
    if rep.misread:
        lines.append(f"\nMisread ({len(rep.misread)}) — manual plate → system plate:")
        lines += [f"  {t(m)} {m.direction:<3} {m.plate} → {s.plate}{'' if s.auto else ' (confirmed by a person)'}  [{s.ref}]"
                  for m, s in rep.misread]
    if rep.missed:
        lines.append(f"\nMissed by the system ({len(rep.missed)}):")
        lines += [f"  {t(m)} {m.direction:<3} {m.plate}" for m in rep.missed]
    if rep.extra:
        lines.append(f"\nIn the system but not in the manual log ({len(rep.extra)}) — check each one:")
        lines += [f"  {t(s)} {s.direction:<3} {s.plate}{'' if s.auto else ' (confirmed by a person)'}  [{s.ref}]"
                  for s in rep.extra]
    return "\n".join(lines)
