"""Smoke test: local_agent plate_detected message → backend persistence."""
import asyncio
import base64
import json

import websockets

WS_URL = "wss://vehicle-command-17.preview.emergentagent.com/api/agent/ws"
SECRET = "vashu-agent-secret-change-me-2026"
AGENT_ID = "probe-plate-detector"
FAKE_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c283728292c30313432271f393d3830393530312effc0000b0801000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc4001f0100030101010101010101010000000000000102030405060708090a0bffda0008010100003f00fbd0ffd9"
)


async def run() -> int:
    url = f"{WS_URL}?agent_id={AGENT_ID}&secret={SECRET}"
    async with websockets.connect(url, ping_interval=None) as ws:
        hello = await asyncio.wait_for(ws.recv(), timeout=8)
        print("[TEST] hello:", hello[:80])
        await ws.send(json.dumps({
            "type": "agent_register", "agent_id": AGENT_ID,
            "hostname": "probe", "platform": "linux", "version": "test",
        }))
        await asyncio.wait_for(ws.recv(), timeout=5)

        # Simulate local YOLO+OCR detection
        await ws.send(json.dumps({
            "type": "plate_detected",
            "camera_id": "PROBE-CAM-01",
            "plate": "KA05MG9999",
            "confidence": "high",
            "crop_jpeg_b64": base64.b64encode(FAKE_JPEG).decode("ascii"),
        }))
        ack = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[TEST] plate_ack:", ack)
        data = json.loads(ack)
        assert data.get("type") == "plate_ack"
        assert data.get("plate") == "KA05MG9999"
        print("[TEST] ✅ plate_detected handler works end-to-end")

        # Send a duplicate quickly — should get duplicate=true
        await ws.send(json.dumps({
            "type": "plate_detected",
            "camera_id": "PROBE-CAM-01",
            "plate": "KA05MG9999",
            "crop_jpeg_b64": "",
        }))
        ack2 = await asyncio.wait_for(ws.recv(), timeout=10)
        print("[TEST] dedup ack:", ack2)
        d2 = json.loads(ack2)
        assert d2.get("duplicate") is True, "second call should be flagged duplicate"
        print("[TEST] ✅ 30s dedup working")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(asyncio.run(run()))
    except Exception as e:
        print(f"[TEST][FAIL] {e}")
        sys.exit(1)
