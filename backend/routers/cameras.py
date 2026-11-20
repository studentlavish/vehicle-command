"""Direct cameras (USB / IP Webcam / RTSP) and their entry/exit roles."""
import re
from typing import Optional, Literal, Dict, Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from camera_manager import camera_manager, CameraSourceConflict
import security


from core import ROLE_ADMIN_MANAGER, ROLE_ADMIN_ONLY, ROLE_ALL, db, logger, now_utc, require_roles

router = APIRouter(prefix="/api")

# ---------- Cameras (USB / IP Webcam / RTSP) ----------
class CameraConnectIn(BaseModel):
    camera_id: str
    source: str  # int-as-string for USB, or http/rtsp URL
    label: Optional[str] = ""
    # Optional setup in the same step (role is applied for admins only).
    role: Optional[Literal["entry", "exit", "both"]] = None
    gate: Optional[str] = None


_URL_CREDS_RE = re.compile(r"(//[^:/@]+:)[^@/]+@")


def _mask_source(source: Any) -> str:
    """Hide the password in rtsp://user:pass@host URLs before showing them in the UI."""
    return _URL_CREDS_RE.sub(r"\1***@", str(source or ""))


def _source_type(source: str) -> str:
    s = (source or "").strip().lower()
    if s.startswith("agent:"):
        return "agent"
    if s.startswith("rtsp"):
        return "rtsp"
    if s.startswith("http"):
        return "http"
    if s.isdigit() or s.startswith("usb:"):
        return "usb"
    return "unknown" if s else "none"


async def _camera_configs() -> Dict[str, dict]:
    return {d["camera_id"]: d async for d in db.camera_config.find({}, {"_id": 0})}


@router.get("/cameras")
async def list_cameras(user: dict = Depends(require_roles(*ROLE_ALL))):
    # Hide fully-removed / stopped cameras from the UI so the card disappears
    # when an admin disconnects an obsolete direct-URL camera.
    all_cams = camera_manager.list()
    configs = await _camera_configs()
    out = {}
    for cid, st in all_cams.items():
        if st.get("status") == "stopped":
            continue
        cfg = configs.get(cid) or {}
        st = {**st, "source": _mask_source(st.get("source")),
              "role": cfg.get("role", "both"), "enabled": cfg.get("enabled", True),
              "configured": bool(cfg), "gate": cfg.get("gate", "")}
        if cfg.get("name"):
            st["label"] = cfg["name"]
        out[cid] = st
    return out


@router.get("/cameras/overview")
async def cameras_overview(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """One row per camera — live status, config (role / name / gate / active) and
    where its video comes from — for the central Cameras page."""
    configs = await _camera_configs()
    live = camera_manager.list()
    agent_of: Dict[str, str] = {}
    async for a in db.agents.find({}, {"agent_id": 1, "cameras": 1}):
        for c in a.get("cameras", []) or []:
            if c:
                agent_of[c.strip().upper()] = a.get("agent_id", "")
    rows = []
    for cid in sorted(set(configs) | set(live) | set(agent_of)):
        cfg = configs.get(cid) or {}
        st = live.get(cid) or {}
        live_source = st.get("source", "") if st.get("status") != "stopped" else ""
        if live_source.startswith("agent:"):
            source, agent_id = "", live_source.split(":", 1)[1]
        else:
            source, agent_id = live_source or cfg.get("source", ""), ""
        agent_id = agent_id or agent_of.get(cid, "")
        stype = "agent" if agent_id and not source else _source_type(source)
        rows.append({
            "camera_id": cid,
            "name": cfg.get("name") or (st.get("label") if st.get("label") != cid else "") or "",
            "role": cfg.get("role", "both"),
            "gate": cfg.get("gate", ""),
            "enabled": cfg.get("enabled", True),
            "configured": bool(cfg.get("role")),
            "use_for_records": bool(cfg.get("use_for_records")),
            "record_agent_id": cfg.get("record_agent_id", ""),
            "source_type": stype,
            "source": _mask_source(source),
            "agent_id": agent_id,
            "status": st.get("status") if st and st.get("status") != "stopped" else "offline",
            "error": st.get("error"),
            "last_frame_at": st.get("last_frame_at"),
            "frames_captured": st.get("frames_captured", 0),
            "live": bool(st) and st.get("status") != "stopped",
        })
    return rows


# ---------- Camera roles (entry / exit / both) ----------
class CameraConfigIn(BaseModel):
    role: Literal["entry", "exit", "both"]
    name: Optional[str] = ""
    gate: Optional[str] = ""
    enabled: bool = True


@router.get("/camera-config")
async def list_camera_config(user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Configured cameras plus any live/recent camera that has no role yet."""
    configured = {cid: d for cid, d in (await _camera_configs()).items() if d.get("role")}
    seen = set(camera_manager.list().keys())
    async for a in db.agents.find({}, {"cameras": 1}):
        seen.update(c.strip().upper() for c in a.get("cameras", []) if c)
    out = list(configured.values())
    for cid in sorted(seen - set(configured)):
        out.append({"camera_id": cid, "role": "both", "name": "", "gate": "", "enabled": True, "configured": False})
    for d in out:
        d.setdefault("configured", True)
    return sorted(out, key=lambda d: d["camera_id"])


@router.put("/camera-config/{camera_id}")
async def put_camera_config(camera_id: str, payload: CameraConfigIn, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    cid = camera_id.strip().upper()
    if not cid:
        raise HTTPException(status_code=400, detail="camera_id required")
    doc = {"camera_id": cid, **payload.model_dump(), "updated_at": now_utc().isoformat()}
    await db.camera_config.update_one({"camera_id": cid}, {"$set": doc}, upsert=True)
    await security.audit(db, user, "camera.config", cid, role=payload.role, enabled=payload.enabled)
    # Keep the live tile's label in sync with the saved name.
    state = camera_manager.get(cid)
    if state and payload.name:
        state.label = payload.name
    doc.pop("_id", None)
    return {**doc, "configured": True}


@router.delete("/camera-config/{camera_id}")
async def delete_camera_config(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_ONLY))):
    """Forget a camera: delete its config and stop any direct-URL stream.
    Agent-fed cameras reappear (without a role) if the agent keeps streaming them."""
    cid = camera_id.strip().upper()
    r = await db.camera_config.delete_one({"camera_id": cid})
    removed = camera_manager.remove(cid)
    # Agent-reported cameras (e.g. an offline agent camera with no role yet) are
    # listed from db.agents.cameras, so forget them there too.
    a = await db.agents.update_many({"cameras": cid}, {"$pull": {"cameras": cid}})
    if r.deleted_count == 0 and not removed and a.modified_count == 0:
        raise HTTPException(status_code=404, detail="Camera not configured")
    await security.audit(db, user, "camera.config_delete", cid)
    return {"ok": True}


@router.get("/cameras/{camera_id}")
async def get_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ALL))):
    state = camera_manager.get(camera_id)
    if not state:
        raise HTTPException(status_code=404, detail="Camera not found")
    return state.public()


@router.post("/cameras/connect")
async def connect_camera(payload: CameraConnectIn, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    # Refuse to overlay a direct-URL capture on top of an agent-fed camera.
    # The local agent is the authoritative source for its camera IDs — otherwise
    # the cloud would try to reach the phone's private LAN IP (e.g. 192.168.x.x).
    cid = payload.camera_id.strip().upper()
    if not cid:
        raise HTTPException(status_code=400, detail="camera_id required")
    try:
        cfg = await db.camera_config.find_one({"camera_id": cid}, {"_id": 0}) or {}
        label = (payload.label or "").strip() or cfg.get("name", "")
        info = camera_manager.connect(cid, payload.source, label)
        # Persist the source so the camera reconnects after a backend restart.
        fields: Dict[str, Any] = {"camera_id": cid, "source": payload.source.strip(), "updated_at": now_utc().isoformat()}
        if (payload.label or "").strip():
            fields["name"] = payload.label.strip()
        if payload.gate is not None:
            fields["gate"] = payload.gate.strip()
        if payload.role and user.get("role") == "admin":
            fields["role"] = payload.role
        await db.camera_config.update_one({"camera_id": cid}, {"$set": fields}, upsert=True)
        await security.audit(db, user, "camera.connect", cid, source=_mask_source(payload.source))
        return {**info, "source": _mask_source(info.get("source"))}
    except CameraSourceConflict as ce:
        raise HTTPException(status_code=409, detail=str(ce))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Camera connect failed: {e}")


@router.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str, user: dict = Depends(require_roles(*ROLE_ADMIN_MANAGER))):
    """Fully remove a camera from the registry (stops capture thread if any).
    Use this to clean up stale duplicates. Agent-fed cameras that are still
    online will be re-registered on the next frame from the local agent."""
    ok = camera_manager.remove(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Camera not found")
    await _forget_camera_source(camera_id)
    return {"ok": True, "camera_id": camera_id.strip().upper()}


async def _forget_camera_source(camera_id: str) -> None:
    """Stop a disconnected direct-URL camera from coming back on restart (role/name are kept)."""
    await db.camera_config.update_one({"camera_id": camera_id.strip().upper()}, {"$unset": {"source": ""}})


async def restore_direct_cameras() -> int:
    """Reconnect direct-URL cameras (RTSP / IP Webcam / USB) saved from the Cameras page."""
    n = 0
    async for d in db.camera_config.find({"source": {"$nin": [None, ""]}}, {"_id": 0}):
        try:
            camera_manager.connect(d["camera_id"], d["source"], d.get("name", ""))
            n += 1
        except Exception as e:
            logger.warning("[CAMERA] restore failed camera_id=%s: %s", d.get("camera_id"), e)
    if n:
        logger.info("[CAMERA] restored %d direct-URL camera(s)", n)
    return n


