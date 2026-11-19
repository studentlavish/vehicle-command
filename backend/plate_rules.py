"""Indian number-plate rules: normalisation, look-alike correction, validation.

Shared by the backend (server.py / plate_pipeline.py) and the showroom
camera agent (local_agent/plate_detector.py) — keep both copies identical.

Supported formats
-----------------
* Standard:  SS DD LLL NNNN  e.g. KA05MG9999, DL3CAB1234, MH12AB1234, UP21C4567
    SS   = state / UT code (validated against the official list)
    DD   = district / RTO code, 1–2 digits
    LLL  = series, 0–3 letters
    NNNN = registration number, exactly 4 digits on OCR paths
* Bharat series:  YY BH NNNN LL  e.g. 22BH1234AA (always two trailing letters)

Look-alike correction
---------------------
OCR engines routinely confuse O/0, I/1, Z/2, S/5, B/8, G/6 … Because every
position in an Indian plate is either *always a letter* or *always a digit*,
we can correct those confusions by position — but only when the corrected
string then passes full validation. Nothing is "guessed": if a string can't
be made valid, it's rejected.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TG", "TN", "TR", "TS",
    "UA", "UK", "UP", "WB",
}

# Characters that look like a digit → the digit, and vice versa.
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "J": "1", "T": "7",
             "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4"}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "4": "A", "7": "T", "3": "B"}

_STANDARD_RE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$")
_BH_RE = re.compile(r"^(\d{2})BH(\d{4})([A-Z]{2})$")


def clean(raw: Optional[str]) -> str:
    """Uppercase and keep only A–Z / 0–9."""
    return "".join(c for c in (raw or "").upper() if c.isalnum())


def _digits(s: str) -> Optional[str]:
    out = []
    for c in s:
        if c.isdigit():
            out.append(c)
        elif c in _TO_DIGIT:
            out.append(_TO_DIGIT[c])
        else:
            return None
    return "".join(out)


def _letters(s: str) -> Optional[str]:
    out = []
    for c in s:
        if c.isalpha():
            out.append(c)
        elif c in _TO_LETTER:
            out.append(_TO_LETTER[c])
        else:
            return None
    return "".join(out)


def is_valid(plate: Optional[str]) -> bool:
    """True if `plate` (already cleaned) is a structurally valid Indian plate."""
    p = clean(plate)
    m = _STANDARD_RE.match(p)
    if m:
        return m.group(1) in STATE_CODES and int(m.group(2)) > 0
    return bool(_BH_RE.match(p))


def _try_standard(s: str) -> Optional[str]:
    if not 6 <= len(s) <= 11:
        return None
    state = _letters(s[:2])
    number = _digits(s[-4:])
    if not state or not number or state not in STATE_CODES:
        return None
    rest = s[2:-4]
    # District is 1–2 digits; prefer 2 (most common), then 1.
    for k in (2, 1):
        if len(rest) < k:
            continue
        district = _digits(rest[:k])
        series = _letters(rest[k:]) if rest[k:] else ""
        if district and series is not None and len(series) <= 3 and int(district) > 0:
            cand = f"{state}{district}{series}{number}"
            if _STANDARD_RE.match(cand):
                return cand
    return None


def _try_bh(s: str) -> Optional[str]:
    if len(s) != 10:
        return None
    yy = _digits(s[:2])
    bh = _letters(s[2:4])
    num = _digits(s[4:8])
    tail = _letters(s[8:])
    if yy and bh == "BH" and num and tail:
        cand = f"{yy}BH{num}{tail}"
        return cand if _BH_RE.match(cand) else None
    return None


def correct(raw: Optional[str]) -> Tuple[Optional[str], bool]:
    """Return (valid_plate, was_corrected). (None, False) if it can't be made valid.

    Also tries trimming one stray leading/trailing character, which OCR often
    picks up from the plate border or the "IND" hologram.
    """
    s = clean(raw)
    if not s:
        return None, False
    if is_valid(s):
        return s, False
    candidates = [s]
    if s.startswith("IND"):
        candidates.append(s[3:])
    if len(s) > 6:
        candidates += [s[1:], s[:-1]]
    for c in candidates:
        for fn in (_try_standard, _try_bh):
            fixed = fn(c)
            if fixed:
                return fixed, fixed != s
    return None, False


def extract(text: Optional[str]) -> Tuple[Optional[str], bool]:
    """Find the best valid plate anywhere in free OCR / LLM text."""
    if not text:
        return None, False
    whole, fixed = correct(text)
    if whole:
        return whole, fixed
    tokens = re.split(r"[^A-Za-z0-9]+", text.upper())
    # Plates are often split across tokens ("KA 05 MG 9999") — try joined windows.
    for size in (4, 3, 2, 1, 5):
        for i in range(0, max(0, len(tokens) - size + 1)):
            plate, fixed = correct("".join(tokens[i:i + size]))
            if plate:
                return plate, fixed
    return None, False


def distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings only)."""
    a, b = clean(a), clean(b)
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
