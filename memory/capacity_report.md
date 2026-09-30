# Capacity Audit — 50 Cars Inside + 1,000 Movements/Day
**Date:** 2026-09-24 · **Scope:** read-only audit of preview environment · **No production code/DB modified**

## 1. Measured current state (preview)

### Database (MongoDB 7.0.43, pymongo 4.6.3)
| Collection | Docs | Avg doc size | Storage |
|---|---|---|---|
| visit_sessions | 2,330 | 379 B | 548 KB |
| vehicle_masters | 38 | 385 B | 44 KB |
| users | 7 | 295 B | 36 KB |
| whatsapp_log | 0 | — | — |
| user_sessions | 1 | 242 B | 24 KB |
| settings / agents | 2 / 8 | ~270 B | ~36 KB |

- Session history spans 2026-03-30 → 2026-09-24 (~178 days ≈ full 180-day retention window). Currently 15 active (inside) sessions.
- Indexes (adequate): `visit_sessions`: vehicle_number, entry_time, visit_date, status, (vehicle_number+entry_time) compound. `vehicle_masters`: vehicle_number (unique), phone, customer_id. `users`: email (unique). `whatsapp_log`: only `_id` (fine at current volume).

### Snapshot storage
- `/app/backend/snapshots/`: 49 files, 1.6 MB total, avg ~29 KB, largest 182 KB.
- Real-camera estimates (agent q78 vehicle crop + q85 plate crop): vehicle crop ~60–180 KB, plate crop ~10–40 KB → **~150 KB/movement** conservative.

### Host resources (preview container)
- Disk: `/app` volume = **9.8 GB total, 3.0 GB available (70% used)**; overlay `/` = 121 GB with 83 GB free.
- RAM: 32 GB total, ~7.3 GB available.
- Backend: single uvicorn worker (`--workers 1`), hot-reload on.

## 2. Target load model
- 50 vehicles simultaneously inside (active sessions = 50).
- 1,000 movements/day (entries+exits) ≈ 0.7/min average, ~2–3/min at rush-hour peak.

## 3. Measured capacity verdicts

| Area | Verdict | Evidence |
|---|---|---|
| DB data volume | ✅ PASS (~100× headroom) | 1,000/day × 180d retention = 180k docs ≈ 68 MB data + indexes ≈ <200 MB total. Trivial for Mongo. |
| Write throughput | ✅ PASS | `_persist_entry` = 4–6 indexed ops + 2 file writes (<20 ms). Peak 3/min uses <0.1% of single-worker capacity. |
| 50 concurrent inside | ✅ PASS | `count_documents({status:"active"})` on indexed field; 50 vs current 15 is nothing. |
| Live camera streaming | ✅ PASS | Latest-frame-only in memory (no accumulation), 15 fps push, WS binary header capped at 8 KB; ~0.5–1.6 Mbps per viewer-camera. |
| Event bus fan-out | ✅ PASS | Per-subscriber queue maxsize=200 with drop+warn; 1 event/movement ≪ capacity. |
| Local agent OCR | ✅ PASS | Detection every 3 s/camera at the edge; backend receives only small JSON+crops. |
| **Snapshot disk on `/app`** | ❌ **FAIL — primary bottleneck** | 1,000 mov/day × ~150 KB ≈ **150 MB/day → 3.0 GB free exhausted in ~20 days**. 180-day steady state would need ~27 GB. |
| **Retention disk purge** | ⚠️ **PARTIAL — measured leak** | `_retention_loop` deletes only `entry_image`/`exit_image` files. **`entry_plate_image`/`exit_plate_image` are never unlinked** → plate crops (~10–40 KB × 2,000/day ≈ 20–80 MB/day) accumulate on disk forever even after DB purge. |
| List/report row caps | ⚠️ **PARTIAL — silent truncation at target volume** | Entry/Exit/Vehicle lists default `limit=500` (1,000 mov/day exceeds it daily); report exports cap `to_list(5000)`/`to_list(10000)` — a 30-day month at 1,000/day = 30k rows → exports truncate at 5k–10k rows silently. |
| whatsapp_log / user_sessions | ✅ PASS (note) | Unbounded but tiny (≈200–400 B/doc); years to matter. |
| Memory | ✅ PASS | ~7 GB free; per-camera footprint = 1 latest frame in RAM. |

## 4. Bottleneck summary (measured, not fixed)
1. **Snapshot disk (P0):** `/app` free space (3.0 GB) supports only ~20 days at 1,000 movements/day. Steady-state 180-day retention needs ~27 GB of snapshot storage.
2. **Retention plate-image leak (P1):** plate-crop files are excluded from the purge loop → unbounded disk growth even with retention enabled.
3. **Row caps (P2):** 500-row list defaults and 5,000/10,000-row export caps silently truncate at the 1,000/day target.

## 5. Recommendations (NOT implemented — audit only)
- Move snapshots to Emergent Object Storage (or a larger persistent volume) and store URLs — removes the P0 disk wall entirely.
- Extend `_retention_loop` to also unlink `entry_plate_image`/`exit_plate_image` files.
- Add pagination (or raise caps with explicit `limit` params) on history lists and report exports.
- Optional: cap WS JPEG size defensively (e.g. reject frames >2 MB) — currently relies on uvicorn's 16 MB default.

## 6. Notes
- Preview measurements; production disk layout may differ — re-measure on the production volume before go-live.
- No code, config, or database changes were made during this audit.
