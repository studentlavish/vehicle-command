"""RDX / Vashu Hyundai gate API — app assembly, local-agent link, startup jobs, alerts and digest.

Route groups live in routers/*.py; shared setup lives in core.py.
"""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import re
import json
import uuid
import asyncio
import secrets as pysecrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import jwt
import whatsapp_meta as wa_meta
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect, Query
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from camera_manager import camera_manager
from plate_pipeline import route_detection
import plate_pipeline
from pymongo.errors import DuplicateKeyError
from event_bus import event_bus
import security
import scheduler
import config_check
import alerts
import test_drives


from core import RETENTION_DAYS  # noqa: F401,E402  (re-exported: callers read server.RETENTION_DAYS)
from core import ADMIN_EMAIL, ADMIN_PASSWORD, APP_TZ, JWT_ALG, JWT_SECRET, ROLE_ADMIN_MANAGER, ROLE_ADMIN_ONLY, ROLE_ALL, SERVER_STARTED_AT, client, db, hash_password, logger, mongo_url, now_utc, require_roles
from routers.admin import RETENTION_ENABLED, purge_expired_sessions
from routers.cameras import _forget_camera_source, restore_direct_cameras
import backup_utils
from routers.vehicles import MAX_VISIT_HOURS
from routers.whatsapp import _build_daily_summary_text, _twilio_config

app = FastAPI(title="RDX Car Showroom API")
api = APIRouter(prefix="/api")

from routers import auth, vehicles, insights, admin, platescan, whatsapp, cameras, plates, system, testdrives

# ---------- Portal cameras the local agent reads plates from ("Use for records") ----------
# The backend keeps its own capture for the live view; the assigned agent opens the
# same source separately for detection only. A camera is handed to its agent only
# while it is online here, and taken back when it goes offline or the toggle is off.
_agent_sockets: Dict[str, WebSocket] = {}
_agent_assignments_sent: Dict[str, list] = {}
ASSIGNMENT_CHECK_SECONDS = float(os.environ.get("ASSIGNMENT_CHECK_SECONDS", "5"))


class CameraRecordsIn(BaseModel):
    use_for_records: bool
    agent_id: Optional[str] = None


async def _assignments_for(agent_id: str) -> list:
    out = []
    query = {"use_for_records": True, "record_agent_id": agent_id, "source": {"$nin": [None, ""]}}
    async for d in db.camera_config.find(query, {"_id": 0}).sort("camera_id", 1):
        if d.get("enabled") is False:
            continue
        state = camera_manager.get(d["camera_id"])
        if not state or state.status != "online" or _is_agent_fed_state(state):
            continue
        item = {"camera_id": d["camera_id"], "name": d.get("name") or d["camera_id"], "source": d["source"]}
        if d.get("roi"):
            item["roi"] = d["roi"]
        out.append(item)
    return out


def _is_agent_fed_state(state) -> bool:
    return isinstance(state.source, str) and state.source.startswith("agent:")


async def _push_assignments(agent_id: str, force: bool = False) -> None:
    """Send the agent its camera list if it changed (or always, with force)."""
    ws = _agent_sockets.get(agent_id)
    if ws is None:
        return
    cams = await _assignments_for(agent_id)
    if not force and _agent_assignments_sent.get(agent_id) == cams:
        return
    try:
        await ws.send_json({"type": "camera_assignments", "cameras": cams})
        _agent_assignments_sent[agent_id] = cams
        logger.info("[agent %s] camera assignments → %s", agent_id, [c["camera_id"] for c in cams] or "none")
    except Exception as e:
        logger.info("[agent %s] could not send camera assignments: %s", agent_id, e)


async def _assignment_loop() -> None:
    """Follow cameras going online/offline and config changes for every registered agent."""
    while True:
        await asyncio.sleep(ASSIGNMENT_CHECK_SECONDS)
        for agent_id in list(_agent_assignments_sent):
            try:
                await _push_assignments(agent_id)
            except Exception as e:
                logger.warning("[agent %s] assignment check failed: %s", agent_id, e)


@api.put("/camera-config/{camera_id}/records")
async def set_camera_records(camera_id: str, payload: CameraRecordsIn,
                             user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Turn "Use for records" on/off: an agent reads plates from this portal camera."""
    cid = camera_id.strip().upper()
    cfg = await db.camera_config.find_one({"camera_id": cid}, {"_id": 0}) or {}
    fields: Dict[str, Any] = {"use_for_records": payload.use_for_records, "updated_at": now_utc().isoformat()}
    previous_agent = cfg.get("record_agent_id") or ""
    if payload.use_for_records:
        if not cfg.get("source"):
            raise HTTPException(status_code=400, detail="Only cameras added on this page (with a camera URL) can be used for records")
        agent_id = (payload.agent_id or "").strip()
        if agent_id:
            if not await db.agents.find_one({"agent_id": agent_id}, {"_id": 1}):
                raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        else:
            online = [a["agent_id"] async for a in db.agents.find({"status": "online"}, {"agent_id": 1})]
            if len(online) != 1:
                raise HTTPException(status_code=400, detail="Choose which camera agent should use this camera"
                                    if online else "No camera agent is online — start the agent first")
            agent_id = online[0]
        fields["record_agent_id"] = agent_id
    await db.camera_config.update_one({"camera_id": cid}, {"$set": fields}, upsert=True)
    await security.audit(db, user, "camera.records", cid, use_for_records=payload.use_for_records,
                         agent_id=fields.get("record_agent_id", previous_agent))
    for a in {previous_agent, fields.get("record_agent_id", "")} - {""}:
        await _push_assignments(a)
    return {"ok": True, "camera_id": cid, "use_for_records": payload.use_for_records,
            "record_agent_id": fields.get("record_agent_id", previous_agent)}


@api.post("/cameras/{camera_id}/disconnect")
async def disconnect_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Disconnect a camera and remove it from the registry.

    - Direct-URL cameras: capture thread is stopped, entry removed.
    - Agent-fed cameras: entry removed; the local agent will re-register the
      camera automatically on the next frame if it's still streaming — so
      only *stale* agent-fed cameras stay disconnected.
    """
    if not camera_manager.remove(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    await _forget_camera_source(camera_id)
    logger.info("[CAMERA] disconnected + removed camera_id=%s by user=%s", camera_id.strip().upper(), user.get("email"))
    return {"ok": True, "camera_id": camera_id.strip().upper()}


def _authorize_ws(token: Optional[str]) -> Optional[dict]:
    """Verify a token passed via ?token=... query string on the WebSocket."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ---------- Local Agent (outbound WSS from private-network showroom PCs) ----------
class AgentRegisterIn(BaseModel):
    agent_id: str
    hostname: Optional[str] = ""
    platform: Optional[str] = "windows"
    version: Optional[str] = "1.0.0"


VASHU_AGENT_SECRET = os.environ.get("VASHU_AGENT_SECRET", "")


# Largest JPEG accepted from an agent (live frames and detection crops).
MAX_AGENT_JPEG_BYTES = int(os.environ.get("MAX_AGENT_JPEG_BYTES", str(2 * 1024 * 1024)))
_warned_shared_secret: set = set()


async def _authorize_agent(agent_id: Optional[str], secret: Optional[str]) -> bool:
    """An agent must be registered (POST /api/agent/register) and present its own secret.

    Agents registered before per-agent secrets existed (no secret_hash yet) may
    still use the shared VASHU_AGENT_SECRET until an admin rotates their secret.
    Unknown agent IDs are always refused.
    """
    if not agent_id or not secret:
        return False
    doc = await db.agents.find_one({"agent_id": agent_id}, {"secret_hash": 1})
    if not doc:
        return False
    if doc.get("secret_hash"):
        return security.secret_matches(secret, doc["secret_hash"])
    if security.shared_secret_matches(secret, VASHU_AGENT_SECRET):
        if agent_id not in _warned_shared_secret:
            _warned_shared_secret.add(agent_id)
            logger.warning("[agent %s] authenticated with the shared VASHU_AGENT_SECRET — give it its own secret "
                           "(Cameras → Camera agents → Rotate secret)", agent_id)
        return True
    return False


def _public_agent(d: dict) -> dict:
    return {
        "agent_id": d.get("agent_id"),
        "hostname": d.get("hostname", ""),
        "platform": d.get("platform", ""),
        "version": d.get("version", ""),
        "status": d.get("status", "offline"),
        "last_heartbeat": d.get("last_heartbeat"),
        "connected_at": d.get("connected_at"),
        "cameras": d.get("cameras", []),
        "has_own_secret": bool(d.get("secret_hash")),
        "health": d.get("health") or {},
    }


@api.get("/agent/status")
async def agent_status(user: dict = Depends(require_roles(*ROLE_ALL))):
    docs = await db.agents.find({}).to_list(200)
    return [_public_agent(d) for d in docs]


_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")


@api.post("/agent/register")
async def agent_register_http(payload: AgentRegisterIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Register a new camera agent. The secret is returned ONCE — put it in the agent's .env."""
    agent_id = payload.agent_id.strip()
    if not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=400, detail="Agent ID: 2–64 letters, digits, '-', '_' or '.'")
    if await db.agents.find_one({"agent_id": agent_id}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An agent with this ID already exists — rotate its secret instead")
    secret = security.generate_agent_secret()
    await db.agents.insert_one({
        "agent_id": agent_id, "hostname": payload.hostname, "platform": payload.platform,
        "version": payload.version, "created_at": now_utc().isoformat(), "status": "offline",
        "secret_hash": security.hash_secret(secret),
    })
    await security.audit(db, user, "agent.register", agent_id)
    return {"ok": True, "agent_id": agent_id, "secret": secret}


@api.post("/agent/{agent_id}/rotate-secret")
async def agent_rotate_secret(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Issue a new secret for an agent (the old one stops working immediately)."""
    secret = security.generate_agent_secret()
    r = await db.agents.update_one({"agent_id": agent_id}, {"$set": {"secret_hash": security.hash_secret(secret),
                                                                    "secret_rotated_at": now_utc().isoformat()}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    await security.audit(db, user, "agent.rotate_secret", agent_id)
    return {"ok": True, "agent_id": agent_id, "secret": secret}


@api.delete("/agent/{agent_id}")
async def agent_delete(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    doc = await db.agents.find_one_and_delete({"agent_id": agent_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Agent not found")
    for cam_id in doc.get("cameras", []):
        camera_manager.mark_agent_offline(cam_id, "agent removed")
    await security.audit(db, user, "agent.delete", agent_id)
    return {"ok": True}


@api.post("/agent/{agent_id}/disconnect")
async def agent_disconnect_http(agent_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    await db.agents.update_one({"agent_id": agent_id}, {"$set": {"status": "offline"}})
    doc = await db.agents.find_one({"agent_id": agent_id}) or {}
    for cam_id in doc.get("cameras", []):
        camera_manager.mark_agent_offline(cam_id, "agent disconnect requested")
    return {"ok": True}


AGENT_EVENT_STALE_SECONDS = 120


async def _claim_agent_event(event_id: str) -> Optional[dict]:
    """Claim an agent event for processing.

    Returns None when this call owns it (go ahead), the stored ack when it was
    already processed, or {} when another connection is processing it right now.
    """
    now = datetime.now(timezone.utc)
    try:
        await db.processed_events.insert_one({"event_id": event_id, "status": "processing", "at": now})
        return None
    except DuplicateKeyError:
        prev = await db.processed_events.find_one({"event_id": event_id}) or {}
        if prev.get("status") == "done":
            return prev.get("ack") or {}
        started = prev.get("at")
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or (now - started).total_seconds() > AGENT_EVENT_STALE_SECONDS:
            # A previous attempt died mid-way — take it over.
            await db.processed_events.update_one({"event_id": event_id}, {"$set": {"status": "processing", "at": now}})
            return None
        return {}


def _agent_seen_at(ts, agent_id: str = "") -> Optional[datetime]:
    """The agent's 'seen at' time, if plausible (within the last 7 days, not in the future)."""
    try:
        t = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    now = datetime.now(timezone.utc)
    if t > now + timedelta(seconds=120) or t < now - timedelta(days=7):
        logger.warning("[agent %s] ignoring implausible detection time %s (check the PC clock)", agent_id, t.isoformat())
        return None
    return min(t, now)


@app.websocket("/api/agent/ws")
async def ws_agent(websocket: WebSocket, agent_id: Optional[str] = Query(default=None), secret: Optional[str] = Query(default=None)):
    """Outbound WSS endpoint for the Windows Local Camera Agent.

    Accepts two frame delivery modes for Cloudflare-compatibility:
      1. Legacy JSON text frame:  {"type":"frame","camera_id":..,"jpeg_b64":".."}
      2. Binary frame (preferred): [4-byte BE header-len][utf-8 JSON header][raw JPEG]
         header must include {"type":"frame","camera_id":..,"name":..,"seq":..,"ts":..}
    """
    await websocket.accept()
    # Prefer headers (secrets in URLs end up in proxy logs); query params kept for older agents.
    agent_id = (websocket.headers.get("x-agent-id") or agent_id or "").strip()
    secret = websocket.headers.get("x-agent-secret") or secret or ""
    if not await _authorize_agent(agent_id, secret):
        logger.warning("[agent %s] refused: unknown agent or bad secret", agent_id or "?")
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    active_agent_id: str = agent_id
    cameras_registered: set = set()
    await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
        "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
    }})

    await websocket.send_json({"type": "hello", "ok": True})
    logger.info("[agent %s] connected", active_agent_id or "?")
    _agent_sockets[active_agent_id] = websocket

    import base64 as _b64
    import struct as _struct

    async def _handle_frame(cam_id: str, jpeg: bytes, name: str) -> None:
        if not cam_id or not jpeg:
            return
        if len(jpeg) > MAX_AGENT_JPEG_BYTES:
            logger.warning("[agent %s] dropped oversized frame (%d bytes) camera=%s", active_agent_id, len(jpeg), cam_id)
            return
        cam_id_norm = cam_id.strip().upper()
        if cam_id_norm not in cameras_registered:
            camera_manager.register_agent_camera(cam_id_norm, label=name or cam_id_norm, agent_id=active_agent_id or "")
            cameras_registered.add(cam_id_norm)
            logger.info("[CAMERA] agent camera registered camera_id=%s agent_id=%s", cam_id_norm, active_agent_id or "?")
        camera_manager.push_frame(cam_id_norm, jpeg)
        camera_manager.push_frame(cam_id, jpeg)

    try:
        while True:
            event = await websocket.receive()
            # WebSocket disconnected
            if event.get("type") == "websocket.disconnect":
                break

            # ---- Binary path (preferred) ----
            if "bytes" in event and event["bytes"] is not None:
                data: bytes = event["bytes"]
                if len(data) < 4:
                    continue
                try:
                    (hlen,) = _struct.unpack(">I", data[:4])
                    if hlen <= 0 or hlen > len(data) - 4 or hlen > 8192:
                        continue
                    header = json.loads(data[4:4 + hlen].decode("utf-8"))
                    jpeg = data[4 + hlen:]
                except Exception:
                    continue
                if header.get("type") != "frame":
                    continue
                await _handle_frame(header.get("camera_id", ""), jpeg, header.get("name", ""))
                continue

            # ---- Text path (control + legacy frames) ----
            raw = event.get("text")
            if raw is None:
                continue
            try:
                msg = json.loads(raw) if raw else {}
            except Exception:
                await websocket.send_json({"type": "error", "message": "bad json"})
                continue
            mtype = msg.get("type")

            if mtype == "agent_register":
                claimed = msg.get("agent_id") or active_agent_id
                if claimed != active_agent_id:
                    await websocket.send_json({"type": "error", "message": "agent_id does not match the authenticated agent"})
                    continue
                await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                    "hostname": msg.get("hostname", ""),
                    "platform": msg.get("platform", "windows"), "version": msg.get("version", "1.0.0"),
                    "status": "online", "connected_at": now_utc().isoformat(), "last_heartbeat": now_utc().isoformat(),
                }})
                await websocket.send_json({"type": "registered", "agent_id": active_agent_id})
                await _push_assignments(active_agent_id, force=True)

            elif mtype == "camera_status":
                cam_id_raw = msg.get("camera_id")
                if not cam_id_raw:
                    continue
                cam_id = str(cam_id_raw).strip().upper()
                name = msg.get("name", cam_id)
                status = msg.get("status", "online")
                if status == "online":
                    camera_manager.register_agent_camera(cam_id, label=name, agent_id=active_agent_id or "")
                    logger.info("[CAMERA] agent camera registered camera_id=%s agent_id=%s", cam_id, active_agent_id or "?")
                else:
                    camera_manager.mark_agent_offline(cam_id, msg.get("error", "agent reported offline"))
                cameras_registered.add(cam_id)
                if active_agent_id:
                    await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {
                        "cameras": list(cameras_registered), "last_heartbeat": now_utc().isoformat(),
                    }})

            elif mtype == "heartbeat":
                update = {"last_heartbeat": now_utc().isoformat(), "status": "online"}
                health = msg.get("health")
                if isinstance(health, dict) and len(json.dumps(health, default=str)) <= 8192:
                    update["health"] = health
                await db.agents.update_one({"agent_id": active_agent_id}, {"$set": update})

            elif mtype == "ping":
                # App-level keep-alive from the agent — reply so the proxy sees traffic
                try:
                    await websocket.send_json({"type": "pong", "ts": msg.get("ts")})
                except Exception:
                    pass

            elif mtype == "frame":
                # Legacy base64 JSON path (backward compat)
                cam_id = msg.get("camera_id")
                b64 = msg.get("jpeg_b64", "")
                if not cam_id or not b64:
                    continue
                try:
                    jpeg = _b64.b64decode(b64)
                except Exception:
                    continue
                await _handle_frame(cam_id, jpeg, msg.get("name", ""))

            elif mtype == "plate_detected":
                # Local agent ran YOLO+OCR at the edge. Validate the plate and
                # either save it or put it in the Review queue for a person.
                cam_id = str(msg.get("camera_id", "")).strip().upper()
                raw_plate = (msg.get("plate") or "").upper().strip()  # "" = vehicle without a readable plate
                if not cam_id:
                    if msg.get("event_id"):  # answer, or the agent would re-send it forever
                        await websocket.send_json({"type": "plate_ack", "event_id": msg["event_id"],
                                                   "error": "camera_id missing"})
                    continue
                def _b64_or_empty(v):
                    try:
                        data = _b64.b64decode(v) if v else b""
                    except Exception:
                        return b""
                    return data if len(data) <= MAX_AGENT_JPEG_BYTES else b""
                event_id = str(msg.get("event_id") or "")[:64]
                if event_id:
                    prior = await _claim_agent_event(event_id)
                    if prior is not None:
                        if prior:  # already processed — the agent missed our ack; answer again
                            await websocket.send_json(prior)
                        continue
                try:
                    persistence = await route_detection(
                        db,
                        camera_id=cam_id,
                        raw_plate=raw_plate,
                        confidence=str(msg.get("confidence") or "medium"),
                        jpeg_frame=_b64_or_empty(msg.get("crop_jpeg_b64")),
                        plate_jpeg=_b64_or_empty(msg.get("plate_jpeg_b64")),
                        candidates=msg.get("candidates") or [],
                        reads=int(msg.get("reads") or 0),
                        support=float(msg.get("support") or 0),
                        seen_at=_agent_seen_at(msg.get("ts"), active_agent_id),
                    )
                    ack = {
                        "type": "plate_ack",
                        "event_id": event_id or None,
                        "plate": persistence.get("plate", raw_plate),
                        "queued_for_review": persistence.get("queued_for_review", False),
                        "review_id": persistence.get("review_id"),
                        "duplicate": persistence.get("duplicate", False),
                        "event": persistence.get("event"),
                        "owner_status": persistence.get("owner_status"),
                        "session_id": (persistence.get("session") or {}).get("id"),
                    }
                    if event_id:
                        await db.processed_events.update_one({"event_id": event_id},
                                                             {"$set": {"status": "done", "ack": ack}})
                    await websocket.send_json(ack)
                except Exception as e:
                    logger.exception("[agent] persistence failed for plate=%s", raw_plate)
                    if event_id:
                        await db.processed_events.delete_one({"event_id": event_id, "status": "processing"})
                        await websocket.send_json({"type": "plate_nack", "event_id": event_id,
                                                   "message": type(e).__name__})

            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.info("[agent %s] ws error: %s", active_agent_id or "?", e)
    finally:
        if _agent_sockets.get(active_agent_id) is websocket:
            _agent_sockets.pop(active_agent_id, None)
            _agent_assignments_sent.pop(active_agent_id, None)
        if active_agent_id:
            await db.agents.update_one({"agent_id": active_agent_id}, {"$set": {"status": "offline"}})
        for cam_id in cameras_registered:
            camera_manager.mark_agent_offline(cam_id, "agent disconnected")
        logger.info("[agent %s] disconnected", active_agent_id or "?")



@app.websocket("/api/ws/events")
async def ws_events(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    """Broadcast channel for the live Dashboard — receives entry.recorded events
    published by the auto-detection pipeline."""
    await websocket.accept()
    cookie_token = websocket.cookies.get("access_token")
    payload = _authorize_ws(cookie_token) or _authorize_ws(token)
    if payload is None:
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    q = event_bus.subscribe()
    try:
        await websocket.send_json({"type": "hello", "user": payload.get("email")})
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.info("[ws events] disconnect: %s", e)
    finally:
        event_bus.unsubscribe(q)


@app.websocket("/api/ws/camera/{camera_id}")
async def ws_camera(websocket: WebSocket, camera_id: str, token: Optional[str] = Query(default=None)):
    # Accept then authenticate via cookie or ?token=
    await websocket.accept()

    # Try cookie first
    cookie_token = websocket.cookies.get("access_token")
    payload = _authorize_ws(cookie_token) or _authorize_ws(token)
    if payload is None:
        await websocket.send_json({"type": "error", "message": "unauthorized"})
        await websocket.close(code=4401)
        return

    if camera_manager.get(camera_id) is None:
        await websocket.send_json({
            "type": "error",
            "message": (
                f"camera '{camera_id}' not connected. "
                "If this is an agent-fed camera, make sure the Local Agent is running and streaming."
            ),
        })
        await websocket.close(code=4404)
        return

    logger.info("[STREAM] browser subscribed camera_id=%s user=%s", camera_id.strip().upper(), payload.get("email"))

    # Push initial status
    state = camera_manager.get(camera_id)
    if state:
        try:
            await websocket.send_json({"type": "status", "camera": state.public()})
        except Exception:
            return

    connected = {"v": True}

    def is_connected() -> bool:
        return connected["v"]

    async def send_bytes(data: bytes) -> None:
        try:
            await websocket.send_bytes(data)
        except Exception:
            connected["v"] = False

    # Status heartbeat every 2s + broadcast detected plates
    async def heartbeat():
        last_plate_sig = None
        while connected["v"]:
            await asyncio.sleep(2)
            st = camera_manager.get(camera_id)
            if st is None:
                break
            try:
                await websocket.send_json({"type": "status", "camera": st.public()})
            except Exception:
                connected["v"] = False
                break
            # Broadcast new plate detections through the same WebSocket
            plate = getattr(st, "latest_plate", None)
            if plate:
                sig = (plate.get("plate"), plate.get("detected_at"))
                if sig != last_plate_sig:
                    last_plate_sig = sig
                    try:
                        await websocket.send_json({"type": "plate", **plate})
                    except Exception:
                        connected["v"] = False
                        break

    hb_task = asyncio.create_task(heartbeat())
    try:
        await camera_manager.stream(camera_id, send_bytes, is_connected, fps=15)
    except WebSocketDisconnect:
        pass
    finally:
        connected["v"] = False
        hb_task.cancel()
        logger.info("[ws camera %s] client disconnected", camera_id)


# ===================== Startup =====================
async def seed_admin():
    """Create the first admin from .env on a fresh install — and only then.

    Once any admin exists the database owns the password: restarts never reset
    it, and ADMIN_PASSWORD in .env is ignored (change passwords in the app).
    """
    if await db.users.find_one({"role": "admin"}, {"_id": 1}):
        return
    problem = security.password_problem(ADMIN_PASSWORD)
    if problem:
        raise RuntimeError(f"Refusing to create the first admin: ADMIN_PASSWORD is not acceptable ({problem}).")
    if await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 1}) is None:
        await db.users.insert_one({
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "name": "Admin",
            "role": "admin",
            "status": "active",
            "created_at": now_utc().isoformat(),
            "last_login": now_utc().isoformat(),
        })
        logger.info("Seeded admin user: %s", ADMIN_EMAIL)
    else:
        logger.warning("No admin exists and %s is already a non-admin user — promote a user to admin manually.", ADMIN_EMAIL)


GOOGLE_ADMIN_EMAIL = os.environ.get("GOOGLE_ADMIN_EMAIL", "")
AGENT_BOOTSTRAP_ID = os.environ.get("AGENT_BOOTSTRAP_ID", "").strip()


async def seed_bootstrap_agent():
    """Local development: pre-register one agent that uses the shared VASHU_AGENT_SECRET."""
    if not AGENT_BOOTSTRAP_ID or not os.environ.get("VASHU_AGENT_SECRET", ""):
        return
    r = await db.agents.update_one({"agent_id": AGENT_BOOTSTRAP_ID},
                                   {"$setOnInsert": {"agent_id": AGENT_BOOTSTRAP_ID, "status": "offline",
                                                     "created_at": now_utc().isoformat()}}, upsert=True)
    if r.upserted_id:
        logger.info("Pre-registered development agent %s (shared secret)", AGENT_BOOTSTRAP_ID)


async def seed_google_admin():
    """Explicitly configured owner account that authenticates via Google only."""
    if not GOOGLE_ADMIN_EMAIL:
        return
    email = GOOGLE_ADMIN_EMAIL.strip().lower()
    if await db.users.find_one({"email": email}):
        return
    await db.users.insert_one({
        "email": email,
        # Random unknown password — this account signs in via Google only.
        "password_hash": hash_password(str(ObjectId())),
        "name": "Owner",
        "role": "admin",
        "status": "active",
        "auth_provider": "google",
        "created_at": now_utc().isoformat(),
        "last_login": None,
    })
    logger.info("Seeded Google admin user: %s", email)


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.vehicle_masters.create_index("vehicle_number", unique=True)
    await db.vehicle_masters.create_index("phone_number")
    await db.vehicle_masters.create_index("customer_id")
    await db.visit_sessions.create_index("vehicle_number")
    await db.visit_sessions.create_index("entry_time")
    await db.visit_sessions.create_index("visit_date")
    await db.visit_sessions.create_index("status")
    await db.visit_sessions.create_index([("vehicle_number", 1), ("entry_time", -1)])
    await db.plate_reviews.create_index([("status", 1), ("created_at", -1)])
    await db.plate_reviews.create_index("id", unique=True)
    await db.known_plates.create_index("plate", unique=True)
    await db.ocr_labels.create_index([("at", 1)])


@app.on_event("startup")
async def startup():
    config_check.check_or_exit()
    await ensure_indexes()
    await security.ensure_indexes(db)
    await plate_pipeline.ensure_session_indexes(db)
    await seed_admin()
    await seed_google_admin()
    await seed_bootstrap_agent()
    await restore_direct_cameras()
    app.state.assignments = asyncio.create_task(_assignment_loop())
    # Daily jobs (digest, backup, retention) — see scheduler.py.
    if SCHEDULER_ENABLED:
        app.state.scheduler = asyncio.create_task(scheduler.run_forever(db, _scheduled_jobs()))
    else:
        logger.info("[scheduler] disabled via SCHEDULER_ENABLED=false")
    if ALERTS_ENABLED:
        app.state.alerts = asyncio.create_task(_alert_loop())
    else:
        logger.info("[alerts] disabled via ALERTS_ENABLED=false")


# ===================== Alerts (see alerts.py) =====================
ALERTS_ENABLED = os.environ.get("ALERTS_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
ALERT_CHECK_SECONDS = 60
_ALERT_PREFIX = {"fired": "ALERT", "repeat": "STILL A PROBLEM", "resolved": "RESOLVED"}


async def _alert_recipients() -> List[str]:
    configured = [e.strip().lower() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]
    if configured:
        return configured
    return sorted({(u.get("email") or "").lower() async for u in
                   db.users.find({"role": "admin", "status": {"$ne": "inactive"}}, {"email": 1}) if u.get("email")})


async def _notify_alert(kind: str, doc: dict) -> None:
    """Push an alert change to the dashboard, email and WhatsApp (whichever are configured)."""
    event_bus.publish({"type": "alert.changed", "kind": kind, "alert": alerts.public(doc)})
    text = f"{_ALERT_PREFIX[kind]}: {doc['message']}"
    if kind == "resolved":
        logger.info("[alerts] %s", text)
    else:
        logger.warning("[alerts] %s", text)
    if _email_provider():
        from html import escape as _esc
        html = (f'<div style="font-family:Arial,sans-serif;font-size:14px"><p><b>{_esc(_ALERT_PREFIX[kind])}</b></p>'
                f'<p>{_esc(doc["message"])}</p><p style="color:#64748b;font-size:12px">Vehicle Command · automatic '
                f'alert. Check the dashboard for details.</p></div>')
        for to in await _alert_recipients():
            try:
                await _send_email(to=to, subject=f"[Vehicle Command] {text[:120]}", html=html)
            except Exception as e:
                logger.warning("[alerts] email to %s failed: %s", to, e)
    if wa_meta.meta_whatsapp_enabled():
        to = os.environ.get("ALERT_WHATSAPP_TO", "").strip() or (await _twilio_config())["to"]
        if to:
            try:
                await wa_meta.send_text(db, to=to, body=text, by="alerts", context="alert")
            except Exception as e:
                logger.warning("[alerts] WhatsApp alert failed: %s", e)


async def run_alert_check(now: Optional[datetime] = None) -> Dict[str, List[str]]:
    now = now or datetime.now(timezone.utc)
    backup_job = next((j for j in _scheduled_jobs() if j.name == "backup"), None)
    findings = await alerts.evaluate(
        db, now, cameras=camera_manager.list(), disk=plate_pipeline.snapshot_disk_stats(),
        started_at=SERVER_STARTED_AT, backups_enabled=bool(SCHEDULER_ENABLED and backup_job and backup_job.enabled),
        long_open=await plate_pipeline.long_open_sessions(db, MAX_VISIT_HOURS),
        test_drives_out=(await test_drives.current_status(db, now))["out"],
    )
    return await alerts.reconcile(db, findings, now, _notify_alert)


async def _alert_loop() -> None:
    await db.alerts.create_index([("status", 1), ("key", 1)])
    while True:
        try:
            await run_alert_check()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[alerts] check failed")
        await asyncio.sleep(ALERT_CHECK_SECONDS)


@api.get("/alerts")
async def list_alerts(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    firing = await db.alerts.find({"status": "firing"}).sort("first_at", -1).to_list(100)
    recent = await db.alerts.find({"status": "resolved"}).sort("resolved_at", -1).to_list(20)
    return {"firing": [alerts.public(d) for d in firing], "resolved": [alerts.public(d) for d in recent]}


SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _scheduled_jobs() -> List["scheduler.Job"]:
    async def retention(run_id: str) -> None:
        res = await purge_expired_sessions()
        logger.info("[retention %s] %s", run_id, res)

    async def backup(run_id: str) -> None:
        await backup_utils.run_backup(db, mongo_url, os.environ["DB_NAME"], run_id)

    return [
        scheduler.Job("digest", os.environ.get("DIGEST_TIME", "09:00"), _run_digest_send,
                      enabled=bool(_email_provider())),
        scheduler.Job("backup", os.environ.get("BACKUP_TIME", "03:00"), backup,
                      enabled=os.environ.get("BACKUP_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")),
        scheduler.Job("retention", os.environ.get("RETENTION_TIME", "03:30"), retention, enabled=RETENTION_ENABLED),
    ]


# ===================== Daily Digest Email (cron) =====================
from email_utils import send_email as _send_email, provider as _email_provider
from email_utils import supports_attachments as _email_supports_attachments
import digest_pdf

WEBHOOK_CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "")
_digest_seen_run_ids: set[str] = set()


def _fmt_hms(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "0m"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _pct_delta(now: float, before: float) -> str:
    """Change vs the comparison day, e.g. "+12%", "-5%", "±0%", "new"."""
    if not before:
        return "new" if now else "±0%"
    d = round(100.0 * (now - before) / before)
    return "±0%" if d == 0 else f"{d:+d}%"


async def _day_basics(lo: str, hi: str) -> dict:
    entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": lo, "$lt": hi}})
    exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": lo, "$lt": hi}})
    unique = len(await db.visit_sessions.distinct("vehicle_number", {"entry_time": {"$gte": lo, "$lt": hi}}))
    pipeline = [
        {"$match": {"entry_time": {"$gte": lo, "$lt": hi}, "status": "completed", "duration_seconds": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$duration_seconds"}, "max": {"$max": "$duration_seconds"}}},
    ]
    dwell = await db.visit_sessions.aggregate(pipeline).to_list(1)
    return {"entries": entries, "exits": exits, "unique_vehicles": unique,
            "avg_seconds": dwell[0]["avg"] if dwell else 0, "max_seconds": dwell[0]["max"] if dwell else 0}


async def _busiest_hour(lo: str, hi: str) -> dict:
    """Local hour with the most entries, and the first entry photo taken in it."""
    by_hour: Dict[int, list] = {}
    async for s in db.visit_sessions.find({"entry_time": {"$gte": lo, "$lt": hi}},
                                          {"_id": 0, "entry_time": 1, "entry_image": 1}).sort("entry_time", 1):
        try:
            h = datetime.fromisoformat(s["entry_time"]).astimezone(APP_TZ).hour
        except (TypeError, ValueError):
            continue
        by_hour.setdefault(h, []).append(s)
    if not by_hour:
        return {"busiest_hour": "", "busiest_hour_entries": 0, "busiest_hour_image": ""}
    hour = max(sorted(by_hour), key=lambda h: len(by_hour[h]))
    image = next((s.get("entry_image") for s in by_hour[hour] if s.get("entry_image")), "")
    return {"busiest_hour": f"{hour:02d}:00–{(hour + 1) % 24:02d}:00", "busiest_hour_entries": len(by_hour[hour]),
            "busiest_hour_image": image or ""}


async def _collect_digest_stats() -> dict:
    """Compute yesterday's showroom summary in the showroom's timezone, compared with the same
    weekday a week earlier."""
    tz = APP_TZ
    now_ist = datetime.now(tz)
    yday = (now_ist - timedelta(days=1)).date()

    def bounds(d):
        start = datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        return start.isoformat(), (start + timedelta(days=1)).isoformat()

    lo, hi = bounds(yday)
    base = await _day_basics(lo, hi)
    week_ago = yday - timedelta(days=7)
    prev = await _day_basics(*bounds(week_ago))
    # Busiest camera by entries
    cam_pipeline = [
        {"$match": {"entry_time": {"$gte": lo, "$lt": hi}, "entry_camera": {"$ne": ""}}},
        {"$group": {"_id": "$entry_camera", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 1},
    ]
    busiest = await db.visit_sessions.aggregate(cam_pipeline).to_list(1)
    long_open = await plate_pipeline.long_open_sessions(db, MAX_VISIT_HOURS)
    # How hands-free was yesterday: events saved automatically vs. sent to a person.
    auto_entries = await db.visit_sessions.count_documents({"entry_time": {"$gte": lo, "$lt": hi}, "detected_by": "agent_yolo"})
    auto_exits = await db.visit_sessions.count_documents({"exit_time": {"$gte": lo, "$lt": hi},
                                                         "exit_confidence": {"$in": ["high", "medium"]}})
    reviews = await db.plate_reviews.count_documents({"created_at": {"$gte": lo, "$lt": hi}})
    corrections = await db.ocr_labels.count_documents({"at": {"$gte": lo, "$lt": hi}, "corrected": True})
    auto_total = auto_entries + auto_exits
    exit_missed = await db.visit_sessions.count_documents({"status": "exit_missed", "exit_missed_at": {"$gte": lo, "$lt": hi}})
    entry_missed = await db.visit_sessions.count_documents({"status": "entry_missed", "exit_time": {"$gte": lo, "$lt": hi}})
    keys = ("entries", "exits", "unique_vehicles")
    return {
        "date": yday.isoformat(),
        "entries": base["entries"],
        "exits": base["exits"],
        "unique_vehicles": base["unique_vehicles"],
        "avg_dwell": _fmt_hms(base["avg_seconds"]),
        "max_dwell": _fmt_hms(base["max_seconds"]),
        "compare": {"date": week_ago.isoformat(), "label": f"last {week_ago.strftime('%A')}",
                    **{k: prev[k] for k in keys}, "avg_dwell": _fmt_hms(prev["avg_seconds"])},
        "deltas": {k: _pct_delta(base[k], prev[k]) for k in keys},
        **await _busiest_hour(lo, hi),
        "busiest_camera": busiest[0]["_id"] if busiest else "—",
        "busiest_camera_count": busiest[0]["n"] if busiest else 0,
        "long_open": len(long_open),
        "long_open_plates": [x["vehicle_number"] for x in long_open[:10]],
        "missed_events": exit_missed + entry_missed,
        "auto_saved": auto_total,
        "sent_to_review": reviews,
        "review_rate": f"{100.0 * reviews / (auto_total + reviews):.1f}%" if (auto_total + reviews) else "—",
        "corrections": corrections,
    }


DIGEST_IMAGE_CID = "busiest-hour"


def _render_digest_html(s: dict, image_cid: Optional[str] = None) -> str:
    from html import escape as _esc
    date_str = _esc(s["date"])
    busiest = f"{s['busiest_camera']} ({s['busiest_camera_count']})"
    cmp = s.get("compare") or {}
    vs = f"vs {cmp['label']}" if cmp.get("label") else ""
    deltas = s.get("deltas") or {}

    def sub(key):
        return f"{deltas[key]} {vs} ({cmp.get(key, 0)})" if key in deltas else None

    hour = f"{s['busiest_hour']} ({s.get('busiest_hour_entries', 0)})" if s.get("busiest_hour") else "—"
    image = ('<tr><td style="padding:0 30px 16px 30px">'
             '<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px">'
             f'First car in the busiest hour ({_esc(s.get("busiest_hour", ""))})</div>'
             f'<img src="cid:{_esc(image_cid)}" alt="Busiest hour snapshot" width="500" '
             'style="width:100%;max-width:500px;border-radius:10px;border:1px solid #1e293b;display:block"></td></tr>'
             ) if image_cid else ''
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#0f172a;font-family:Arial,sans-serif;color:#e2e8f0">'
        '<tr><td align="center" style="padding:32px 12px">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#111827;border:1px solid #1f2937;border-radius:14px">'
        '<tr><td style="padding:24px 24px 8px 24px">'
        '<div style="font-size:11px;letter-spacing:2px;color:#60a5fa;text-transform:uppercase">'
        'Vashu Hyundai &middot; Daily Digest</div>'
        f'<h1 style="margin:8px 0 4px 0;font-size:22px;color:#f8fafc">{date_str} &middot; Showroom Recap</h1>'
        '<p style="margin:0;color:#94a3b8;font-size:13px">Yesterday\'s vehicle traffic at a glance'
        + (f', compared with {_esc(cmp["label"])}.' if cmp.get("label") else '.') + '</p>'
        '</td></tr>'
        '<tr><td style="padding:16px 24px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + _metric_cell("Entries", s["entries"], sub("entries")) + _metric_cell("Exits", s["exits"], sub("exits"))
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Unique Vehicles", s["unique_vehicles"], sub("unique_vehicles"))
        + _metric_cell("Avg Dwell", s["avg_dwell"], f"{cmp['avg_dwell']} {vs}" if cmp.get("avg_dwell") else None)
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Busiest Hour", hour) + _metric_cell("Busiest Camera", busiest)
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Longest Dwell", s["max_dwell"])
        + _metric_cell(f"Open > {MAX_VISIT_HOURS:g}h", s.get("long_open", 0))
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Saved automatically", s.get("auto_saved", 0))
        + _metric_cell("Sent to review", f'{s.get("sent_to_review", 0)} ({s.get("review_rate", "—")})')
        + '</table>'
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px">'
        + _metric_cell("Missed entry/exit", s.get("missed_events", 0)) + '<td width="50%"></td>'
        + '</table>'
        + ((f'<p style="margin:10px 6px 0;color:#fbbf24;font-size:12px">Still inside: '
            f'{_esc(", ".join(s["long_open_plates"]))}</p>') if s.get("long_open_plates") else '')
        + '</td></tr>'
        + image
        + '<tr><td style="padding:8px 24px 24px 24px;color:#64748b;font-size:11px;line-height:1.5">'
        + 'Sent by Vashu Hyundai &middot; AI Vehicle Management System. We never ask for your password or payment details by email.'
        + '</td></tr>'
        + '</table></td></tr></table>'
    )


def _metric_cell(label: str, value, sub: Optional[str] = None) -> str:
    from html import escape as _esc
    color = "#94a3b8"
    if sub and sub[:1] == "+":
        color = "#4ade80"
    elif sub and sub[:1] == "-":
        color = "#f87171"
    return (
        '<td width="50%" style="padding:6px" valign="top">'
        '<div style="background:#0b1220;border:1px solid #1e293b;border-radius:10px;padding:14px">'
        f'<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1.5px">{_esc(label)}</div>'
        f'<div style="font-size:22px;color:#f8fafc;font-weight:700;margin-top:4px">{_esc(str(value))}</div>'
        + (f'<div style="font-size:11px;color:{color};margin-top:4px">{_esc(sub)}</div>' if sub else '')
        + '</div></td>'
    )


async def _digest_recipients() -> List[str]:
    """Union of admin/manager user emails + settings.digest_extra_emails."""
    emails: set[str] = set()
    async for u in db.users.find({"role": {"$in": ["admin", "manager"]}, "status": {"$ne": "inactive"}}, {"email": 1}):
        e = (u.get("email") or "").strip().lower()
        if e:
            emails.add(e)
    doc = await db.settings.find_one({"key": "app"}) or {}
    for e in (doc.get("digest_extra_emails") or []):
        e = str(e or "").strip().lower()
        if e:
            emails.add(e)
    return sorted(emails)


async def _digest_image(stats: dict) -> Optional[bytes]:
    url = stats.get("busiest_hour_image") or ""
    if not url.startswith("/api/snapshots/"):
        return None
    try:
        return await plate_pipeline.read_snapshot(os.path.basename(url))
    except Exception as e:
        logger.warning("[digest] could not load busiest-hour snapshot: %s", e)
        return None


async def _send_digest_whatsapp(run_id: str, stats: dict, pdf: Optional[bytes]) -> None:
    """Meta Cloud API only. With META_DIGEST_TEMPLATE set, sends the approved template with the PDF
    as its document header (delivered any time). Without it, sends the text summary plus the PDF,
    which Meta only delivers inside the 24-hour window after the recipient last wrote to us."""
    cfg = await _twilio_config()  # reuses the same "Admin WhatsApp To" recipient
    wa_to = cfg["to"]
    if not wa_to:
        logger.info("[digest %s] whatsapp skipped — no recipient configured", run_id)
        return
    filename = f"vashu-digest-{stats['date']}.pdf"
    media_id = ""
    if pdf:
        try:
            media_id = await wa_meta.upload_media(pdf, "application/pdf", filename)
        except Exception as e:
            logger.warning("[digest %s] whatsapp PDF upload failed: %s", run_id, e)
    if wa_meta.META_DIGEST_TEMPLATE:
        await wa_meta.send_template(
            db, to=wa_to, name=wa_meta.META_DIGEST_TEMPLATE, language=wa_meta.META_DIGEST_TEMPLATE_LANG,
            body_params=[stats["date"], stats["entries"], stats["exits"], stats["unique_vehicles"]],
            header_document={"id": media_id, "filename": filename} if media_id else None,
            by="cron", context="daily_digest")
        logger.info("[digest %s] whatsapp template sent (pdf=%s)", run_id, bool(media_id))
        return
    await wa_meta.send_text(db, to=wa_to, body=await _build_daily_summary_text(), by="cron", context="daily_digest")
    if media_id:
        await wa_meta.send_document(db, to=wa_to, media_id=media_id, filename=filename,
                                    caption=f"Daily digest {stats['date']}", by="cron", context="daily_digest")
    logger.info("[digest %s] whatsapp report sent (pdf=%s)", run_id, bool(media_id))


async def _run_digest_send(run_id: str) -> None:
    try:
        stats = await _collect_digest_stats()
        image = await _digest_image(stats)
        pdf: Optional[bytes] = None
        try:
            pdf = digest_pdf.build_digest_pdf(stats, image)
        except Exception:
            logger.exception("[digest %s] PDF build failed — sending without it", run_id)
        recipients = await _digest_recipients()
        if not recipients:
            logger.info("[digest %s] no email recipients", run_id)
        else:
            files = []
            if _email_supports_attachments():
                if image:
                    files.append({"filename": "busiest-hour.jpg", "content": image, "content_type": "image/jpeg",
                                  "cid": DIGEST_IMAGE_CID})
                if pdf:
                    files.append({"filename": f"vashu-digest-{stats['date']}.pdf", "content": pdf,
                                  "content_type": "application/pdf"})
            html = _render_digest_html(stats, image_cid=DIGEST_IMAGE_CID if any(f.get("cid") for f in files) else None)
            subject = f"Vashu Hyundai · Daily Digest — {stats['date']}"
            sent = 0
            for to_addr in recipients:
                try:
                    await _send_email(to=to_addr, subject=subject, html=html, attachments=files or None)
                    sent += 1
                except Exception as e:
                    logger.warning("[digest %s] send to %s failed: %s", run_id, to_addr, e)
            logger.info("[digest %s] sent %d/%d for %s", run_id, sent, len(recipients), stats["date"])
        # WhatsApp companion — Meta Cloud API only when configured; never breaks email.
        if wa_meta.meta_whatsapp_enabled():
            try:
                await _send_digest_whatsapp(run_id, stats, pdf)
            except Exception as e:
                logger.warning("[digest %s] whatsapp send failed: %s", run_id, e)
    except Exception:
        logger.exception("[digest %s] failed", run_id)


@app.post("/api/cron/digest")
async def cron_digest(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("authorization", "")
    if not WEBHOOK_CRON_SECRET or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = auth.split(" ", 1)[1].strip()
    if not pysecrets.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        env = await request.json()
    except Exception:
        env = {}
    run_id = request.headers.get("x-webhook-id") or (env.get("run_id") if isinstance(env, dict) else "") or str(uuid.uuid4())
    # Idempotency
    if run_id in _digest_seen_run_ids:
        return {"ok": True, "duplicate": True, "run_id": run_id}
    _digest_seen_run_ids.add(run_id)
    if len(_digest_seen_run_ids) > 500:
        # avoid unbounded growth
        _digest_seen_run_ids.clear()
        _digest_seen_run_ids.add(run_id)
    asyncio.create_task(_run_digest_send(run_id))
    return {"ok": True, "queued": True, "run_id": run_id}


@app.post("/api/cron/digest/preview")
async def cron_digest_preview(user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Admin-only manual trigger for testing the digest without waiting for the cron."""
    stats = await _collect_digest_stats()
    recipients = await _digest_recipients()
    return {"stats": stats, "recipients": recipients}


async def _run_backup_safe(run_id: str):
    """Wrapper: backup failures are logged + recorded, never raised — the
    running showroom app is unaffected and previous backups are kept."""
    try:
        await backup_utils.run_backup(db, mongo_url, os.environ["DB_NAME"], run_id)
    except Exception as e:
        logger.exception("[backup %s] failed: %s", run_id, e)
        try:
            await db.backups.insert_one({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "kind": "mongodb",
                "status": "failed",
                "error": str(e)[:400],
                "expired": False,
                "created_at": now_utc().isoformat(),
            })
        except Exception:
            pass


@app.post("/api/cron/backup")
async def cron_backup(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    auth = request.headers.get("authorization", "")
    if not WEBHOOK_CRON_SECRET or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = auth.split(" ", 1)[1].strip()
    if not pysecrets.compare_digest(token, WEBHOOK_CRON_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")
    run_id = request.headers.get("x-webhook-id") or str(uuid.uuid4())
    asyncio.create_task(_run_backup_safe(run_id))
    return {"ok": True, "queued": True, "run_id": run_id}


@api.get("/backups")
async def list_backups(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Backup history (metadata only — no secrets, no DB content)."""
    docs = await db.backups.find({"expired": False}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return {"backups": docs, "keep": backup_utils.BACKUP_KEEP}


@app.on_event("shutdown")
async def shutdown():
    for name in ("scheduler", "alerts"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
    client.close()


# Mount router + CORS
app.include_router(auth.router)
app.include_router(vehicles.router)
app.include_router(insights.router)
app.include_router(admin.router)
app.include_router(platescan.router)
app.include_router(whatsapp.router)
app.include_router(cameras.router)
app.include_router(api)
app.include_router(plates.router)
app.include_router(system.router)
app.include_router(testdrives.router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=security.cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)
