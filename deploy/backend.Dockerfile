# Backend API (FastAPI + uvicorn). Build from the repository root:
#   docker compose -f deploy/docker-compose.yml build backend
FROM mongo:7.0 AS mongotools

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# libglib for OpenCV; Kerberos libs for the MongoDB Database Tools copied below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgssapi-krb5-2 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# mongodump / mongorestore for the nightly backup (fails the build if they can't run here).
COPY --from=mongotools /usr/bin/mongodump /usr/bin/mongorestore /usr/bin/
RUN mongodump --version && mongorestore --version

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .

RUN useradd --system --uid 10001 app && mkdir -p /data/snapshots /data/storage && chown -R app /data
USER app
ENV SNAPSHOTS_DIR=/data/snapshots STORAGE_DIR=/data/storage

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=4)"
# One worker on purpose: live camera frames and dashboard events are held in
# this process (see camera_manager.py / event_bus.py). Async I/O handles the
# showroom's load with a large margin (memory/capacity_report.md).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
