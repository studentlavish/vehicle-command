# Camera agent with offline number-plate recognition (same steps as local_agent/install_ocr.sh).
# Reads network cameras (IP Webcam / RTSP), finds plates, and sends them to the backend.
# Build from the repository root:
#   docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml build agent
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# libglib/libgomp for OpenCV + PaddlePaddle; git/curl to fetch the OCR models.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/local_agent
COPY local_agent/requirements-base.txt local_agent/requirements-detection.txt ./
# setuptools: the Awiros reader (via PaddleOCR) imports it, and Python 3.12 no longer ships it.
RUN pip install -r requirements-base.txt && pip install -r requirements-detection.txt setuptools

# PaddleOCR model code (pinned to the commit tested with this agent) + Awiros Indian-plate model.
ARG PADDLEOCR_COMMIT=dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf
RUN git clone --filter=blob:none --no-checkout https://github.com/PaddlePaddle/PaddleOCR.git models/PaddleOCR \
 && git -C models/PaddleOCR checkout -q "$PADDLEOCR_COMMIT" \
 && rm -rf models/PaddleOCR/.git
RUN mkdir -p models/awiros && cd models/awiros \
 && for f in model.safetensors en_dict.txt LICENSE; do \
      curl -fL --retry 3 -o "$f" "https://huggingface.co/Awiros/anpr-ocr/resolve/main/$f" \
      || curl -fL --retry 3 -o "$f" "https://huggingface.co/surendran0m07/anpr-ocr/resolve/main/$f"; \
    done

COPY local_agent/ .
RUN useradd --system --uid 10001 --create-home app \
 && mkdir -p /data/logs \
 && chmod -R a+rX /app/local_agent && chown -R app /data
USER app
# Plate-finder + fast-plate-ocr models, downloaded on the host first because some networks block
# GitHub's release file host from inside Docker. Fetch them with:  deploy/fetch_agent_models.sh
COPY --chown=app deploy/agent-models/ /home/app/.cache/
ENV AGENT_LOG_DIR=/data/logs OUTBOX_PATH=/data/outbox.sqlite3 ENABLE_LOCAL_DETECTION=true

# Load every model once so the small ONNX models are downloaded into the image, not at start-up.
# Retried because the model host sometimes drops connections; if it still fails they download on first start.
RUN for i in 1 2 3; do \
      python -c "from plate_ocr import PlateFinder, PlateReader; PlateFinder(); print('reader:', PlateReader().description)" && break; \
      echo "model download failed (attempt $i), retrying"; sleep 5; \
    done || true

CMD ["python", "agent.py"]
