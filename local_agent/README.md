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

## Auto-start on Windows boot (optional)

Task Scheduler → Create Basic Task → Trigger: *At startup* → Action: *Start a program*
→ Program: `C:\path\to\local_agent\start_agent.bat`.

## Backend env var

The Vashu backend must have `VASHU_AGENT_SECRET` set to the same value as `.env`.
Requests without a matching secret are rejected with WS close code 4401.

## Live Monitoring flow

Once running, the existing Live Monitoring page shows the agent camera as
`CAM-01 · Phone Camera · Online` and streams the JPEG frames pushed by this
agent through the existing `/api/ws/camera/CAM-01` channel. No UI or backend
architecture change was required beyond adding this outbound path.

## Logs

`logs/agent.log` — rotates on run. Sensitive fields (RTSP credentials, agent
secret) are never logged.

## Troubleshooting

| Symptom                                | Fix                                                              |
|----------------------------------------|------------------------------------------------------------------|
| `unauthorized` on connect              | `VASHU_AGENT_SECRET` mismatch — check both sides                 |
| Camera not opening                     | Verify `CAMERA_SOURCE` in a browser / VLC first                  |
| Reconnect loop every 5-30 s            | Cloud backend or laptop internet down — agent will recover       |
| High CPU                               | Lower `AGENT_FPS` / `FRAME_WIDTH` / `FRAME_HEIGHT` / `JPEG_QUALITY` |
