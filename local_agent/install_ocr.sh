#!/usr/bin/env bash
# Installs the free, offline number-plate recognition stack for the camera agent:
#   • open-image-models plate finder (MIT)        — ~8 MB, downloaded on first run
#   • Awiros ANPR-OCR Indian-plate reader (Apache-2.0) — ~150 MB from Hugging Face
#   • fast-plate-ocr cross-check reader (MIT)    — ~5 MB, downloaded on first run
#   • PaddlePaddle runtime + PaddleOCR code (Apache-2.0)
# Re-running is safe; finished steps are skipped.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
MODELS="$HERE/models"
PADDLEOCR_COMMIT="dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf"   # version tested with this agent
say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

PY=""
for c in python3.12 python3.13 python3.11 python3.10; do command -v "$c" >/dev/null 2>&1 && { PY="$(command -v $c)"; break; }; done
[ -n "$PY" ] || { echo "Python 3.10–3.13 is required (brew install python@3.12)"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip wheel >/dev/null

say "Installing agent + OCR packages (PaddlePaddle is a large download)"
.venv/bin/pip install -r requirements-base.txt
.venv/bin/pip install -r requirements-detection.txt

mkdir -p "$MODELS/awiros"
if [ ! -f "$MODELS/PaddleOCR/ppocr/__init__.py" ]; then
  say "Fetching PaddleOCR model code"
  rm -rf "$MODELS/PaddleOCR"
  git clone --filter=blob:none --no-checkout https://github.com/PaddlePaddle/PaddleOCR.git "$MODELS/PaddleOCR"
  git -C "$MODELS/PaddleOCR" checkout -q "$PADDLEOCR_COMMIT"
fi

if [ ! -s "$MODELS/awiros/model.safetensors" ]; then
  say "Downloading Awiros ANPR-OCR model (~150 MB)"
  for repo in Awiros/anpr-ocr surendran0m07/anpr-ocr; do
    ok=1
    for f in model.safetensors en_dict.txt LICENSE; do
      curl -fL --retry 3 -o "$MODELS/awiros/$f.part" "https://huggingface.co/$repo/resolve/main/$f" || { ok=0; break; }
      mv "$MODELS/awiros/$f.part" "$MODELS/awiros/$f"
    done
    [ $ok = 1 ] && { echo "   from huggingface.co/$repo"; break; }
  done
  [ -s "$MODELS/awiros/model.safetensors" ] || echo "   ⚠ Could not download Awiros — the agent will use fast-plate-ocr instead."
fi

say "Loading every model once (downloads the small ONNX models)"
.venv/bin/python - <<'PY'
import logging; logging.basicConfig(level=logging.INFO, format="   %(message)s")
from plate_ocr import PlateFinder, PlateReader
PlateFinder(); r = PlateReader()
print("\n   Plate reader:", r.description)
PY

if [ -x "$HERE/../backend/.venv/bin/pip" ]; then
  say "Installing the same readers for the dashboard's \"Scan plate\" upload (backend)"
  "$HERE/../backend/.venv/bin/pip" install -r requirements-detection.txt
fi

say "Done. Restart start.sh so the backend picks up the readers."
echo "   For live cameras: add ENABLE_LOCAL_DETECTION=true to local_agent/.env, then start the agent."
echo "   Measure accuracy on your own photos:  .venv/bin/python benchmark_ocr.py <folder>"
