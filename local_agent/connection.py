"""Outbound WSS connection to the Vashu cloud backend."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional

import websockets

log = logging.getLogger("vashu_agent.connection")


class CloudConnection:
    _BACKOFF_STEPS = [5, 10, 20, 30]

    def __init__(self, ws_url: str, agent_id: str, agent_secret: str) -> None:
        base = ws_url.rstrip("?/&")
        sep = "&" if "?" in base else "?"
        self.url = f"{base}{sep}agent_id={agent_id}&secret={agent_secret}"
        self.agent_id = agent_id
        self.agent_secret = agent_secret
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._send_lock = asyncio.Lock()
        self._attempt = 0

    def next_backoff(self) -> int:
        step = self._BACKOFF_STEPS[min(self._attempt, len(self._BACKOFF_STEPS) - 1)]
        self._attempt += 1
        return step

    def _reset_backoff(self) -> None:
        self._attempt = 0

    async def connect_and_run(self, hello_payload: Dict[str, Any]) -> None:
        log.info("[AGENT] connecting to cloud")
        async with websockets.connect(self.url, max_size=20 * 1024 * 1024, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            self._reset_backoff()
            log.info("[AGENT] Connected to cloud")
            await self._send_raw(hello_payload)
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "hello":
                        log.info("[AGENT] hello acknowledged by cloud")
                    elif mtype == "error":
                        log.error("[ERROR] server: %s", msg.get("message"))
                        if msg.get("message") == "unauthorized":
                            raise PermissionError("cloud rejected agent credentials")
                    elif mtype == "registered":
                        log.info("[AGENT] registered agent_id=%s", msg.get("agent_id"))
            finally:
                self._ws = None
                log.info("[AGENT] Disconnected")

    async def safe_send(self, payload: Dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        async with self._send_lock:
            try:
                await ws.send(json.dumps(payload))
            except Exception as e:
                log.warning("[AGENT] send failed: %s", e)

    async def _send_raw(self, payload: Dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        await self._ws.send(json.dumps(payload))

    async def close(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        except Exception:
            pass
        self._ws = None

    # ---- test-mode helpers ----
    async def probe(self, agent_id: str) -> None:
        async with websockets.connect(self.url, ping_interval=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
            if hello.get("type") == "error":
                raise RuntimeError(hello.get("message"))
            await ws.send(json.dumps({
                "type": "agent_register", "agent_id": agent_id,
                "hostname": "probe", "platform": "windows", "version": "1.0.0",
            }))
            await ws.send(json.dumps({"type": "heartbeat", "agent_id": agent_id}))
            # give the server a moment to ack
            try:
                await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def probe_send_frame(self, camera_id: str, camera_name: str, jpeg: bytes) -> None:
        async with websockets.connect(self.url, ping_interval=None) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)  # hello
            await ws.send(json.dumps({
                "type": "agent_register", "agent_id": self.agent_id, "hostname": "probe", "platform": "windows", "version": "1.0.0",
            }))
            await ws.send(json.dumps({
                "type": "camera_status", "camera_id": camera_id, "name": camera_name, "status": "online",
            }))
            await ws.send(json.dumps({
                "type": "frame", "camera_id": camera_id, "name": camera_name, "seq": 1, "ts": 0,
                "jpeg_b64": base64.b64encode(jpeg).decode("ascii"),
            }))
