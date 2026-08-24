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
- Real-time push (SSE/WebSocket) to update dashboard stats.
- Company-wide multi-branch tenancy.
- Advanced dashboard filters (branch, vehicle model, dwell time).
- Excel automation for vehicle imports.
- Add `type: "entry_saved"` companion event on `/api/ws/events` so Live Monitoring pops a toast per plate capture.

## What's Been Implemented (2026-02-08)
- **Cloudflare-safe Local Camera Agent transport**:
  - Local Agent (`/app/local_agent/`) now sends camera frames as **binary WebSocket frames** with a length-prefixed JSON header, avoiding Cloudflare's large text-frame limits that were dropping the connection with HTTP 520 / WS 1006 on production.
  - Backend `/api/agent/ws` accepts both binary (preferred) and legacy base64 JSON (backward-compatible) frame paths.
  - Added application-level ping/pong (~10s) on top of websockets `ping_interval=15` to keep idle Cloudflare proxies from killing the socket.
  - Default agent config lowered to 960×540 @ JPEG q55 @ 8 fps for safer proxy compatibility.
  - Failed `safe_send` now closes the socket to trigger the built-in reconnect backoff instead of silently swallowing the error.
- Verified with a python WS client against the preview URL: binary frame ingest, legacy base64 frame ingest, ping→pong, 12-frame burst, heartbeat, and clean disconnect — all pass.
- **Production redeploy required** to push this fix live.

## Test Credentials
See `/app/memory/test_credentials.md`.
