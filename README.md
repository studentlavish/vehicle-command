# Vehicle Command

Automatic gate log for the Vashu Hyundai showroom. Cameras at the entry and
exit lanes read number plates. Every visit is recorded with its entry and exit
times and photos. A person checks only the few reads the system isn't sure
about.

```
local_agent/   runs on the showroom PC: cameras → plate reading → durable outbox → server
backend/       FastAPI + MongoDB: entry/exit logic, review queue, reports, alerts, backups
frontend/      React dashboard
deploy/        Docker Compose stack for a VPS (Caddy HTTPS, MongoDB, backend, dashboard)
docs/          deploy · hardware · go-live · accuracy · runbook
tests/         automated tests (backend, agent, repository hygiene)
```

| I want to… | Read |
|---|---|
| Run it on my Mac | [LOCAL_SETUP.md](LOCAL_SETUP.md) |
| Put it on a server | [docs/deploy.md](docs/deploy.md) |
| Choose and mount the cameras | [docs/hardware.md](docs/hardware.md) |
| Prove it works before stopping the manual log | [docs/go-live.md](docs/go-live.md) |
| Measure and improve plate-reading accuracy | [docs/accuracy.md](docs/accuracy.md) |
| Handle an alert, restore a backup, rotate secrets | [docs/runbook.md](docs/runbook.md) |
| Set up the showroom PC | [local_agent/README.md](local_agent/README.md) |

Run the checks before every change is deployed:

```bash
make check     # lint + all tests + dashboard build
```
