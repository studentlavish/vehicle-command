"""Phase 4 — correction penalties, no-plate vehicles, benchmark metrics."""
from pathlib import Path

import cv2
import numpy as np
import pytest

import benchmark_ocr as bench
import plate_rules
from plate_detector import LocalPlateDetector
from plate_ocr import OcrRead, PlateBox, PlateReader


# ---------- look-alike corrections ----------
@pytest.mark.parametrize("raw,plate,swaps,risky,trimmed", [
    ("KA05MG9999", "KA05MG9999", 0, 0, 0),
    ("KAO5MG9999", "KA05MG9999", 1, 0, 0),     # O→0 safe
    ("MH12A81234", "MH12AB1234", 1, 0, 0),     # 8→B safe
    ("KA05MG999T", "KA05MG9997", 1, 1, 0),     # T→7 risky
    ("4P05MG9999", "AP05MG9999", 1, 1, 0),     # 4→A risky
    ("IND KA05MG9999", "KA05MG9999", 0, 0, 3),  # hologram trimmed
    ("XKA05MG9999", "KA05MG9999", 0, 0, 1),    # stray border char
])
def test_correct_detail(raw, plate, swaps, risky, trimmed):
    assert plate_rules.correct_detail(raw) == (plate, swaps, risky, trimmed)


def test_correction_factor_orders_by_risk():
    clean = plate_rules.correction_factor("KA05MG9999")[1]
    safe = plate_rules.correction_factor("KAO5MG9999")[1]
    risky = plate_rules.correction_factor("KA05MG999T")[1]
    assert clean == 1.0 > safe > risky
    assert plate_rules.correction_factor("HELLO") == (None, 0.0)


class FixedEngine:
    def __init__(self, name, text, conf):
        self.name, self.text, self.conf = name, text, conf

    def read(self, img):
        return OcrRead(self.text, self.conf, self.name)


CROP = np.zeros((20, 80, 3), np.uint8)


def test_reader_lowers_confidence_for_risky_fix():
    exact = PlateReader(ensemble=False, engines=[FixedEngine("a", "KA05MG9997", 0.95)]).read(CROP)
    risky = PlateReader(ensemble=False, engines=[FixedEngine("a", "KA05MG999T", 0.95)]).read(CROP)
    assert exact[0] == risky[0] == "KA05MG9997"
    assert exact[1] == pytest.approx(0.95)
    assert risky[1] == pytest.approx(0.95 * plate_rules.RISKY_SWAP_FACTOR)


def test_risky_fix_falls_below_sure_and_gets_cross_checked():
    reader = PlateReader(engines=[FixedEngine("a", "KA05MG999T", 0.95), FixedEngine("b", "KA05MG9991", 0.9)])
    plate, conf, note = reader.read(CROP)
    assert "≠" in note and conf < 0.7  # disagreement after the penalty → low, goes to review


# ---------- vehicles without a plate ----------
class NoPlates:
    model = "none"

    def find(self, frame):
        return []

    @staticmethod
    def crop(frame, box):
        return frame


class NeverReads:
    description = "none"

    def read(self, crop):
        return None


def test_vehicle_without_plate_is_reported_once():
    seen = {"car": True}
    det = LocalPlateDetector(finder=NoPlates(), reader=NeverReads(),
                             vehicle_detect=lambda f: (seen["car"], "car", 0.9))
    frame = np.zeros((120, 160, 3), np.uint8)
    out = []
    for i in range(25):
        out += det.process_frame(frame, "GATE-IN", now=100 + i * 0.2)
    assert len(out) == 1 and out[0].plate == "" and out[0].crop_jpeg and out[0].vehicle_class == "car"
    seen["car"] = False
    det.process_frame(frame, "GATE-IN", now=106)
    seen["car"] = True
    for i in range(12):
        out += det.process_frame(frame, "GATE-IN", now=107 + i * 0.2)
    assert len(out) == 2  # a new vehicle after the first one left


def test_no_plate_reporting_needs_the_vehicle_gate():
    det = LocalPlateDetector(finder=NoPlates(), reader=NeverReads())
    frame = np.zeros((120, 160, 3), np.uint8)
    assert sum(len(det.process_frame(frame, "C", now=i)) for i in range(30)) == 0


def test_plate_in_view_is_not_reported_as_no_plate():
    class OnePlate(NoPlates):
        def find(self, frame):
            return [PlateBox(10, 10, 90, 30, 0.9)]
    det = LocalPlateDetector(finder=OnePlate(), reader=NeverReads(), vehicle_detect=lambda f: (True, "car", 0.9))
    frame = np.zeros((120, 160, 3), np.uint8)
    assert sum(len(det.process_frame(frame, "C", now=i * 0.2)) for i in range(30)) == 0


# ---------- benchmark ----------
def test_benchmark_names():
    assert bench.truth_from_name(Path("KA05MG9999_night_2.jpg")) == "KA05MG9999"
    assert bench.condition_from_name(Path("KA05MG9999_night_2.jpg")) == "night"
    assert bench.condition_from_name(Path("KA05MG9999_a1b2c3d4.jpg")) == "unlabelled"


def test_benchmark_summary_and_baseline():
    rows = [
        {"truth": "KA05MG9999", "got": "KA05MG9999", "conf": 0.95, "condition": "day"},
        {"truth": "MH12AB1234", "got": "MH12AB1234", "conf": 0.90, "condition": "day"},
        {"truth": "DL3CAB1234", "got": "DL3CAB1284", "conf": 0.85, "condition": "night"},  # wrong but confident
        {"truth": "TN87C5106", "got": "TN87C5106", "conf": 0.40, "condition": "night"},    # right, goes to review
        {"truth": "NOTAPLATE", "got": "", "conf": 0.0, "condition": "night"},              # correctly nothing
    ]
    s = bench.summarize(rows, auto_conf=0.8)
    o = s["overall"]
    assert o["images"] == 5 and o["accuracy"] == 80.0
    assert o["auto_saved"] == 3 and o["precision"] == pytest.approx(66.67) and o["review_rate"] == 40.0
    assert o["wrong_auto"] == ["DL3CAB1234→DL3CAB1284"]
    assert s["by_condition"]["day"]["precision"] == 100.0
    assert bench.compare(s, s) == []
    better = {"overall": {**o, "precision": 99.9, "accuracy": 95.0}}
    assert len(bench.compare(s, better)) == 2


def test_benchmark_run_on_images(tmp_path):
    img = np.full((60, 200, 3), 255, np.uint8)
    for name in ("KA05MG9999_day.jpg", "MH12AB1234_night.jpg"):
        cv2.imwrite(str(tmp_path / name), img)
    reader = PlateReader(ensemble=False, engines=[FixedEngine("fixed", "KA05MG9999", 0.97)])
    rows = bench.run(sorted(tmp_path.glob("*.jpg")), finder=None, engines={}, combined=reader)
    s = bench.summarize(rows, 0.8)
    assert s["overall"]["accuracy"] == 50.0 and s["overall"]["precision"] == 50.0
