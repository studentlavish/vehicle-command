#!/usr/bin/env bash
# Starts MongoDB (if needed), the FastAPI backend on :8001 and the React app on :3000.
# Ctrl+C stops backend + frontend.  Add --agent to also start the camera agent.
# By default the app is reachable ONLY from this Mac (127.0.0.1). To let other
# devices on your Wi-Fi open it, run:  BIND_HOST=0.0.0.0 bash start.sh
set -o pipefail
BIND_HOST="${BIND_HOST:-127.0.0.1}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/logs"

# MongoDB
if ! nc -z localhost 27017 >/dev/null 2>&1; then
  echo "==> Starting MongoDB"
  if [ "${USE_DOCKER_MONGO:-0}" = "1" ]; then docker start vc-mongo >/dev/null
  else brew services start mongodb-community >/dev/null; fi
  for i in $(seq 1 20); do nc -z localhost 27017 && break; sleep 1; done
fi
nc -z localhost 27017 || { echo "MongoDB is not reachable on :27017"; exit 1; }

pids=()
cleanup() { echo; echo "==> Stopping..."; for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup INT TERM EXIT

[ "$BIND_HOST" = "127.0.0.1" ] && echo "==> Access: this Mac only (BIND_HOST=0.0.0.0 to share on Wi-Fi)" || echo "==> Access: ANY device on your network can reach the app"
echo "==> Backend  -> http://localhost:8001   (log: logs/backend.log)"
( cd "$ROOT/backend" && exec .venv/bin/uvicorn server:app --host "$BIND_HOST" --port 8001 --reload --reload-exclude ".venv/*" ) \
  > >(tee "$ROOT/logs/backend.log") 2>&1 &
pids+=($!)

for i in $(seq 1 30); do curl -s -o /dev/null http://localhost:8001/docs && break; sleep 1; done

if [ "${1:-}" = "--agent" ]; then
  echo "==> Camera agent (log: logs/agent.log)"
  ( cd "$ROOT/local_agent" && exec .venv/bin/python agent.py ) > "$ROOT/logs/agent.log" 2>&1 &
  pids+=($!)
fi

echo "==> Frontend -> http://localhost:3000   (log: logs/frontend.log)"
( cd "$ROOT/frontend" && HOST="$BIND_HOST" exec yarn start ) > >(tee "$ROOT/logs/frontend.log") 2>&1 &
pids+=($!)

( sleep 25; open http://localhost:3000 2>/dev/null ) &
wait
