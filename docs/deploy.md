# Deploying to your own server (VPS)

One small Linux server runs everything: the dashboard and API behind Caddy
(automatic HTTPS), plus MongoDB. Snapshots and nightly backups go to an
S3-compatible bucket. The showroom PC runs the camera agent and connects out
to the server; nothing at the showroom needs to be reachable from the
internet.

```
Showroom PC (agent) ──wss──▶  Caddy :443 ──▶ backend :8001 ──▶ MongoDB
                               │                    └──▶ S3 bucket (snapshots, backups)
Browsers ─────https──────────▶ └── dashboard (static)
```

## 1. Server

- **Size:** Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, 40 GB SSD. The backend runs no
  machine-learning models, so this is plenty for ~1,000 movements a day.
- **DNS:** point an A record (for example `gate.yourdomain.com`) at the server's
  IP.
- **Firewall:** open only SSH, 80 and 443. Caddy uses port 80 for the
  certificate challenge and redirects it to HTTPS.
  ```bash
  sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443 && sudo ufw enable
  ```
- **Docker:** install Docker Engine and the compose plugin by following
  docs.docker.com/engine/install/ubuntu/.

## 2. Bucket (snapshots + backups)

Create a private bucket on AWS S3 (Mumbai `ap-south-1`), Cloudflare R2 or any
S3-compatible service. Then:

- Create an access key limited to that bucket, with permission to put, get,
  delete and list objects.
- Add a lifecycle rule that deletes `vehicle-command/backups/` objects older
  than 30 days. This is a safety net: the app already deletes backups beyond
  `BACKUP_KEEP`.

## 3. Configure

```bash
git clone <your repository> vehicle-command && cd vehicle-command/deploy
cp .env.example .env                  # DOMAIN, MongoDB root user + password
cp backend.env.example backend.env    # JWT secret, first admin, bucket, email, Google
chmod 600 .env backend.env
```

`backend.env`:

- `JWT_SECRET`: generate it with `python3 -c "import secrets; print(secrets.token_hex(32))"`.
- `ADMIN_EMAIL` / `ADMIN_PASSWORD`: used only to create the first admin, at
  least 10 characters. After the first sign-in, change the password from the
  user menu.
- `STORAGE_BACKEND=s3` and the `S3_*` settings.
- Email for the daily digest and alerts: `EMAIL_PROVIDER=resend` (with
  `RESEND_API_KEY`) or `smtp`, and `EMAIL_FROM`.
- Optional Google sign-in: create an OAuth client ("Web application") in Google
  Cloud with the redirect URI `https://<DOMAIN>/api/auth/google/callback`, then
  set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

The backend checks its settings at start-up. If anything is missing or unsafe,
it refuses to start and lists every problem in `docker compose logs backend`.

## 4. Start

```bash
docker compose up -d --build
docker compose ps                 # all three services "running" / backend "healthy"
docker compose logs -f backend    # "Seeded admin user", "[scheduler] jobs: ..."
```

Open `https://<DOMAIN>`. The certificate is issued automatically on the first
visit, which can take up to a minute.

## 5. Connect the showroom PC

1. In the dashboard, go to **Settings → Camera agents → Register agent** (for
   example `showroom-pc-01`) and copy the secret it shows.
2. On the PC, put these in `local_agent/.env`:
   ```
   VASHU_AGENT_WS_URL=wss://<DOMAIN>/api/agent/ws
   VASHU_AGENT_ID=showroom-pc-01
   VASHU_AGENT_SECRET=<the secret>
   ```
3. Run `start_agent.bat --test` (every check should pass). Then install the
   service with `install_service.bat` (see `local_agent/README.md`).
4. In **Settings → Cameras**, set each camera to **Entry** or **Exit**.

## 6. Moving from the Emergent deployment

1. **Database:** `mongodump --uri "<old MONGO_URL>" --db <old DB_NAME> --archive=old.gz --gzip`.
   Copy the file to the server, then:
   ```bash
   docker compose exec -T mongo mongorestore --username vcadmin --password '<pw>' \
     --authenticationDatabase admin --archive --gzip --nsFrom='<old>.*' --nsTo='vehicle_command.*' < old.gz
   ```
2. **Old snapshots:** put the old `EMERGENT_LLM_KEY` in `backend.env`, run
   `docker compose up -d`, then:
   ```bash
   docker compose exec backend python maintenance.py migrate-snapshots          # dry run
   docker compose exec backend python maintenance.py migrate-snapshots --apply
   ```
   Remove `EMERGENT_LLM_KEY` again once it reports everything is in place.
3. **Open visits:** if the log mentions duplicate open visits, run
   `docker compose exec backend python maintenance.py close-duplicate-active --apply`.
4. **Passwords:** the old admin password (`admin123`) is not carried over as
   "known". Reset it from the Users page.

## 7. Smoke checklist (after every deployment)

- [ ] `https://<DOMAIN>` loads with a valid certificate. `http://` redirects to it.
- [ ] Password sign-in works, and Google sign-in works if configured.
- [ ] `https://<DOMAIN>/api/health` returns `{"ok": true}`.
- [ ] Live Monitoring shows the showroom cameras.
- [ ] A test car driven through the entry lane appears as **Entry** with a
      snapshot, and through the exit lane as **Exit** on the same visit.
- [ ] **Settings → Camera agents** shows the PC online with outbox depth 0.
- [ ] The next morning: the digest email arrived and the backups list shows
      last night's backup.

## Monitoring

- **Built-in alerts:** problems appear as a banner on every dashboard page, and
  are sent by email (to every admin, or `ALERT_EMAILS`) and WhatsApp (Meta,
  `ALERT_WHATSAPP_TO`) when those are configured. Each alert is sent when it
  starts, every 6 hours while it continues, and once when it is fixed. The
  rules:
  - a camera or the showroom PC is offline for more than 2 minutes
  - more than 20 plate reads are waiting to upload
  - more than 10 reads are waiting in Plate Review, or one has waited over an hour
  - the snapshot disk is low
  - a backup failed, or there has been none for 36 hours
  - cars have been recorded inside for too long
- **Uptime monitor:** point a free external monitor (UptimeRobot, Better Stack…)
  at `https://<DOMAIN>/api/ready`. It returns 200 only when the database and the
  storage both answer, so it also catches the case where the whole server is
  down and can't send its own alerts.
- **Logs:** `docker compose logs backend` (JSON lines with `LOG_FORMAT=json`).
  For error tracking, set `SENTRY_DSN` in `backend.env` and in the agent's
  `.env` (Sentry's free tier is enough).

## Updating

```bash
git pull && cd deploy && docker compose up -d --build
```

The database, snapshots and certificates live in Docker volumes and survive
rebuilds.
