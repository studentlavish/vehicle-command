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

## P2 Backlog
- Company-wide multi-branch tenancy.
- Advanced dashboard filters (branch, vehicle model, dwell time).
- Excel automation for vehicle imports.

## What's Been Implemented (2026-02-08 · Session +1)
- **Multi-Camera Local Agent**: `AGENT_CAMERAS_JSON` / `AGENT_CAMERAS_FILE` env lets one Windows agent stream N cameras (USB / RTSP / MJPEG) over a single WSS connection. Each entry supports its own `id`, `name`, `source`, `width/height/fps/jpeg_quality`; legacy `CAMERA_ID/NAME/SOURCE` single-camera env is preserved.
- **Entry Toast Event**: Live Monitoring page subscribes to `/api/ws/events` and pops a sonner toast (plate + owner + entry camera) the moment the auto-detection pipeline saves an entry.
- **Agent Health Card**: New `AgentHealthCard.jsx` on the Dashboard polls `/api/agent/status` every 15s and lists each connected local agent with hostname, camera count, last-heartbeat ("Ns ago"/"Nm ago"/"Nd ago") and Online/Offline pill.
- **180-day Retention Worker**: `_retention_loop` runs on startup and every 24h — deletes `visit_sessions.entry_time < now-180d` and unlinks the associated `/api/snapshots/*.jpg` files. Verified: purged 58 stale sessions on boot.

## Test Credentials
See `/app/memory/test_credentials.md`.
