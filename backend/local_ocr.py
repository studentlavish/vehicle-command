"""Free, offline plate reading for the dashboard's "Scan plate" photo upload.

Reuses the camera agent's engines (local_agent/plate_ocr.py):
plate finder (open-image-models) → Awiros Indian-plate OCR, cross-checked by
fast-plate-ocr. Models are shared with the agent (local_agent/models/), so
running local_agent/install_ocr.sh once sets up both.

Packages must also be installed in the backend's virtualenv:
    backend/.venv/bin/pip install -r local_agent/requirements-detection.txt
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("rdx.local_ocr")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "local_agent"
os.environ.setdefault("AWIROS_DIR", str(_AGENT_DIR / "models" / "awiros"))
os.environ.setdefault("PADDLEOCR_DIR", str(_AGENT_DIR / "models" / "PaddleOCR"))

_lock = threading.Lock()
_engines: Optional[tuple] = None
_error: Optional[str] = None

INSTALL_HINT = (
    "Offline plate reading isn't installed. Run local_agent/install_ocr.sh, then "
    "backend/.venv/bin/pip install -r local_agent/requirements-detection.txt and restart the backend."
)


def _load() -> Optional[tuple]:
    global _engines, _error
    with _lock:
        if _engines is not None or _error is not None:
            return _engines
        try:
            if str(_AGENT_DIR) not in sys.path:
                sys.path.append(str(_AGENT_DIR))
            from plate_ocr import PlateFinder, PlateReader
            _engines = (PlateFinder(), PlateReader())
            logger.info("[scan] offline plate reader ready: %s", _engines[1].description)
        except Exception as e:  # packages or models missing
            _error = f"{type(e).__name__}: {e}"
            logger.warning("[scan] offline plate reader unavailable — %s", _error)
        return _engines


def available() -> bool:
    return _load() is not None


def describe() -> str:
    eng = _load()
    return eng[1].description if eng else "unavailable"


def read_base64(b64: str) -> Dict[str, Any]:
    """Read the plate in a base64 photo. Blocking — call via asyncio.to_thread.

    Returns {"plate": str|None, "confidence": float, "engine": str, "plate_found": bool}.
    Raises RuntimeError(INSTALL_HINT) if the engines aren't installed.
    """
    eng = _load()
    if eng is None:
        raise RuntimeError(INSTALL_HINT)
    finder, reader = eng
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("That file isn't a readable image")

    boxes = finder.find(img)
    # Try the best detected plates; fall back to the whole photo for close-ups.
    crops = [finder.crop(img, b) for b in boxes[:3]] or [img]
    best = None
    for crop in crops:
        r = reader.read(crop)
        if r and (best is None or r[1] > best[1]):
            best = r
    if not best:
        return {"plate": None, "confidence": 0.0, "engine": reader.description, "plate_found": bool(boxes)}
    plate, conf, note = best
    return {"plate": plate, "confidence": float(conf), "engine": note, "plate_found": bool(boxes)}
