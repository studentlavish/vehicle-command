"""Indian plate normalisation / correction / validation."""
import pytest

import plate_rules


@pytest.mark.parametrize("plate", ["KA05MG9999", "DL3CAB1234", "MH12AB1234", "UP21C4567", "22BH1234AA", "TN87C5106"])
def test_valid_plates(plate):
    assert plate_rules.is_valid(plate)
    assert plate_rules.correct(plate) == (plate, False)


@pytest.mark.parametrize("plate", ["", "ABC", "XX05MG9999", "KA00MG9999", "KA05MG999", "22BH1234A", "1234567890"])
def test_invalid_plates(plate):
    assert not plate_rules.is_valid(plate)


@pytest.mark.parametrize("raw,expected", [
    ("KA O5 MG 9999", "KA05MG9999"),      # O→0 in the district
    ("KA05MG99S9", "KA05MG9959"),         # S→5 in the number
    ("MH12A81234", "MH12AB1234"),         # 8→B in the series
    ("IND KA05MG9999", "KA05MG9999"),     # hologram prefix trimmed
    ("ka-05-mg-9999", "KA05MG9999"),      # case + punctuation
])
def test_lookalike_correction(raw, expected):
    plate, corrected = plate_rules.correct(raw)
    assert plate == expected
    assert corrected is True or plate_rules.clean(raw) == expected


def test_uncorrectable_is_rejected():
    assert plate_rules.correct("HELLO WORLD") == (None, False)


def test_extract_from_free_text():
    assert plate_rules.extract("Plate: KA 05 MG 9999 (front)")[0] == "KA05MG9999"


def test_distance():
    assert plate_rules.distance("KA05MG9999", "KA05MG9999") == 0
    assert plate_rules.distance("KA05MG9999", "KA05MG9998") == 1
    assert plate_rules.distance("KA05MG9999", "KA05M9999") == 1
