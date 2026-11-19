# Gate hardware and camera placement

The camera matters more than any software setting. A sharp, evenly lit plate
of the right size is read correctly almost every time; a blurred, glaring or
tiny plate isn't, whatever the software does.

## What to buy

| Item | Specification | Why |
|---|---|---|
| **ANPR / LPR camera**, one per lane (entry, exit) | 2–4 MP, **RTSP** output, **shutter 1/1000 s or faster** (an "LPR mode" or manual shutter), varifocal 2.8–12 mm or 5–50 mm lens, **built-in IR illuminator** (850 nm), WDR ≥ 120 dB, IP66 | A fast shutter removes motion blur. IR gives a clear plate at night without headlight glare. WDR handles sun behind the car |
| Mount / pole | Rigid, no wobble in wind | A shaking camera blurs every frame |
| **PoE switch** (or PoE injector) | One port per camera | One cable for power and data |
| Showroom PC | 4-core CPU (Intel i5 8th gen or newer), 8 GB RAM, SSD, Windows 10/11, **wired Ethernet** | Reads plates at 5 frames/s per camera |
| **UPS** for the PC, switch and router | ~30 min | A power blip must not lose the gate |
| Speed bump or boom barrier, 3–6 m before the camera | — | Cars slow to walking pace, so every car gives several sharp reads |

Phone cameras (the current Android IP-cam setup) are fine for testing, but not
for production. They have no fast shutter or IR, and they overheat and drop
Wi-Fi.

## Placement (each lane)

```
          camera (1.0–1.5 m high, ≤ 30° to the side, ≤ 30° down)
             \
              \        plate fills ≥ 130 px of the image width
               \            ▼
   ────────[speed bump]───[car]──────▶ direction of travel
           3–6 m before the reading spot
```

- **Distance:** set the zoom so the plate is **at least 130 pixels wide** in the
  picture (about 1/8 of a 1080p frame width) where cars slow down.
- **Angle:** at most 30° sideways and 30° downward. Flatter angles distort the
  characters.
- **One direction per camera.** The entry camera faces cars coming in, the exit
  camera faces cars going out. Set the roles in **Settings → Cameras**.
- **Avoid:** pointing into the sun at opening or closing times, reflective
  walls behind the plate, and trees or flags in the view (they trigger motion
  constantly).
- **Two-row plates** (common on commercial vehicles) need about 20 % more
  height in the picture.

## Camera settings

- Shutter: 1/1000 s (1/2000 s if cars don't slow down). Increase the gain or IR
  rather than slowing the shutter.
- IR: on/auto at night. Turn the camera's "smart IR" or exposure compensation
  down if plates glow white.
- Resolution 1920×1080, 15–25 fps, H.264 main stream over RTSP.
- Turn off the camera's own on-screen text in the plate area (time stamps,
  names).

In the agent's `.env` (per camera in `AGENT_CAMERAS_JSON`):

```json
[{"id": "GATE-IN",  "name": "Entry lane", "source": "rtsp://user:pass@192.168.1.21:554/Streaming/Channels/101",
  "width": 1920, "height": 1080, "fps": 10, "roi": [0.15, 0.35, 0.85, 1.0]},
 {"id": "GATE-OUT", "name": "Exit lane",  "source": "rtsp://user:pass@192.168.1.22:554/Streaming/Channels/101",
  "width": 1920, "height": 1080, "fps": 10, "roi": [0.15, 0.35, 0.85, 1.0]}]
```

`roi` is the part of the picture searched for plates, as fractions of the width
and height (x1, y1, x2, y2). Limit it to the lane, so parked cars and the road
behind are ignored.

## Check each camera before the shadow run

- [ ] `start_agent.bat --test` passes for both cameras.
- [ ] Live Monitoring shows both lanes. A car at the reading spot has a sharp
      plate that is at least 130 px wide in a screenshot.
- [ ] Drive a car in and out 10 times by day and 10 times at night. Every pass
      creates exactly one entry and one exit, on the same visit.
- [ ] Two cars one behind the other are both recorded.
- [ ] Unplug the network cable for 5 minutes during a pass, then plug it back
      in. The pass appears, with its real time, within a minute (outbox).
