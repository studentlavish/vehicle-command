"""Logging and error tracking for the backend.

LOG_FORMAT=json   one JSON object per line (for log collectors); default is plain text
LOG_LEVEL         INFO by default
SENTRY_DSN        send unhandled errors to Sentry (needs the sentry-sdk package)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def configure() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _init_sentry()


def _init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("rdx").warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return
    sentry_sdk.init(dsn=dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                    traces_sample_rate=0.0, send_default_pii=False)
