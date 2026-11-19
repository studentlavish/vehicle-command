"""Number-plate finding and reading engines for the camera agent.

All engines here are free and run fully offline:

  Plate finder
    * open-image-models YOLOv9 licence-plate detector (MIT, ONNX)
      — downloaded automatically on first use (~8 MB).

  Plate readers (OCR). With OCR_ENGINE=auto (default):
    1. fast-plate-ocr   (MIT) — compact global plate model (ONNX, ~5 MB, ~25 ms).
       Reads every plate.
    2. Awiros ANPR-OCR  (Apache-2.0) — PP-OCRv5 model trained on Indian plates
       (~150 MB, 0.5–4 s per plate on CPU). Consulted only when the first
       reader is unsure or its answer fails the Indian-format check.
    3. EasyOCR          (Apache-2.0) — general-purpose text OCR, last resort.
  OCR_ENGINE=awiros makes Awiros the primary reader instead — measure both on
  your own images with benchmark_ocr.py before switching.

Cross-check (OCR_ENSEMBLE=true, default): agreement raises confidence;
disagreement lowers it so the read goes to the Review queue instead of being
saved automatically.

Environment variables
    OCR_ENGINE          auto | awiros | fastplate | easyocr   (default: auto)
    OCR_ENSEMBLE        true/false — consult a second reader when unsure (default: true)
    OCR_SURE            primary confidence above which the second reader is skipped (0.90)
    AWIROS_DIR          folder holding model.safetensors + en_dict.txt
                        (default: local_agent/models/awiros)
    PADDLEOCR_DIR       PaddleOCR repo checkout (default: local_agent/models/PaddleOCR)
    FASTPLATE_MODEL     fast-plate-ocr hub model (default: cct-s-v2-global-model)
    PLATE_DETECTOR      open-image-models detector (default: yolo-v9-t-640-license-plate-end2end)
    PLATE_DETECTOR_CONF minimum plate-box confidence (default: 0.35)
"""
from __future__ import annotations

import copy
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

import plate_rules

log = logging.getLogger("vashu_agent.ocr")

_HERE = Path(__file__).resolve().parent
AWIROS_DIR = Path(os.environ.get("AWIROS_DIR", _HERE / "models" / "awiros"))
PADDLEOCR_DIR = Path(os.environ.get("PADDLEOCR_DIR", _HERE / "models" / "PaddleOCR"))
FASTPLATE_MODEL = os.environ.get("FASTPLATE_MODEL", "cct-s-v2-global-model")
PLATE_DETECTOR = os.environ.get("PLATE_DETECTOR", "yolo-v9-t-640-license-plate-end2end")
PLATE_DETECTOR_CONF = float(os.environ.get("PLATE_DETECTOR_CONF", "0.35"))
OCR_ENGINE = os.environ.get("OCR_ENGINE", "auto").strip().lower()
OCR_ENSEMBLE = os.environ.get("OCR_ENSEMBLE", "true").strip().lower() in ("1", "true", "yes", "on")
OCR_SURE = float(os.environ.get("OCR_SURE", "0.90"))


@dataclass
class OcrRead:
    text: str          # raw text from the engine (A–Z0–9 only)
    conf: float        # 0..1
    engine: str


@dataclass
class PlateBox:
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float


def _clean(text: str) -> str:
    return "".join(c for c in (text or "").upper() if c.isalnum())


# ---------------------------------------------------------------------------
# Plate finder
# ---------------------------------------------------------------------------
class PlateFinder:
    """Locates number plates in a full camera frame (open-image-models, MIT)."""

    name = "open-image-models"

    def __init__(self, model: str = PLATE_DETECTOR, conf: float = PLATE_DETECTOR_CONF) -> None:
        from open_image_models import create_detector
        self._det = create_detector(model, conf_thresh=conf)
        self.model = model
        log.info("[OCR] plate finder ready: %s (conf ≥ %.2f)", model, conf)

    def find(self, frame_bgr: np.ndarray) -> List[PlateBox]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        H, W = frame_bgr.shape[:2]
        out: List[PlateBox] = []
        for d in self._det.predict(frame_bgr) or []:
            b = d.bounding_box
            x1, y1, x2, y2 = max(0, int(b.x1)), max(0, int(b.y1)), min(W, int(b.x2)), min(H, int(b.y2))
            if x2 - x1 >= 8 and y2 - y1 >= 4:
                out.append(PlateBox(x1, y1, x2, y2, float(d.confidence)))
        out.sort(key=lambda p: p.conf * (p.x2 - p.x1), reverse=True)  # confident + large first
        return out

    @staticmethod
    def crop(frame_bgr: np.ndarray, box: PlateBox, pad: float = 0.06) -> np.ndarray:
        H, W = frame_bgr.shape[:2]
        w, h = box.x2 - box.x1, box.y2 - box.y1
        px, py = int(w * pad), int(h * pad * 2)
        return frame_bgr[max(0, box.y1 - py):min(H, box.y2 + py), max(0, box.x1 - px):min(W, box.x2 + px)].copy()


# ---------------------------------------------------------------------------
# OCR engines
# ---------------------------------------------------------------------------
class AwirosEngine:
    """Awiros ANPR-OCR (PP-OCRv5 rec, Indian plates). Mirrors the model card's test.py."""

    name = "awiros"
    _CONFIG = {
        "model_type": "rec",
        "algorithm": "SVTR_HGNet",
        "Transform": None,
        "Backbone": {"name": "PPHGNetV2_B4", "text_rec": True},
        "Head": {
            "name": "MultiHead",
            "out_channels_list": {"CTCLabelDecode": 64, "NRTRLabelDecode": 67},
            "head_list": [
                {"CTCHead": {
                    "Neck": {"name": "svtr", "dims": 120, "depth": 2, "hidden_dims": 120,
                             "kernel_size": [1, 3], "use_guide": True},
                    "Head": {"fc_decay": 1e-05},
                }},
                {"NRTRHead": {"nrtr_dim": 384, "max_text_length": 25}},
            ],
        },
    }
    _SHAPE = (3, 48, 320)

    def __init__(self, model_dir: Path = AWIROS_DIR, paddleocr_dir: Path = PADDLEOCR_DIR) -> None:
        weights = model_dir / "model.safetensors"
        dict_path = model_dir / "en_dict.txt"
        if not weights.is_file() or not dict_path.is_file():
            raise FileNotFoundError(f"Awiros model files not found in {model_dir} (run install_ocr.sh)")
        if not (paddleocr_dir / "ppocr" / "__init__.py").is_file():
            raise FileNotFoundError(f"PaddleOCR checkout not found at {paddleocr_dir} (run install_ocr.sh)")
        if str(paddleocr_dir) not in sys.path:
            sys.path.insert(0, str(paddleocr_dir))
        import paddle
        from ppocr.modeling.architectures import build_model
        from ppocr.postprocess import build_post_process
        from safetensors.numpy import load_file

        paddle.set_device("gpu" if paddle.is_compiled_with_cuda() else "cpu")
        self._paddle = paddle
        self._post = build_post_process({
            "name": "CTCLabelDecode", "character_dict_path": str(dict_path), "use_space_char": True,
        })
        self._model = build_model(copy.deepcopy(self._CONFIG))
        self._model.eval()
        state = {k: paddle.to_tensor(v) for k, v in load_file(str(weights)).items()}
        missing, unexpected = self._model.set_state_dict(state) or ([], [])
        if missing:
            log.warning("[OCR] awiros: %d weights missing from checkpoint", len(missing))
        log.info("[OCR] Awiros ANPR-OCR ready (%s)", "GPU" if paddle.is_compiled_with_cuda() else "CPU")

    def _prep(self, img_bgr: np.ndarray) -> np.ndarray:
        _, h, w = self._SHAPE
        ih, iw = img_bgr.shape[:2]
        new_w = min(int(iw * h / max(ih, 1)), w)
        resized = cv2.resize(img_bgr, (max(new_w, 1), h))
        if new_w < w:
            padded = np.zeros((h, w, 3), dtype=np.uint8)
            padded[:, :new_w] = resized
            resized = padded
        x = resized.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        return x.transpose((2, 0, 1))

    def read(self, plate_bgr: np.ndarray) -> Optional[OcrRead]:
        if plate_bgr.ndim == 2:
            plate_bgr = cv2.cvtColor(plate_bgr, cv2.COLOR_GRAY2BGR)
        h, w = plate_bgr.shape[:2]
        if w < h * 2.8:
            # Squarish crop → likely a two-row plate. Awiros squeezes everything
            # into a 48-px strip, so also try the two rows placed side by side
            # and keep whichever gives a valid plate with higher confidence.
            mid = h // 2
            side = np.hstack([plate_bgr[:mid], cv2.resize(plate_bgr[mid:], (w, mid))])
            reads = [r for r in (self._read_one(plate_bgr), self._read_one(side)) if r]
            valid = [r for r in reads if plate_rules.is_valid(plate_rules.correct(r.text)[0])]
            pool = valid or reads
            return max(pool, key=lambda r: r.conf) if pool else None
        return self._read_one(plate_bgr)

    def _read_one(self, plate_bgr: np.ndarray) -> Optional[OcrRead]:
        x = self._paddle.to_tensor(np.expand_dims(self._prep(plate_bgr), 0))
        with self._paddle.no_grad():
            preds = self._model(x)
        if isinstance(preds, dict):
            preds = preds.get("ctc", next(iter(preds.values())))
        elif isinstance(preds, (list, tuple)):
            preds = preds[0]
        res = self._post(preds.numpy())
        if not res:
            return None
        text, conf = res[0]
        text = _clean(text)
        return OcrRead(text, float(conf), self.name) if text else None


class FastPlateEngine:
    """fast-plate-ocr global model (ONNX, MIT)."""

    name = "fast-plate-ocr"

    def __init__(self, model: str = FASTPLATE_MODEL) -> None:
        from fast_plate_ocr import LicensePlateRecognizer
        self._m = LicensePlateRecognizer(model, device="auto")
        self._rgb = getattr(self._m.config, "image_color_mode", "rgb") == "rgb"
        log.info("[OCR] fast-plate-ocr ready (%s)", model)

    def read(self, plate_bgr: np.ndarray) -> Optional[OcrRead]:
        if plate_bgr.ndim == 2:
            img = cv2.cvtColor(plate_bgr, cv2.COLOR_GRAY2RGB) if self._rgb else plate_bgr
        else:
            img = cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2RGB if self._rgb else cv2.COLOR_BGR2GRAY)
        pred = self._m.run(img, return_confidence=True)[0]
        text = _clean(pred.plate)
        if not text:
            return None
        probs = [float(p) for p in (pred.char_probs if pred.char_probs is not None else [])][: len(text)]
        conf = float(np.mean(probs)) * 0.5 + float(min(probs)) * 0.5 if probs else 0.5
        return OcrRead(text, conf, self.name)


class EasyOcrEngine:
    """EasyOCR — general text OCR, used only when nothing better is installed."""

    name = "easyocr"

    def __init__(self) -> None:
        import easyocr
        langs = [s.strip() for s in os.environ.get("EASYOCR_LANGS", "en").split(",") if s.strip()]
        gpu = os.environ.get("EASYOCR_GPU", "0").strip().lower() in ("1", "true", "yes", "on")
        self._r = easyocr.Reader(langs, gpu=gpu, verbose=False)
        log.info("[OCR] EasyOCR ready (fallback)")

    def read(self, plate_bgr: np.ndarray) -> Optional[OcrRead]:
        gray = cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2GRAY) if plate_bgr.ndim == 3 else plate_bgr
        h = gray.shape[0]
        if 0 < h < 60:
            gray = cv2.resize(gray, None, fx=60.0 / h, fy=60.0 / h, interpolation=cv2.INTER_CUBIC)
        rows = self._r.readtext(gray, detail=1, paragraph=False,
                                allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        parsed = []
        for bbox, text, conf in rows or []:
            parsed.append((min(p[1] for p in bbox), min(p[0] for p in bbox), _clean(text), float(conf)))
        if not parsed:
            return None
        parsed.sort(key=lambda r: (round(r[0] / 20), r[1]))  # two-line plates: top line first
        text = "".join(t for *_, t, _ in parsed)
        conf = sum(c for *_, c in parsed) / len(parsed)
        return OcrRead(text, conf, self.name) if text else None


_ENGINE_CLASSES = {"awiros": AwirosEngine, "fastplate": FastPlateEngine, "easyocr": EasyOcrEngine}
_AUTO_ORDER = ["fastplate", "awiros", "easyocr"]
_PRIMARY_FIRST = {"awiros": ["awiros", "fastplate", "easyocr"], "fastplate": ["fastplate", "awiros", "easyocr"],
                  "easyocr": ["easyocr"]}


class PlateReader:
    """Loads the best available OCR engine(s) and reads plate crops."""

    def __init__(self, engine: str = OCR_ENGINE, ensemble: bool = OCR_ENSEMBLE, engines=None) -> None:
        """`engines`: ready engine objects (primary first) instead of loading the installed ones."""
        order = [] if engines is not None else (_AUTO_ORDER if engine == "auto" else _PRIMARY_FIRST.get(engine, [engine]))
        self.engines = list(engines or [])[: 2 if ensemble else 1]
        for key in order:
            try:
                self.engines.append(_ENGINE_CLASSES[key]())
            except Exception as e:  # missing package / model files
                log.warning("[OCR] %s unavailable: %s", key, e)
            if len(self.engines) >= (2 if ensemble else 1):
                break
        if not self.engines:
            raise RuntimeError("No OCR engine available — run install_ocr.sh")
        self.primary = self.engines[0]
        self.checker = self.engines[1] if ensemble and len(self.engines) > 1 else None
        log.info("[OCR] primary=%s cross-check=%s", self.primary.name, self.checker.name if self.checker else "off")

    @property
    def description(self) -> str:
        return self.primary.name + (f" (+ {self.checker.name} when unsure)" if self.checker else "")

    def read(self, plate_bgr: np.ndarray) -> Optional[Tuple[str, float, str]]:
        """Return (valid_plate, confidence, engine_note) or None if no valid plate."""
        if plate_bgr is None or plate_bgr.size == 0:
            return None
        first = self._safe(self.primary, plate_bgr)
        plate1 = self._fixed(first)
        if plate1 and (not self.checker or first.conf >= OCR_SURE):
            return plate1, first.conf, first.engine          # confident & valid — no need to ask again
        if not self.checker:
            return None

        second = self._safe(self.checker, plate_bgr)
        plate2 = self._fixed(second)
        if plate1 and plate2 and plate1 == plate2:
            # Two independent models agree — strongest evidence we can get offline.
            return plate1, min(1.0, max(first.conf, second.conf) * 0.6 + 0.4), f"{first.engine}+{second.engine} agree"
        if plate1 and plate2:
            # Disagreement: take the more confident reader, but cap confidence so
            # the multi-frame vote / Review queue makes the final call.
            near = plate_rules.distance(plate1, plate2) <= 1
            win, lose, wp, lp = (first, second, plate1, plate2) if first.conf >= second.conf else (second, first, plate2, plate1)
            return wp, win.conf * (0.75 if near else 0.5), f"{win.engine} (≠ {lose.engine}: {lp})"
        if plate1:
            return plate1, first.conf * 0.85, first.engine
        if plate2:
            return plate2, second.conf * 0.7, second.engine
        return None

    @staticmethod
    def _fixed(read: Optional[OcrRead]) -> Optional[str]:
        """Valid plate from a raw read; look-alike fixes lower read.conf (risky ones a lot)."""
        if not read:
            return None
        plate, factor = plate_rules.correction_factor(read.text)
        if plate:
            read.conf *= factor
        return plate

    @staticmethod
    def _safe(engine, img) -> Optional[OcrRead]:
        try:
            return engine.read(img)
        except Exception as e:
            log.warning("[OCR] %s failed on a crop: %s", engine.name, e)
            return None
