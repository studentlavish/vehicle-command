"""Multi-read voting in plate_detector."""
from plate_detector import _Read, vote


def _r(plate, conf=0.9):
    return _Read(plate=plate, conf=conf, crop=b"", plate_crop=b"", vehicle_class="car", vehicle_conf=1.0)


def test_unanimous_reads_win_with_full_support():
    winner, support, distinct = vote([_r("KA05MG9999")] * 3)
    assert winner == "KA05MG9999"
    assert support == 1.0
    assert distinct == ["KA05MG9999"]


def test_character_vote_repairs_single_slip():
    winner, support, _ = vote([_r("KA05MG9999"), _r("KA05MG9998"), _r("KA05MG9999")])
    assert winner == "KA05MG9999"
    assert support > 0.6


def test_split_reads_have_low_support():
    winner, support, distinct = vote([_r("KA05MG9999"), _r("MH12AB1234"), _r("DL3CAB1234")])
    assert support < 0.6
    assert len(distinct) == 3


def test_empty_vote():
    assert vote([]) == (None, 0.0, [])
