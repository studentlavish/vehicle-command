"""Multi-plate tracking in LocalPlateDetector, with stand-in finder/reader."""
import numpy as np

import plate_detector
from plate_detector import LocalPlateDetector, _match_score
from plate_ocr import PlateBox

FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class ScriptedFinder:
    """Returns the boxes scripted for each successive frame. Box crops carry a marker
    pixel value so the reader knows which plate it is looking at."""
    model = "scripted"

    def __init__(self, frames):
        self.frames = list(frames)

    def find(self, frame):
        return self.frames.pop(0) if self.frames else []

    @staticmethod
    def crop(frame, box):
        return np.full((20, 80, 3), int(box.conf * 100) % 256, dtype=np.uint8)


class MarkerReader:
    description = "marker"

    def __init__(self, plates):
        self.plates = plates  # marker -> plate text

    def read(self, crop):
        plate = self.plates.get(int(crop[0, 0, 0]))
        return (plate, 0.95, "marker") if plate else None


def box(x, y, marker):
    # conf doubles as the marker (0.11 → 11); still a valid confidence
    return PlateBox(x, y, x + 100, y + 25, marker / 100)


def run(det, n_frames, cam="CAM-01", t0=1000.0, dt=0.2):
    out = []
    for i in range(n_frames):
        out += det.process_frame(FRAME, cam, now=t0 + i * dt)
    return out


def test_two_cars_in_one_frame_are_read_separately():
    frames = [[box(50, 300, 11), box(400, 300, 22)]] * 3
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999", 22: "MH12AB1234"}))
    found = run(det, 3)
    assert sorted(d.plate for d in found) == ["KA05MG9999", "MH12AB1234"]
    assert all(d.confidence == "high" and d.reads == 3 for d in found)


def test_moving_car_stays_one_track():
    frames = [[box(50 + 60 * i, 300, 11)] for i in range(5)]
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999"}))
    found = run(det, 5)
    assert [d.plate for d in found] == ["KA05MG9999"]


def test_car_that_stays_in_view_is_reported_once():
    frames = [[box(100, 300, 11)]] * 12
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999"}))
    assert len(run(det, 12)) == 1
    assert det.active_tracks("CAM-01") == 1


def test_brief_sighting_is_finalised_low_when_car_leaves():
    frames = [[box(100, 300, 11)], [], [], []]
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999"}))
    found = run(det, 4)
    assert len(found) == 1 and found[0].confidence == "low" and found[0].reads == 1
    assert det.active_tracks("CAM-01") == 0


def test_cameras_are_tracked_independently():
    frames = [[box(100, 300, 11)]] * 6
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999"}))
    for i in range(3):
        det.process_frame(FRAME, "GATE-IN", now=1000 + i)
        det.process_frame(FRAME, "GATE-OUT", now=1000 + i)
    assert det.active_tracks("GATE-IN") == 1 and det.active_tracks("GATE-OUT") == 1


def test_detection_carries_first_seen_time():
    frames = [[box(100, 300, 11)]] * 3
    det = LocalPlateDetector(finder=ScriptedFinder(frames), reader=MarkerReader({11: "KA05MG9999"}))
    assert run(det, 3, t0=5000.0)[0].seen_at == 5000.0


def test_match_score():
    a = (100, 300, 200, 325)
    assert _match_score(a, a) > 1.0                            # overlapping
    assert 0 < _match_score(a, (300, 300, 400, 325)) < 1.0     # two widths away
    assert _match_score(a, (100 + 100 * int(plate_detector.TRACK_MAX_JUMP + 1), 300, 600, 325)) == 0
