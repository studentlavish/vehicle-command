"""Meta WhatsApp Cloud API service layer.

Primary WhatsApp provider. When META_* credentials are absent the caller
falls back to the existing Twilio sender / dry-run path. Secrets live only
in backend/.env — they are never logged and never sent to the frontend.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("rdx.whatsapp_meta")

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "")
META_WABA_ID = os.environ.get("META_WABA_ID", "")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION") or "v26.0"

# Statuses worth a single automatic retry (transient Meta-side failures).
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class WhatsAppMetaError(RuntimeError):
    """Raised on Meta send failure. Message is sanitized — never contains the token."""


def meta_whatsapp_enabled() -> bool:
    """Meta is the active provider only when the mandatory credentials exist."""
    return bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID)


def _sanitize_meta_response(data: Any) -> Dict[str, Any]:
    """Keep only safe response fields — no request data, no headers, no tokens."""
    if not isinstance(data, dict):
        return {}
    safe: Dict[str, Any] = {}
    for k in ("messaging_product", "contacts", "messages"):
        if k in data:
            safe[k] = data[k]
    err = data.get("error")
    if isinstance(err, dict):
        safe["error"] = {k: err.get(k) for k in ("message", "code", "type", "error_subcode") if k in err}
    return safe


async def send_text(db, to: str, body: str, by: str = "system", context: str = "manual") -> Dict[str, Any]:
    """Send a freeform WhatsApp text via the Meta Cloud API.

    Logs success AND failure to `whatsapp_log` (provider/status/error fields)
    without persisting the access token. Retries once on transient 429/5xx.
    Raises WhatsAppMetaError on final failure.
    """
    # Auth travels in the header only — the URL contains no secret material.
    url = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}

    last_net_error: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            last_net_error = e
            if attempt == 1:
                continue
            break
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:300]}
        safe = _sanitize_meta_response(data)
        if not resp.is_error:
            msg_id = ""
            try:
                msg_id = (safe.get("messages") or [{}])[0].get("id", "")
            except Exception:
                pass
            await _log(db, {
                "direction": "outbound", "provider": "meta", "status": "sent",
                "to": to, "body": body, "by": by, "context": context,
                "message_id": msg_id, "http_status": resp.status_code,
            })
            return {"provider": "meta", "delivered": True, "message_id": msg_id}
        err_msg = (safe.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        if resp.status_code in _RETRYABLE and attempt == 1:
            continue
        await _log(db, {
            "direction": "outbound", "provider": "meta", "status": "failed",
            "to": to, "body": body, "by": by, "context": context,
            "http_status": resp.status_code, "error": str(err_msg)[:300],
        })
        raise WhatsAppMetaError(f"Meta send failed ({resp.status_code}): {err_msg}")

    await _log(db, {
        "direction": "outbound", "provider": "meta", "status": "failed",
        "to": to, "body": body, "by": by, "context": context,
        "error": f"network error: {type(last_net_error).__name__}",
    })
    raise WhatsAppMetaError(f"Meta send failed: {type(last_net_error).__name__}")


async def _log(db, record: Dict[str, Any]) -> None:
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    try:
        await db.whatsapp_log.insert_one(record)
    except Exception:
        logger.warning("whatsapp_log insert failed")


def verify_webhook_challenge(mode: Optional[str], token: Optional[str], challenge: Optional[str]) -> Optional[str]:
    """Return the challenge when Meta's GET verification handshake is valid."""
    if mode == "subscribe" and META_VERIFY_TOKEN and token and hmac.compare_digest(token, META_VERIFY_TOKEN):
        return challenge or ""
    return None


def verify_signature(raw: bytes, header: Optional[str]) -> bool:
    """X-Hub-Signature-256 check. Enforced only when META_APP_SECRET is configured."""
    if not META_APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(META_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:])
