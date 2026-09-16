# PRD — RDX Car Showroom Management System

## Original Problem Statement
Premium AI-powered Car Showroom Vehicle Management System for a modern automobile dealership. Luxury dark theme with blue (#2563EB) and white accents, glassmorphism UI, Poppins font, Lucide icons, Framer Motion animations, desktop + tablet optimized. Admin-only login. Dashboard with 6 metric cards, live camera placeholder, recent-vehicles table, analytics charts, report exports (Excel/PDF/CSV), settings, and user management. 180-day data retention.

## Architecture
- **Backend**: FastAPI + Motor(MongoDB) + PyJWT + bcrypt. JWT via httpOnly cookies + bearer fallback. openpyxl + reportlab for exports.
- **Frontend**: React 19 + React Router + Tailwind + shadcn/ui + framer-motion + recharts + sonner. Poppins font.
- **Database**: MongoDB collections — `users`, `vehicles`, `settings`. Indexes on `users.email` (unique), `vehicles.vehicle_number`, `vehicles.status`, `vehicles.created_at`.

## User Personas
- **Admin** — only role able to log in; full access.
- **Manager / Security Guard** — listed under Users management; login gating for these roles reserved for phase 2.

## Core Requirements (static)
- Beautiful admin login with animated background, remember me, forgot password link.
- Dashboard: 6 stat cards (Today's Entry, Today's Exit, Cars Inside, Total Vehicles, Visitors Today, Monthly Visitors) with icon + number + % change + trend graph.
- Live Camera section — placeholder for CCTV/IP feed with online status.
- Recent Vehicles table + Vehicle Records CRUD with search/date/status filters and status colour codes (green/gray/yellow).
- Vehicle Details popup with owner, contact, entry/exit time, total parking duration.
- Analytics page: daily/weekly/monthly line + bar + pie charts.
- Reports page: daily/weekly/monthly generation with Excel/PDF/CSV exports.
- Settings page: company, camera, database, retention (180 days), WhatsApp report time, backup.
- Users page: list of admin/manager/security personnel with role/status/last login.
- 180-day auto retention policy (setting exposed; enforcement queued for phase 2 job).
- Footer: `© 2026 hyundai Car Showroom Management System`.

## What's Been Implemented (2026-02-07)
- JWT admin auth (login/logout/me/refresh) with bcrypt hashing, httpOnly cookies, bearer fallback.
- Admin seeded (`admin@rdx.com` / `admin123`) + 3 demo users + 42 demo vehicle records.
- Dashboard, Live Monitoring, Vehicle Records, Entry History, Exit History, Analytics, Reports, Settings, Users pages — all navigable and functional.
- Working CSV/XLSX/PDF report exports for daily/weekly/monthly windows.
- Vehicle CRUD (list/filter/search/date, create/update/delete) with details dialog.
- Settings persistence (`settings.app`) and Users add/delete.
- Full test suite: backend 30/30 pytest, frontend flows verified by testing agent.

## P1 Backlog (next phase)
- CCTV/RTSP camera feed with WebRTC preview.
- AI Number Plate Recognition (Gemini Vision) auto-logging of entries/exits.
- Twilio WhatsApp + Resend email daily/weekly report delivery.
- Automated 180-day retention worker (currently config-only).
- Password reset flow (endpoints stubbed, wire email delivery).
- Role-based login for manager/security accounts.

## What's Been Implemented (2026-02-08 · Session +3)
### Production Deployment Blockers — RESOLVED
- **ML deps split**: Removed `torch`, `torchvision`, `ultralytics`, `ultralytics-thop`, `triton` from `backend/requirements.txt`. Deployment now installs a lean backend. YOLO capability moved to the showroom PC via `local_agent`:
  - New `local_agent/plate_detector.py` — YOLOv8n + Gemini Vision OCR, lazy-imports so a stream-only agent doesn't need torch.
  - `local_agent/requirements.txt` — adds `ultralytics`, `torch`, `numpy`, `httpx`.
  - `local_agent/config.py` — new env toggles `ENABLE_LOCAL_DETECTION` (default false) + `DETECTION_INTERVAL_SECONDS` (default 3s).
  - `local_agent/agent.py` — parallel `_detect_camera` worker per camera that grabs a frame, runs YOLO+OCR locally, and sends a `plate_detected` WS message to the cloud (with local 30s dedup).
- **New WS message `plate_detected`** on `POST /api/agent/ws` — backend calls `_persist_entry` (already ML-free) to create/dedup the visit_session, upsert master, save snapshot, and broadcast on the event bus. Verified with `tests/test_plate_detected_ws.py`.
- **Legacy `drop_collection('vehicles')` removed entirely** from `seed_demo` — no code path can drop a Mongo collection anymore.
- **Retention loop safe start**: `_retention_loop` now sleeps `startup_grace_seconds=3600` before the first purge and can be disabled via `RETENTION_ENABLED=false` env.
- **N+1 killed** in `/vehicles/search` (batched `$group` aggregations), `/visit-sessions` and legacy `/vehicles` list (`_batch_master_map` single `$in`).
- **`.gitignore` no longer blocks `.env` files** — deploy manifest can pick them up.

### Architecture (post-fix)
```
Android IP Camera → Local Agent (Windows PC, YOLO + Gemini OCR)
                    ↓ WSS `plate_detected` messages
                Emergent Backend (ML-free, just persistence + broadcast)
                    ↓
                MongoDB + Dashboard
```

## P2 Backlog
- **Auto Master Enrichment**: When YOLO+Gemini records an entry for a brand-new plate, Live Monitoring now pops a modal (`AutoMasterEnrichModal.jsx`) with the plate + entry snapshot and prompts the operator for Owner Name / Phone / Model. Saved via existing `PATCH /api/vehicles/master/{vn}`. New plates arriving in bursts are queued so the operator handles them one at a time.
- **Camera Snapshots Gallery**: `VehicleDetail.jsx` now shows a horizontal strip of the latest entry snapshots (`/api/snapshots/*.jpg`) with click-to-enlarge lightbox for quick visual audit. Auto-hides when a vehicle has none.
- **Daily Digest Email** (Emergent-managed Resend):
  - `backend/email_utils.py` — playbook-compliant guardrail gate (G2/G3), `EMAIL_FROM_NAME="Vashu Hyundai"` from env.
  - `POST /api/cron/digest` — Bearer-auth (`WEBHOOK_CRON_SECRET`), idempotent by run_id, immediately acks 2xx and queues the send.
  - `POST /api/cron/digest/preview` — admin-only manual trigger (returns stats + recipients).
  - `.emergent/crons.yml` — cadence `0 9 * * *` Asia/Kolkata.
  - Recipients = admin+manager users in DB ∪ `settings.digest_extra_emails` (editable via existing `PUT /api/settings`).
  - HTML template: yesterday's Entries · Exits · Unique Vehicles · Avg Dwell · Longest Dwell · Busiest Camera.
- **Live Occupancy Chart**: New `LiveOccupancyChart.jsx` on the Dashboard shows a rolling 2-hour sparkline of cars inside; polls `/api/dashboard/stats` every 30s and pushes an immediate sample whenever an entry.recorded event bumps `stats.cars_inside.value`.

## What's Been Implemented (2026-09-16)
### Hands-Free ANPR — Manual Enrich Popup Removed
- Deleted `frontend/src/components/AutoMasterEnrichModal.jsx` and all its wiring in `LiveMonitoring.jsx` (`pendingEnrich` queue, `resolveEnrich`, modal render). New plates no longer trigger any popup — 100% hands-free.
- Backend unchanged and verified: `plate_pipeline.py` auto-creates `vehicle_masters` with `owner_name="Unknown Owner"`, blank phone/model, and persists the `visit_session` with entry/exit timestamps + vehicle/plate snapshots immediately.
- Staff see only a transient success toast ("plate · Unknown Owner · Entry recorded", 4.5s). Owner details remain editable later from Vehicle Records (`PATCH /api/vehicles/master/{vn}`).
- Verified: `tests/test_entry_exit_e2e.py` (entry → 30s dedup → exit → snapshots → Unknown Owner) ALL PASS; `tests/test_plate_detected_ws.py` PASS; Live Monitoring screenshot confirms no modal.

## Test Credentials
See `/app/memory/test_credentials.md`.
