#!/usr/bin/env bash
# One-time local setup for Vehicle Command on macOS.
# Installs MongoDB, Python 3.12, Node 20 + Yarn (via Homebrew), then the
# backend virtualenv, frontend node_modules and the local camera agent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
die() { printf "\n\033[1;31mERROR: %s\033[0m\n" "$*"; exit 1; }

# ---------- Homebrew ----------
if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"; fi
  if [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"; fi
fi
BREW_PREFIX="$(brew --prefix)"

# ---------- MongoDB ----------
if [ "${USE_DOCKER_MONGO:-0}" = "1" ]; then
  say "Starting MongoDB in Docker"
  command -v docker >/dev/null || die "Docker not installed"
  docker start vc-mongo 2>/dev/null || docker run -d --name vc-mongo -p 27017:27017 -v vc-mongo-data:/data/db mongo:7
else
  say "Installing MongoDB Community + Database Tools"
  brew tap mongodb/brew >/dev/null
  brew list mongodb-community >/dev/null 2>&1 || brew install mongodb-community
  brew list mongodb-database-tools >/dev/null 2>&1 || brew install mongodb-database-tools
  brew services start mongodb-community || true
fi

# ---------- Python ----------
PY=""
for c in python3.12 python3.13 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then PY="$(command -v $c)"; break; fi
done
if [ -z "$PY" ]; then
  say "Installing Python 3.12"
  brew install python@3.12
  PY="$BREW_PREFIX/bin/python3.12"
fi
say "Using Python: $PY ($($PY --version))"

# ---------- Node + Yarn ----------
if ! command -v node >/dev/null 2>&1; then
  say "Installing Node 20"
  brew install node@20
  brew link --overwrite --force node@20 || true
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 18 ] || die "Node $NODE_MAJOR found; need 18+ (brew install node@20)"
if ! command -v yarn >/dev/null 2>&1; then
  say "Installing Yarn 1.x"
  corepack enable 2>/dev/null || npm install -g yarn
fi

# ---------- .env files (generated once, with random secrets) ----------
say "Creating .env files"
if [ ! -f "$ROOT/backend/.env" ]; then
  JWT="$(openssl rand -hex 32)"; AGENT="$(openssl rand -hex 24)"; CRON="$(openssl rand -hex 24)"
  sed -e "s|^JWT_SECRET=.*|JWT_SECRET=\"$JWT\"|" \
      -e "s|^VASHU_AGENT_SECRET=.*|VASHU_AGENT_SECRET=\"$AGENT\"|" \
      -e "s|^WEBHOOK_CRON_SECRET=.*|WEBHOOK_CRON_SECRET=\"$CRON\"|" \
      "$ROOT/backend/.env.example" > "$ROOT/backend/.env"
  if [ ! -f "$ROOT/local_agent/.env" ]; then
    sed -e "s|^VASHU_AGENT_SECRET=.*|VASHU_AGENT_SECRET=$AGENT|" \
        "$ROOT/local_agent/.env.example" > "$ROOT/local_agent/.env"
  fi
  echo "   backend/.env + local_agent/.env created"
else
  echo "   backend/.env already exists — left unchanged"
fi
[ -f "$ROOT/local_agent/.env" ] || cp "$ROOT/local_agent/.env.example" "$ROOT/local_agent/.env"
[ -f "$ROOT/frontend/.env" ] || cp "$ROOT/frontend/.env.example" "$ROOT/frontend/.env"

# ---------- Backend ----------
say "Setting up backend virtualenv"
cd "$ROOT/backend"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip wheel >/dev/null
.venv/bin/pip install -r requirements-local.txt
# Optional: Emergent LLM SDK (only needed for AI plate-read / chat features)
if [ "${INSTALL_EMERGENT:-1}" = "1" ]; then
  .venv/bin/pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ \
    || echo "   (emergentintegrations not installed — AI features stay disabled, app still runs)"
fi
mkdir -p snapshots

# ---------- Frontend ----------
say "Installing frontend dependencies (this takes a few minutes)"
cd "$ROOT/frontend"
yarn install

# ---------- Local camera agent ----------
say "Setting up local camera agent"
cd "$ROOT/local_agent"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip wheel >/dev/null
.venv/bin/pip install -r requirements-base.txt
mkdir -p logs

chmod +x "$ROOT/start.sh" "$ROOT/stop.sh" "$ROOT/local_agent/start_agent.sh" 2>/dev/null || true

say "Setup complete!  Run:  ./start.sh"
echo "   Dashboard: http://localhost:3000   (admin@rdx.com / admin123)"
echo "   API docs:  http://localhost:8001/docs"
