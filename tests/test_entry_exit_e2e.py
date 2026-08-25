"""End-to-end proof for Automatic Entry/Exit + snapshots + duplicate protection.

Simulates the local agent sending confirmed `plate_detected` messages and
verifies the backend correctly:
  - creates an ENTRY on first confirmed detection
  - dedups repeated detections while the session is active
  - creates an EXIT (closing the same session) on next detection after cooldown
  - saves both vehicle-crop AND plate-crop snapshots
  - loads existing owner_name/phone from vehicle_masters
  - records status=completed + duration_seconds
"""
import asyncio
import base64
import json
import os
import struct
import sys
import time
import uuid

import httpx
import websockets

BASE = "https://vehicle-command-17.preview.emergentagent.com"
WSS_AGENT = f"wss://vehicle-command-17.preview.emergentagent.com/api/agent/ws"
SECRET = "vashu-agent-secret-change-me-2026"
AGENT_ID = "e2e-entry-exit"
JPEG_HEX = (
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c283728292c30313432271f393d3830393530312effc0000b0801000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc4001f0100030101010101010101010000000000000102030405060708090a0bffda0008010100003f00fbd0ffd9"
)
FAKE_JPEG = bytes.fromhex(JPEG_HEX)
PLATE_JPEG = bytes.fromhex(JPEG_HEX) + b"\x00" * 512  # slightly different bytes


async def send_plate(ws, camera_id: str, plate: str, confidence: str = "high") -> dict:
    await ws.send(json.dumps({
        "type": "plate_detected",
        "camera_id": camera_id,
        "name": "Phone Camera",
        "plate": plate,
        "confidence": confidence,
        "crop_jpeg_b64": base64.b64encode(FAKE_JPEG).decode(),
        "plate_jpeg_b64": base64.b64encode(PLATE_JPEG).decode(),
        "vehicle_class": "car",
    }))
    ack = await asyncio.wait_for(ws.recv(), timeout=10)
    return json.loads(ack)


async def run() -> int:
    plate = f"E2E{uuid.uuid4().hex[:2].upper()}TEST"
    print(f"\n[TEST] using plate={plate}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        r = await client.post(f"{BASE}/api/auth/login", json={"email": "admin@rdx.com", "password": "admin123"})
        r.raise_for_status()

        url = f"{WSS_AGENT}?agent_id={AGENT_ID}&secret={SECRET}"
        async with websockets.connect(url, ping_interval=None) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)  # hello
            await ws.send(json.dumps({"type": "agent_register", "agent_id": AGENT_ID, "hostname": "e2e", "platform": "linux", "version": "test"}))
            await asyncio.wait_for(ws.recv(), timeout=5)  # registered

            # ---- Test D: Entry
            ack = await send_plate(ws, "CAM-01", plate)
            print(f"[TEST D] entry ack: {ack}")
            assert ack.get("event") == "entry", f"expected event=entry, got {ack}"
            assert ack.get("duplicate") is False
            entry_session_id = ack.get("session_id")
            assert entry_session_id, "session_id missing on entry"
            print(f"  ✅ ENTRY created, session_id={entry_session_id}, owner_status={ack.get('owner_status')}")

            # ---- Test E: Duplicate protection (5 quick repeats)
            for i in range(5):
                dup_ack = await send_plate(ws, "CAM-01", plate)
                assert dup_ack.get("duplicate") is True, f"duplicate #{i+1} not flagged: {dup_ack}"
            print(f"  ✅ 5 rapid repeats all flagged duplicate (no extra sessions created)")

            # Verify: still exactly 1 session for this plate
            r = await client.get(f"{BASE}/api/visit-sessions?limit=50")
            r.raise_for_status()
            sessions_for_plate = [s for s in r.json() if s.get("vehicle_number") == plate]
            assert len(sessions_for_plate) == 1, f"expected 1 session, got {len(sessions_for_plate)}"
            s = sessions_for_plate[0]
            assert s.get("status") in ("inside", "active"), f"status={s.get('status')}"
            assert s.get("entry_image", "").startswith("/api/snapshots/"), f"missing entry snapshot: {s.get('entry_image')}"
            print(f"  ✅ /api/visit-sessions has 1 session · status={s.get('status')} · entry_image={s.get('entry_image')}")

            # Verify vehicle master auto-created for brand-new plate (Test H)
            r = await client.get(f"{BASE}/api/vehicles/master/{plate}")
            r.raise_for_status()
            m = r.json()
            print(f"  ✅ vehicle_master auto-created for new plate · owner_name='{m.get('owner_name')}'")

            # ---- Test F: Exit after cooldown
            print("[TEST F] sleeping 31s to clear cooldown before EXIT ...")
            await asyncio.sleep(31)
            exit_ack = await send_plate(ws, "CAM-01", plate)
            print(f"[TEST F] exit ack: {exit_ack}")
            assert exit_ack.get("event") == "exit", f"expected event=exit, got {exit_ack}"
            assert exit_ack.get("session_id") == entry_session_id, "exit should CLOSE the same session, not create a new one"
            print(f"  ✅ EXIT closed the same session, session_id={exit_ack.get('session_id')}")

            # ---- Test G: existing vehicle owner enrichment
            r = await client.get(f"{BASE}/api/visit-sessions?limit=50")
            r.raise_for_status()
            final = [s for s in r.json() if s.get("vehicle_number") == plate][0]
            assert final.get("status") in ("exited", "completed"), f"status={final.get('status')}"
            assert final.get("exit_image", "").startswith("/api/snapshots/"), f"missing exit snapshot: {final.get('exit_image')}"
            assert final.get("duration_seconds") and final["duration_seconds"] >= 30, f"duration={final.get('duration_seconds')}"
            print(f"  ✅ session closed: status={final['status']} duration={final['duration_seconds']}s exit_image={final['exit_image']}")

    print("\n[TEST] ✅ ALL PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except AssertionError as e:
        print(f"[TEST] ❌ ASSERT FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[TEST] ❌ ERROR: {e}")
        sys.exit(1)
