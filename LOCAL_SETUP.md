# Running Vehicle Command locally (macOS)

Stack: **FastAPI** backend (port 8001) · **React/CRA + craco** frontend (port 3000) · **MongoDB** (port 27017) · optional **camera agent** (`local_agent/`).

## 1. One-time setup

```bash
cd ~/Documents/Personal/lavish/vehicle-command
bash setup.sh
```

This installs (via Homebrew, only if missing): MongoDB Community + Database Tools, Python 3.12, Node 20, Yarn 1.x — then creates `backend/.venv`, runs `yarn install` in `frontend/`, and creates `local_agent/.venv`.

Prefer MongoDB in Docker? `USE_DOCKER_MONGO=1 ./setup.sh` (and the same variable for `./start.sh`).

## 2. Start everything

```bash
bash start.sh            # MongoDB + backend + frontend
bash start.sh --agent    # ...plus the camera agent (uses your Mac webcam by default)
```

- Dashboard: http://localhost:3000
- API docs (Swagger): http://localhost:8001/docs
- Logs: `logs/backend.log`, `logs/frontend.log`, `logs/agent.log`

Ctrl+C stops it. If a port is left busy: `./stop.sh` (`./stop.sh --mongo` also stops MongoDB).

## 3. Logins (seeded automatically on first backend start)

On a fresh (empty) database the first admin is created from `backend/.env`
(`ADMIN_EMAIL`, `ADMIN_PASSWORD` — at least 10 characters, not a common
password, or the backend refuses to start). After that the database owns the
password: restarts never reset it. Change it from the user menu → **Change
password**; an admin resets other users' passwords on the **Users** page.

Five wrong passwords for the same email from the same address lock sign-in
for 15 minutes (`LOGIN_MAX_FAILURES`, `LOGIN_LOCK_MINUTES`).

For local `http://` development set `COOKIE_SECURE="false"` in `backend/.env`;
keep it `true` (the default) in production behind HTTPS.

### Camera agents

Each showroom PC is registered in **Settings → Camera agents**, which shows its
secret once. Put it in that PC's `local_agent/.env` as `VASHU_AGENT_SECRET`
(with the same `VASHU_AGENT_ID`). Unregistered agent IDs are refused. Agents
registered before this existed keep using the shared `VASHU_AGENT_SECRET` until
you press **Rotate secret**.

The app never creates demo users or fake vehicles.

### Clean out old demo data (one time)

Earlier versions added fake vehicles, visits and demo logins automatically. Remove them:

```bash
cd backend
.venv/bin/python maintenance.py remove-demo-data            # shows what would be deleted
.venv/bin/python maintenance.py remove-demo-data --apply    # deletes it
.venv/bin/python maintenance.py fix-visit-dates --apply     # re-dates old visits in India time
```

## 4. Config files (created by setup.sh, git-ignored)

| File | Purpose |
|------|---------|
| `backend/.env` | Mongo URL, DB name, JWT secret (randomly generated), admin creds, CORS, agent secret, optional integration keys |
| `frontend/.env` | `REACT_APP_BACKEND_URL=http://localhost:8001` |
| `local_agent/.env` | Agent → `ws://localhost:8001/api/agent/ws`, camera source (`0` = webcam), same `VASHU_AGENT_SECRET` as backend |

`setup.sh` builds each `.env` from its `.env.example` with fresh random secrets, and never overwrites an existing `.env`.

## 5. Camera agent

- Webcam: `CAMERA_SOURCE=0` (macOS will ask for camera permission for Terminal the first time).
- Phone (IP Webcam app): `CAMERA_SOURCE=http://<phone-ip>:8080/video`
- RTSP: `CAMERA_SOURCE=rtsp://user:pass@192.168.1.10:554/...`
- Self-test: `local_agent/start_agent.sh --test`
- On-device number-plate detection (large download, ~2 GB with PyTorch):
  `local_agent/.venv/bin/pip install -r local_agent/requirements-detection.txt` then set `ENABLE_LOCAL_DETECTION=true`.

## 6. Plate recognition accuracy

Automatic reads go through: OCR → Indian-format check (with O/0, I/1, S/5… fixed by position) → vote across several frames → saved automatically **only when confident**. Anything uncertain lands in **Plate Review** (sidebar) for a guard to confirm against the photo.

Tuning (backend `.env`): `REVIEW_MODE`, `MIN_VISIT_SECONDS`. Agent (`local_agent/.env`): `PLATE_MODEL_PATH`, `VOTE_MIN_READS`, `DETECTION_INTERVAL_SECONDS`.

For best accuracy add a YOLO model trained on number plates as `local_agent/best.pt` — without it the agent reads the whole vehicle image.

## 7. What works offline vs. needs keys

Works locally with no keys: login/users, vehicle records, entry/exit history, analytics, reports (CSV/XLSX/PDF export), live monitoring through the agent, retention cleanup.

Needs cloud keys in `backend/.env` (blank = feature quietly disabled):

| Feature | Variables |
|---------|-----------|
| AI image/plate reading, chat | `EMERGENT_LLM_KEY` (+ `emergentintegrations` package, installed by setup if reachable) |
| Snapshot upload / daily backups to object storage | `EMERGENT_LLM_KEY` |
| Daily digest email | `EMERGENT_EMAIL_KEY` |
| WhatsApp (Twilio) | `TWILIO_*` |
| WhatsApp (Meta Cloud API) | `META_*` |
| "Sign in with Google" | Uses Emergent's hosted auth; may not work from localhost — use email/password locally |

Daily jobs (digest 09:00, backup 03:00, retention 03:30 — `DIGEST_TIME`, `BACKUP_TIME`, `RETENTION_TIME`) run inside the backend. They can also be triggered manually:

```bash
source backend/.env
curl -X POST http://localhost:8001/api/cron/digest -H "Authorization: Bearer $WEBHOOK_CRON_SECRET"
curl -X POST http://localhost:8001/api/cron/backup -H "Authorization: Bearer $WEBHOOK_CRON_SECRET"
```

## 8. Running tests

```bash
cd backend && .venv/bin/pytest tests -q
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `KeyError: 'MONGO_URL'` | `backend/.env` missing → `cp backend/.env.example backend/.env` and fill secrets |
| Backend can't reach Mongo | `brew services restart mongodb-community`, check `nc -z localhost 27017` |
| Login works but you're bounced back to /login | Open the app at `http://localhost:3000` (not 127.0.0.1) so cookies match `CORS_ORIGINS` |
| `yarn install` fails on `@emergentbase/visual-edits` | Remove that line from `frontend/package.json` devDependencies — it's only Emergent's visual editor |
| Port 3000/8001 in use | `./stop.sh` |

## Camera roles (entry / exit)

In **Settings → Cameras** set each camera to **Entry**, **Exit** or **Entry + exit**:

- An **entry** camera only opens visits. If the car still has an open visit
  (its exit was never seen), that visit is marked *Exit not seen*.
- An **exit** camera only closes visits. A read one character away from a single
  car inside closes that car's visit (recorded as a fuzzy match). An exit with
  no open visit goes to Plate Review; once confirmed it is stored as *Entry not seen*.
- **Entry + exit** (the default for unconfigured cameras) toggles on the open
  visit, with `MIN_VISIT_SECONDS` so a car standing in view isn't flipped.

Visits open longer than `MAX_VISIT_HOURS` (default 24) are listed on the
dashboard and in the daily digest. They are never closed automatically.

If the backend logs that it couldn't create the one-open-visit-per-plate index,
run `backend/.venv/bin/python backend/maintenance.py close-duplicate-active`
(dry run), then again with `--apply`.

## Running the tests

```bash
backend/.venv/bin/pip install -r requirements-dev.txt
local_agent/.venv/bin/pip install -r requirements-dev.txt
make check      # lint + all tests + frontend build
make test       # tests only
```

Backend tests need MongoDB running locally (or set `TEST_MONGO_URL`). Each run
uses its own temporary `vc_test_*` database and temporary snapshot folder, and
deletes both when it finishes — your real data is never touched.
