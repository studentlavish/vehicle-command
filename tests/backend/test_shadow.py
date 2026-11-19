"""Phase 7 — shadow-run comparison of system records vs. the manual gate log."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import shadow
from conftest import BACKEND, DB_NAME, MONGO_URL

IST = ZoneInfo("Asia/Kolkata")


def ev(hh, mm, plate, d, auto=True, day=2):
    return shadow.Event(datetime(2026, 12, day, hh, mm, tzinfo=IST), plate, d, auto=auto)


def test_compare_classifies_every_event():
    manual = [ev(10, 0, "KA05MG9999", "in", False), ev(10, 5, "MH12AB1234", "in", False),
              ev(10, 30, "DL3CAB1234", "in", False), ev(11, 0, "KA05MG9999", "out", False)]
    system = [ev(10, 1, "KA05MG9999", "in"),              # match
              ev(10, 6, "MH12AB1284", "in"),              # misread (one character)
              ev(11, 2, "KA05MG9999", "out", auto=False),  # match, confirmed by a person
              ev(12, 0, "TN87C5106", "in")]               # extra — not in the manual log
    rep = shadow.compare(manual, system)
    assert [m.plate for m, _ in rep.matched] == ["KA05MG9999", "KA05MG9999"]
    assert [(m.plate, s.plate) for m, s in rep.misread] == [("MH12AB1234", "MH12AB1284")]
    assert [m.plate for m in rep.missed] == ["DL3CAB1234"]
    assert [s.plate for s in rep.extra] == ["TN87C5106"]
    # auto events: matched-auto 1 + misread 1 + extra 1 → 1 right of 3
    assert rep.precision == pytest.approx(33.33) and rep.recall == 50.0


def test_direction_and_window_must_match():
    manual = [ev(10, 0, "KA05MG9999", "in", False)]
    assert shadow.compare(manual, [ev(10, 0, "KA05MG9999", "out")]).missed
    assert shadow.compare(manual, [ev(10, 20, "KA05MG9999", "in")], window_minutes=10).missed
    assert shadow.compare(manual, [ev(10, 20, "KA05MG9999", "in")], window_minutes=30).matched


def test_read_manual_log(tmp_path):
    p = tmp_path / "log.csv"
    p.write_text("Time,Plate,Direction\n2026-12-02 10:00,KA 05 MG 9999,IN\n2026-12-02 11:00,KA-05-MG-9999,exit\n")
    rows = shadow.read_manual_log(str(p), IST)
    assert [(r.plate, r.direction, r.time.hour) for r in rows] == [("KA05MG9999", "in", 10), ("KA05MG9999", "out", 11)]
    p.write_text("time,plate,direction\n2026-12-02 10:00,KA05MG9999,sideways\n")
    with pytest.raises(ValueError, match="line 2"):
        shadow.read_manual_log(str(p), IST)


def test_shadow_compare_command(client, db, tmp_path):
    entry = datetime(2026, 12, 2, 10, 1, tzinfo=IST).astimezone(timezone.utc)
    db.visit_sessions.insert_one({"id": "s1", "vehicle_number": "KA05MG9999", "detected_by": "agent_yolo",
                                  "entry_time": entry.isoformat(), "exit_time": (entry + timedelta(hours=1)).isoformat(),
                                  "exit_confidence": "high", "status": "completed"})
    log = tmp_path / "gate.csv"
    log.write_text("time,plate,direction\n2026-12-02 10:00,KA05MG9999,in\n2026-12-02 11:00,KA05MG9999,out\n"
                   "2026-12-02 11:30,MH12AB1234,in\n")
    out_csv = tmp_path / "result.csv"
    env = {**os.environ, "MONGO_URL": MONGO_URL, "DB_NAME": DB_NAME, "APP_TIMEZONE": "Asia/Kolkata"}
    r = subprocess.run([sys.executable, "maintenance.py", "shadow-compare", "--log", str(log), "--csv", str(out_csv)],
                       cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout.strip().splitlines()[-2])
    assert summary["matched"] == 2 and summary["missed"] == 1 and summary["precision"] == 100.0
    assert "Missed by the system (1)" in r.stdout
    assert Path(out_csv).read_text().count("\n") == 4  # header + 3 rows
