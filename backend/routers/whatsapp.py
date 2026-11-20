"""WhatsApp reports and Meta webhooks."""
from datetime import timedelta
from typing import Optional, Dict

import whatsapp_meta as wa_meta
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel


# Optional integrations
try:
    from twilio.rest import Client as TwilioClient
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False

from core import ROLE_ADMIN_MANAGER, TWILIO_FROM, TWILIO_SID, TWILIO_TOKEN, TWILIO_TO_DEFAULT, db, logger, now_utc, require_roles, start_of_day

router = APIRouter(prefix="/api")

# ===================== WhatsApp Reports =====================
class WhatsAppSendIn(BaseModel):
    to: Optional[str] = None  # override recipient; default from settings/env
    body: Optional[str] = None  # override body


async def _build_daily_summary_text() -> str:
    n = now_utc()
    today = start_of_day(n)
    end = today + timedelta(days=1)
    entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}})
    exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}})
    inside = await db.visit_sessions.count_documents({"status": "active"})
    total_masters = await db.vehicle_masters.count_documents({})

    # Top 3 busiest vehicles today
    pipe = [
        {"$match": {"entry_time": {"$gte": today.isoformat(), "$lt": end.isoformat()}}},
        {"$group": {"_id": "$vehicle_number", "visits": {"$sum": 1}}},
        {"$sort": {"visits": -1}},
        {"$limit": 3},
    ]
    top = await db.visit_sessions.aggregate(pipe).to_list(3)
    top_lines = "\n".join(f"  • {t['_id']}: {t['visits']} visits" for t in top) or "  (none)"

    return (
        f"🚗 *RDX Car Showroom — Daily Report*\n"
        f"📅 {today.strftime('%A, %d %b %Y')}\n\n"
        f"• Entries today:  *{entries}*\n"
        f"• Exits today:    *{exits}*\n"
        f"• Cars inside:    *{inside}*\n"
        f"• Total vehicles: *{total_masters}*\n\n"
        f"Top visitors today:\n{top_lines}\n\n"
        f"— sent by RDX AI"
    )


async def _twilio_config() -> Dict[str, str]:
    """Load Twilio config from settings collection, falling back to env vars."""
    doc = await db.settings.find_one({"key": "app"}) or {}
    return {
        "sid": doc.get("twilio_sid") or TWILIO_SID,
        "token": doc.get("twilio_token") or TWILIO_TOKEN,
        "from": doc.get("twilio_from") or TWILIO_FROM,
        "to": doc.get("twilio_to") or TWILIO_TO_DEFAULT,
    }


@router.post("/whatsapp/send-report")
async def send_whatsapp_report(payload: WhatsAppSendIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    cfg = await _twilio_config()
    body = payload.body or await _build_daily_summary_text()
    to = payload.to or cfg["to"]

    # Primary provider: Meta WhatsApp Cloud API (active when META_* env is configured)
    if wa_meta.meta_whatsapp_enabled():
        if not to:
            raise HTTPException(status_code=400, detail="Recipient number not configured (Settings → Admin WhatsApp To).")
        try:
            result = await wa_meta.send_text(db, to=to, body=body, by=user["email"], context="manual_report")
            return {"delivered": True, "mode": "live", "provider": "meta", "sid": result.get("message_id"), "preview": {"to": to, "body": body}}
        except wa_meta.WhatsAppMetaError as e:
            raise HTTPException(status_code=502, detail=str(e))

    if not (HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and to):
        # Dry-run mode — return preview so the UI can display it
        return {
            "delivered": False,
            "mode": "dry-run",
            "provider": "none",
            "reason": "WhatsApp credentials not configured (set META_ACCESS_TOKEN + META_PHONE_NUMBER_ID in backend/.env, or the legacy Twilio vars, plus the recipient number).",
            "preview": {"to": to or "(missing)", "body": body},
        }

    try:
        cli = TwilioClient(cfg["sid"], cfg["token"])
        message = cli.messages.create(from_=cfg["from"], to=to, body=body)
        await db.whatsapp_log.insert_one({
            "sid": message.sid,
            "to": to,
            "body": body,
            "provider": "twilio",
            "status": "sent",
            "direction": "outbound",
            "sent_at": now_utc().isoformat(),
            "by": user["email"],
        })
        return {"delivered": True, "mode": "live", "provider": "twilio", "sid": message.sid, "preview": {"to": to, "body": body}}
    except Exception as e:
        await db.whatsapp_log.insert_one({
            "to": to,
            "body": body,
            "provider": "twilio",
            "status": "failed",
            "direction": "outbound",
            "error": str(e)[:300],
            "sent_at": now_utc().isoformat(),
            "by": user["email"],
        })
        logger.exception("Twilio send failed")
        raise HTTPException(status_code=500, detail=f"WhatsApp send failed: {e}")


@router.get("/whatsapp/preview")
async def whatsapp_preview(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    cfg = await _twilio_config()
    body = await _build_daily_summary_text()
    meta_on = wa_meta.meta_whatsapp_enabled()
    twilio_on = bool(HAS_TWILIO and cfg["sid"] and cfg["token"] and cfg["from"] and cfg["to"])
    return {
        "configured": meta_on or twilio_on,
        "provider": "meta" if meta_on else ("twilio" if twilio_on else "none"),
        "to": cfg["to"] or "",
        "from_": cfg["from"] or "",
        "body": body,
    }


# ---------- Meta WhatsApp webhooks (verification + inbound) ----------
@router.get("/whatsapp/webhook")
async def whatsapp_webhook_verify(request: Request):
    q = request.query_params
    challenge = wa_meta.verify_webhook_challenge(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return Response(content=challenge, media_type="text/plain")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request):
    raw = await request.body()
    if not wa_meta.verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        payload = await request.json()  # body is cached; safe after signature check
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return {"ok": True, "ignored": True}
    stored = 0
    for entry in payload.get("entry", []) or []:
        if wa_meta.META_WABA_ID and entry.get("id") != wa_meta.META_WABA_ID:
            continue
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            # Inbound messages — upsert on message_id so Meta delivery retries are idempotent
            for message in value.get("messages", []) or []:
                doc = {
                    "direction": "inbound",
                    "provider": "meta",
                    "status": "received",
                    "message_id": message.get("id"),
                    "from": message.get("from"),
                    "type": message.get("type"),
                    "text": (message.get("text") or {}).get("body"),
                    "created_at": now_utc().isoformat(),
                }
                if doc["message_id"]:
                    await db.whatsapp_log.update_one(
                        {"direction": "inbound", "message_id": doc["message_id"]},
                        {"$setOnInsert": doc}, upsert=True)
                else:
                    await db.whatsapp_log.insert_one(doc)
                stored += 1
            # Delivery/read status events for our outbound messages
            for st in value.get("statuses", []) or []:
                await db.whatsapp_log.update_one(
                    {"direction": "status", "message_id": st.get("id"), "status": st.get("status")},
                    {"$setOnInsert": {
                        "direction": "status", "provider": "meta",
                        "message_id": st.get("id"), "status": st.get("status"),
                        "recipient_id": st.get("recipient_id"),
                        "created_at": now_utc().isoformat(),
                    }},
                    upsert=True)
    return {"ok": True, "stored": stored}


