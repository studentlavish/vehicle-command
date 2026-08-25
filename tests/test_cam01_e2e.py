"""End-to-end proof for the CAM-01 agent-fed architecture.

Simulates the local agent's exact behaviour: connect → register → send binary
frames → confirm the frontend WS endpoint receives them → confirm /api/cameras
shows exactly one CAM-01 → confirm attempts to overlay a direct-URL capture
on CAM-01 (or its lowercase form cam-01) are rejected.
"""
import asyncio
import json
import os
import struct
import sys
from urllib.parse import quote

import httpx
import websockets


BASE = "https://vehicle-command-17.preview.emergentagent.com"
WSS_AGENT = f"wss://vehicle-command-17.preview.emergentagent.com/api/agent/ws"
WSS_CAM = f"wss://vehicle-command-17.preview.emergentagent.com/api/ws/camera/CAM-01"
SECRET = "vashu-agent-secret-change-me-2026"
AGENT_ID = "showroom-pc-01"
BINARY_HEADER = struct.Struct(">I")


def make_jpeg(kb: int = 16) -> bytes:
    minimal = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c283728292c30313432271f393d3830393530312effc0000b0801000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc4001f0100030101010101010101010000000000000102030405060708090a0bffda0008010100003f00fbd0ffd9"
    )
    return minimal + b"\x00" * max(0, kb * 1024 - len(minimal))


def pack(header: dict, jpeg: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return BINARY_HEADER.pack(len(h)) + h + jpeg


async def _login(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{BASE}/api/auth/login", json={"email": "admin@rdx.com", "password": "admin123"})
    r.raise_for_status()


async def _cameras(client: httpx.AsyncClient) -> dict:
    r = await client.get(f"{BASE}/api/cameras")
    r.raise_for_status()
    return r.json()


async def agent_task(stop_evt: asyncio.Event) -> None:
    """Mimic the real local agent: register + push binary frames continuously."""
    url = f"{WSS_AGENT}?agent_id={AGENT_ID}&secret={SECRET}"
    async with websockets.connect(url, ping_interval=15, max_size=20 * 1024 * 1024) as ws:
        await asyncio.wait_for(ws.recv(), timeout=8)  # hello
        await ws.send(json.dumps({
            "type": "agent_register", "agent_id": AGENT_ID,
            "hostname": "showroom-pc-01", "platform": "windows", "version": "e2e-test",
        }))
        await asyncio.wait_for(ws.recv(), timeout=5)  # registered
        # camera_status announcement
        await ws.send(json.dumps({
            "type": "camera_status", "camera_id": "CAM-01",
            "name": "Phone Camera", "status": "online",
        }))
        seq = 0
        while not stop_evt.is_set():
            seq += 1
            hdr = {"type": "frame", "camera_id": "CAM-01", "name": "Phone Camera", "seq": seq, "ts": seq}
            await ws.send(pack(hdr, make_jpeg(kb=12)))
            await asyncio.sleep(0.1)  # ~10 fps


async def run() -> int:
    stop = asyncio.Event()
    ok = True

    # 1. Kick off the fake local agent in the background
    agent = asyncio.create_task(agent_task(stop))
    await asyncio.sleep(1.5)  # let it register and push a few frames

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        await _login(client)

        # 2. Assert /api/cameras contains exactly one authoritative CAM-01
        cams = await _cameras(client)
        cam_list = list(cams.values()) if isinstance(cams, dict) else cams
        cam01 = [c for c in cam_list if c.get("camera_id", "").upper() == "CAM-01"]
        assert len(cam01) == 1, f"expected 1 CAM-01, got {len(cam01)}: {cam01}"
        s = cam01[0]
        assert s["status"] == "online", f"CAM-01 status={s['status']}"
        assert s["source"].startswith("agent:"), f"source={s['source']} (should be agent:...)"
        assert AGENT_ID in s["source"], f"source={s['source']}"
        print(f"  ✅ /api/cameras has ONE authoritative CAM-01: {s['source']} status={s['status']} frames={s['frames_captured']}")

        # 3. Attempt to overlay direct-URL capture — MUST be rejected (409)
        for attempted_id in ("CAM-01", "cam-01", "Cam-01"):
            r = await client.post(f"{BASE}/api/cameras/connect", json={
                "camera_id": attempted_id, "source": "http://192.168.31.201:8080/video", "label": "Front Gate",
            })
            assert r.status_code == 409, f"[{attempted_id}] expected 409, got {r.status_code}: {r.text}"
            body = r.json()
            assert "agent-fed" in body.get("detail", "").lower(), f"unexpected detail: {body}"
            print(f"  ✅ /api/cameras/connect rejected direct-URL for id={attempted_id!r}: HTTP 409")

        # 4. Subscribe as a browser to /api/ws/camera/CAM-01 → confirm binary frames arrive
        cookies = {c.name: c.value for c in client.cookies.jar}
        async with websockets.connect(
            WSS_CAM,
            additional_headers=[("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))],
            max_size=10 * 1024 * 1024,
        ) as ws_browser:
            frames_received = 0
            deadline = asyncio.get_event_loop().time() + 6.0
            while asyncio.get_event_loop().time() < deadline and frames_received < 3:
                try:
                    msg = await asyncio.wait_for(ws_browser.recv(), timeout=2)
                except asyncio.TimeoutError:
                    break
                if isinstance(msg, (bytes, bytearray)):
                    frames_received += 1
            assert frames_received >= 3, f"browser WS only received {frames_received} frames (expected ≥3)"
            print(f"  ✅ /api/ws/camera/CAM-01 delivered {frames_received} binary JPEG frames to the browser")

        # 5. Reconnect scenario — stop agent, restart, ensure no duplicate
        stop.set()
        try:
            await asyncio.wait_for(agent, timeout=5)
        except (asyncio.TimeoutError, Exception):
            agent.cancel()
        await asyncio.sleep(1)

        stop_2 = asyncio.Event()
        agent2 = asyncio.create_task(agent_task(stop_2))
        await asyncio.sleep(1.5)
        cams2 = await _cameras(client)
        cam_list2 = list(cams2.values()) if isinstance(cams2, dict) else cams2
        cam01_2 = [c for c in cam_list2 if c.get("camera_id", "").upper() == "CAM-01"]
        assert len(cam01_2) == 1, f"AFTER RECONNECT expected 1 CAM-01, got {len(cam01_2)}"
        print(f"  ✅ after agent reconnect, still exactly ONE CAM-01 (no duplicate)")

        stop_2.set()
        try:
            await asyncio.wait_for(agent2, timeout=5)
        except Exception:
            agent2.cancel()

        # 6. Cleanup — remove any lingering CAM-01 in preview so subsequent
        #    manual tests start clean.
        await client.delete(f"{BASE}/api/cameras/CAM-01")

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except AssertionError as e:
        print(f"  ❌ FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        sys.exit(1)
