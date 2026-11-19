# Operations runbook

Commands run on the server from `vehicle-command/deploy/` unless noted.

## Daily (2 minutes)

- Clear **Plate Review**. Confirm or correct each read against its photo; tick
  *Non-standard plate* for trade or temporary plates.
- Glance at the alert banner. No banner means everything is working.

## When an alert arrives

| Alert | What to check | Fix |
|---|---|---|
| **Camera … is offline** | Camera power (PoE switch), cable, camera web page | Power-cycle the camera; check the RTSP URL in the agent `.env` |
| **Camera PC … is not connected** | PC on? Internet up? Service running (`nssm status VashuCameraAgent`)? | Start the service (`nssm start VashuCameraAgent`); check `local_agent\logs\agent.log` |
| **… plate reads waiting to upload** | PC online but can't reach the server | Check internet / DNS / server. Nothing is lost: they upload when the connection returns |
| **… waiting in Plate Review** | Nobody cleared the queue | Clear it; if it keeps growing, check the camera image quality |
| **Snapshot disk almost full** | `df -h` on the server | Use `STORAGE_BACKEND=s3`, or grow the disk |
| **Last database backup failed / No backup for …** | `docker compose logs backend \| grep backup` | Fix the bucket credentials or space. It runs again the next night; to run it now set `WEBHOOK_CRON_SECRET` in `backend.env` and `curl -X POST https://<DOMAIN>/api/cron/backup -H "Authorization: Bearer <secret>"` |
| **… cars recorded inside for a long time** | Dashboard → Long-open visits | If the car really left, its exit was missed: close the visit from Vehicle Records. If it happens often, check the exit camera |

## Restart things

```bash
docker compose restart backend          # API (agents reconnect on their own)
docker compose up -d                    # start anything that stopped
docker compose logs --tail=200 backend
```

Showroom PC (Administrator prompt): `nssm restart VashuCameraAgent`.

## Replace a camera

1. Mount and aim the new camera as in docs/hardware.md, with the same position
   and zoom.
2. Put its RTSP URL in `local_agent\.env` (`AGENT_CAMERAS_JSON`), **keeping the
   same `id`** so its role (entry/exit) stays.
3. `nssm restart VashuCameraAgent` and check Live Monitoring.
4. Drive one car in and out to confirm.

## Replace the showroom PC

1. Install the agent (`install_agent.bat`, `install_ocr.sh` or the Windows
   steps in `local_agent/README.md`).
2. Copy `.env` from the old PC, or register a new agent in **Settings → Camera
   agents** and use its secret.
3. `install_service.bat`. If the old PC still has `data\outbox.sqlite3` with
   pending detections, copy it over first.

## Backups

- Taken every night at `BACKUP_TIME` into the bucket. The last `BACKUP_KEEP`
  are kept. The list is at `GET /api/backups`.
- **Monthly test restore** (restores into a separate database, never the live
  one):
  ```bash
  docker compose exec backend python maintenance.py restore-backup            # shows what it will do
  docker compose exec backend python maintenance.py restore-backup --apply    # restore into vehicle_command_restore_test
  ```
  Check the document counts it prints. Afterwards, drop the test database:
  `docker compose exec mongo mongosh -u vcadmin -p --authenticationDatabase admin --eval 'db.getSiblingDB("vehicle_command_restore_test").dropDatabase()'`.
- **Real recovery:** restore the chosen backup with
  `--into vehicle_command --apply` while the backend is stopped
  (`docker compose stop backend`), then start it again.

## Rotate secrets (quarterly, or if one may have leaked)

| Secret | How |
|---|---|
| Admin passwords | User menu → Change password; Users page → Reset password for others |
| Agent secret | Settings → Camera agents → Rotate secret, put it in the PC's `.env`, restart the service |
| `JWT_SECRET` | New value in `backend.env`, `docker compose up -d backend`. Everyone signs in again |
| MongoDB password | Change it in mongo (`db.changeUserPassword`), then in `deploy/.env`, then `docker compose up -d` |
| Bucket / email / Google / WhatsApp keys | Rotate at the provider, update `backend.env`, `docker compose up -d backend` |

## Monthly accuracy cycle

See docs/accuracy.md:

1. Export training data.
2. Run the benchmark.
3. Fine-tune if there are enough new corrections.
4. Promote the new model only if it is not worse.

## Quarterly

- Test restore (above), and rotate secrets.
- Update the system: `git pull && docker compose up -d --build`. Before
  updating, run `make check` on a development machine.
- Check the camera lenses (clean them), mounts and IR at night.
