# Vashu Local Camera Agent (Windows)

Runs on the showroom PC. Reads local cameras (USB / HTTP MJPEG / RTSP / MediaMTX)
and pushes JPEG frames outbound over a secure WebSocket to the Vashu cloud
backend so cameras on private IPs (10.x, 192.168.x, 172.16.x) can be viewed on
the deployed dashboard.

```
Camera → Local Agent → outbound WSS → Vashu Cloud → CameraManager → CameraGrid
```

## Install (Windows 10 / 11)

```bat
install_agent.bat
```

The installer creates `.venv`, installs `requirements.txt`, and copies
`.env.example` → `.env` if missing.

Edit `.env` and set at minimum:

```
VASHU_AGENT_WS_URL=wss://vehicle-command-17.emergent.host/api/agent/ws
VASHU_AGENT_ID=showroom-pc-01
VASHU_AGENT_SECRET=<the secret set on the cloud backend>
CAMERA_ID=CAM-01
CAMERA_NAME=Phone Camera
CAMERA_SOURCE=http://10.94.184.228:8080/video
```

## Start

```bat
start_agent.bat
```

## Self-test

```bat
start_agent.bat --test
```

Runs 7 checks: camera open, frame capture, JPEG encode, cloud WSS, auth, heartbeat, frame transmit.

## Camera source formats

| Type            | Example                                                            |
|-----------------|--------------------------------------------------------------------|
| USB webcam      | `0` / `1` / `usb:0`                                                |
| HTTP MJPEG      | `http://10.94.184.228:8080/video` (Android IP Webcam)              |
| RTSP camera     | `rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101`         |
| RTSP NVR/DVR    | same as RTSP                                                       |
| MediaMTX RTSP   | `rtsp://127.0.0.1:8554/phone`                                      |

RTSP credentials stay only in `.env` on this PC — never sent to the frontend.

## Run as a Windows service (recommended for production)

Download [NSSM](https://nssm.cc), put `nssm.exe` next to this file, then from an
**Administrator** command prompt:

```bat
install_service.bat           :: install + start "VashuCameraAgent"
install_service.bat remove    :: stop + uninstall
```

The service starts at boot and restarts itself 10 s after any crash. Put the PC
on a UPS and wired Ethernet.

## Agent ID and secret

Register the agent in the dashboard (**Settings → Camera agents → Register
agent**). Copy the ID and the secret shown once into `.env`
(`VASHU_AGENT_ID`, `VASHU_AGENT_SECRET`). Unregistered IDs or a wrong secret are
rejected (WS close code 4401). The credentials are sent in request headers,
never in the URL.

## No detection is lost

Every plate detection is written to `data/outbox.sqlite3` first and removed
only when the server confirms it. If the internet or the server is down, the
agent keeps the detections and sends them, oldest first, when it reconnects —
with the time the car was actually seen. The server ignores re-sends it has
already processed, so a car is never recorded twice. The outbox depth is sent in
every heartbeat (dashboard → Camera agents).

## Detection speed

Detection reads the same frames as the live stream (no second camera
connection) at up to `DETECTION_FPS` (default 5) frames per second per camera.
The models run in a background thread, so streaming never stalls; on a slow PC
the rate drops on its own. While the gate is empty and nothing moves, the models
are skipped (`MOTION_THRESHOLD`, checked at least every `IDLE_CHECK_SECONDS`).
Every plate in view is tracked separately, so two cars at once are both read.

## Live Monitoring flow

Once running, the existing Live Monitoring page shows the agent camera as
`CAM-01 · Phone Camera · Online` and streams the JPEG frames pushed by this
agent through the existing `/api/ws/camera/CAM-01` channel. No UI or backend
architecture change was required beyond adding this outbound path.

## Logs

`logs/agent.log` — rotates at 5 MB, keeps 5 files. Sensitive fields (RTSP credentials, agent
secret) are never logged.

## Troubleshooting

| Symptom                                | Fix                                                              |
|----------------------------------------|------------------------------------------------------------------|
| `unauthorized` on connect              | `VASHU_AGENT_SECRET` mismatch — check both sides                 |
| Camera not opening                     | Verify `CAMERA_SOURCE` in a browser / VLC first                  |
| Reconnect loop every 5-30 s            | Cloud backend or laptop internet down — agent will recover       |
| High CPU                               | Lower `AGENT_FPS` / `FRAME_WIDTH` / `FRAME_HEIGHT` / `JPEG_QUALITY` |


## Number-plate recognition (free, offline)

```bash
./install_ocr.sh                          # one time — installs everything below
```

| Stage | Engine | Licence | Cost |
|-------|--------|---------|------|
| Find the plate | open-image-models YOLOv9 plate detector | MIT | Free |
| Read the plate | fast-plate-ocr global model (~25 ms) | MIT | Free |
| Second opinion when unsure | **Awiros ANPR-OCR** (trained on Indian plates; slower, 0.5–4 s on CPU) | Apache-2.0 | Free |
| Last resort | EasyOCR (optional, not installed by default) | Apache-2.0 | Free |

Each read is format-checked (Indian state codes, BH series, O→0 / I→1 fixes),
voted across several frames, and sent with a confidence. Uncertain reads go to
**Plate Review** in the dashboard instead of being saved automatically.

Measure accuracy on your own camera images (name each file after its plate,
e.g. `KA05MG9999.jpg`):

```bash
.venv/bin/python benchmark_ocr.py ~/Desktop/plate-photos
```

To make Awiros read every plate instead, set `OCR_ENGINE=awiros` in `.env` —
compare both with `benchmark_ocr.py` on your own photos first.
