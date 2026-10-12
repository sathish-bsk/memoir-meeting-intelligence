#!/usr/bin/env bash
#
# Memoir — Local Development Startup
#
# Uses local Postgres + Redis (Homebrew), runs migrations, and launches
# auth service (8001), core API (8005), and frontend (3000).
#
# Usage:
#   ./dev.sh          Start everything
#   ./dev.sh stop     Stop all services
#   ./dev.sh status   Show what's running
#   ./dev.sh logs     Tail all service logs
#   ./dev.sh logs auth|api|frontend|enhance  Tail a specific service log
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dev]${NC} $1"; }
warn() { echo -e "${YELLOW}[dev]${NC} $1"; }
err()  { echo -e "${RED}[dev]${NC} $1"; }

PG_USER="${PG_USER:-$(whoami)}"
PSQL="$(command -v psql || echo "/usr/local/opt/postgresql@16/bin/psql")"

# Kill all processes on a given port (graceful then force)
kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti:"$port" 2>/dev/null) || return 0
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 0.5
        # Force kill if still alive
        pids=$(lsof -ti:"$port" 2>/dev/null) || return 0
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
            sleep 0.3
        fi
    fi
}

# ─── Logs command ───────────────────────────────────────────────
if [[ "${1:-}" == "logs" ]]; then
    LOG_DIR="$ROOT_DIR/.dev-logs"
    if [ ! -d "$LOG_DIR" ]; then
        err "No logs found. Run ./dev.sh first."
        exit 1
    fi
    case "${2:-all}" in
        auth)         exec tail -f "$LOG_DIR/auth.log" ;;
        api)          exec tail -f "$LOG_DIR/core-api.log" ;;
        integration)  exec tail -f "$LOG_DIR/integration.log" ;;
        notification) exec tail -f "$LOG_DIR/notification.log" ;;
        orchestrator)    exec tail -f "$LOG_DIR/orchestrator.log" ;;
        transcription)   exec tail -f "$LOG_DIR/transcription.log" ;;
        worker)          exec tail -f "$LOG_DIR/transcription-worker.log" ;;
        enhance)         exec tail -f "$LOG_DIR/resemble-enhance.log" ;;
        frontend)        exec tail -f "$LOG_DIR/frontend.log" ;;
        all)             exec tail -f "$LOG_DIR/auth.log" "$LOG_DIR/core-api.log" "$LOG_DIR/integration.log" "$LOG_DIR/notification.log" "$LOG_DIR/orchestrator.log" "$LOG_DIR/transcription.log" "$LOG_DIR/transcription-worker.log" "$LOG_DIR/resemble-enhance.log" "$LOG_DIR/frontend.log" ;;
        *)
            err "Unknown service: $2"
            echo "Usage: ./dev.sh logs [auth|api|integration|notification|orchestrator|transcription|worker|enhance|frontend]"
            exit 1
            ;;
    esac
fi

# ─── Stop command ────────────────────────────────────────────────
if [[ "${1:-}" == "stop" ]]; then
    log "Stopping services..."
    kill_port 8001 && log "  Auth service stopped"
    kill_port 8002 && log "  Notification service stopped"
    kill_port 8005 && log "  Core API stopped"
    kill_port 8006 && log "  Integration service stopped"
    kill_port 4000 && log "  Orchestrator stopped"
    kill_port 8000 && log "  TranscriptionBase stopped"
    kill_port 8010 && log "  Resemble Enhance stopped"
    kill_port 3000 && log "  Frontend stopped"
    # Transcription worker has no port — kill by PID file
    if [ -f "$ROOT_DIR/.dev-logs/transcription-worker.pid" ]; then
        WORKER_PID=$(cat "$ROOT_DIR/.dev-logs/transcription-worker.pid")
        kill "$WORKER_PID" 2>/dev/null && log "  Transcription worker stopped" || true
        rm -f "$ROOT_DIR/.dev-logs/transcription-worker.pid"
    fi
    exit 0
fi

# ─── Status command ──────────────────────────────────────────────
if [[ "${1:-}" == "status" ]]; then
    echo ""
    echo "Infrastructure:"
    lsof -ti:5432 >/dev/null 2>&1 && echo "  Postgres       :5432  running" || echo "  Postgres       :5432  stopped"
    lsof -ti:6379 >/dev/null 2>&1 && echo "  Redis          :6379  running" || echo "  Redis          :6379  stopped"
    echo ""
    echo "Services:"
    lsof -ti:8001 >/dev/null 2>&1 && echo "  Auth service   :8001  running" || echo "  Auth service   :8001  stopped"
    lsof -ti:8002 >/dev/null 2>&1 && echo "  Notification   :8002  running" || echo "  Notification   :8002  stopped"
    lsof -ti:8005 >/dev/null 2>&1 && echo "  Core API       :8005  running" || echo "  Core API       :8005  stopped"
    lsof -ti:8006 >/dev/null 2>&1 && echo "  Integration    :8006  running" || echo "  Integration    :8006  stopped"
    lsof -ti:4000 >/dev/null 2>&1 && echo "  Orchestrator   :4000  running" || echo "  Orchestrator   :4000  stopped"
    lsof -ti:8000 >/dev/null 2>&1 && echo "  Transcription  :8000  running" || echo "  Transcription  :8000  stopped"
    lsof -ti:8010 >/dev/null 2>&1 && echo "  Resemble Enh.  :8010  running" || echo "  Resemble Enh.  :8010  stopped"
    if [ -f "$ROOT_DIR/.dev-logs/transcription-worker.pid" ] && kill -0 "$(cat "$ROOT_DIR/.dev-logs/transcription-worker.pid")" 2>/dev/null; then
        echo "  Txn Worker     (poll) running"
    else
        echo "  Txn Worker     (poll) stopped"
    fi
    lsof -ti:3000 >/dev/null 2>&1 && echo "  Frontend       :3000  running" || echo "  Frontend       :3000  stopped"
    echo ""
    exit 0
fi

echo ""
echo -e "${GREEN}memoir${NC} — starting local development environment"
echo ""

# ─── 1. Infrastructure ───────────────────────────────────────────
log "Checking infrastructure..."

# Postgres
if lsof -ti:5432 >/dev/null 2>&1; then
    echo -e "  ${DIM}Postgres running on :5432${NC}"
else
    brew services start postgresql@16 2>/dev/null || true
    sleep 2
    if lsof -ti:5432 >/dev/null 2>&1; then
        log "  Postgres started"
    else
        err "Postgres not running. Install with: brew install postgresql@16 && brew services start postgresql@16"
        exit 1
    fi
fi

# Redis
if lsof -ti:6379 >/dev/null 2>&1; then
    echo -e "  ${DIM}Redis running on :6379${NC}"
else
    brew services start redis 2>/dev/null || true
    sleep 1
    if lsof -ti:6379 >/dev/null 2>&1; then
        log "  Redis started"
    else
        err "Redis not running. Install with: brew install redis && brew services start redis"
        exit 1
    fi
fi

# Docker (required for bot orchestrator to spawn BotWorker containers)
if docker info >/dev/null 2>&1; then
    echo -e "  ${DIM}Docker running${NC}"
else
    log "  Docker not running — starting Docker Desktop..."
    open -a Docker 2>/dev/null || true
    # Wait up to 90 seconds for the daemon to be ready
    DOCKER_WAIT=0
    while ! docker info >/dev/null 2>&1; do
        if [ "$DOCKER_WAIT" -ge 90 ]; then
            break
        fi
        if [ $((DOCKER_WAIT % 10)) -eq 0 ] && [ "$DOCKER_WAIT" -gt 0 ]; then
            echo -e "  ${DIM}Waiting for Docker... (${DOCKER_WAIT}s)${NC}"
        fi
        sleep 2
        DOCKER_WAIT=$((DOCKER_WAIT + 2))
    done
    if docker info >/dev/null 2>&1; then
        log "  Docker Desktop started (took ~${DOCKER_WAIT}s)"
    else
        err "Docker failed to start after 90s — bot recording will not work."
        err "Start Docker Desktop manually and re-run ./dev.sh"
        exit 1
    fi
fi

if docker info >/dev/null 2>&1; then
    # Create recordings directory (image build handled by orchestrator on first job)
    mkdir -p "$ROOT_DIR/BotWorker/recordings"
fi

# ─── 2. Create role and databases ─────────────────────────────────
log "Ensuring databases exist..."

# Create videoclip role if it doesn't exist
ROLE_EXISTS=$("$PSQL" -U "$PG_USER" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='videoclip'" 2>/dev/null | tr -d '[:space:]')
if [ "$ROLE_EXISTS" != "1" ]; then
    "$PSQL" -U "$PG_USER" -d postgres -c "CREATE ROLE videoclip WITH LOGIN PASSWORD 'videoclipdev' CREATEDB;" >/dev/null 2>&1
    log "  Created role 'videoclip'"
else
    echo -e "  ${DIM}Role 'videoclip' exists${NC}"
fi

# Create databases
for db in auth_db videoclip_db; do
    DB_EXISTS=$("$PSQL" -U "$PG_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" 2>/dev/null | tr -d '[:space:]')
    if [ "$DB_EXISTS" != "1" ]; then
        "$PSQL" -U "$PG_USER" -d postgres -c "CREATE DATABASE $db OWNER videoclip;" >/dev/null 2>&1
        log "  Created $db"
    else
        echo -e "  ${DIM}$db exists${NC}"
    fi
done

# ─── 3. Auth service setup ──────────────────────────────────────
log "Setting up auth service..."

if [ ! -d "$ROOT_DIR/auth_service/venv" ]; then
    python3 -m venv "$ROOT_DIR/auth_service/venv"
    "$ROOT_DIR/auth_service/venv/bin/pip" install -q -r "$ROOT_DIR/auth_service/requirements.txt"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# Generate ES256 keypair if missing
if [ ! -f "$ROOT_DIR/auth_service/keys/private.pem" ]; then
    mkdir -p "$ROOT_DIR/auth_service/keys"
    "$ROOT_DIR/auth_service/venv/bin/python" -c "
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
key = ec.generate_private_key(ec.SECP256R1())
open('$ROOT_DIR/auth_service/keys/private.pem','wb').write(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
open('$ROOT_DIR/auth_service/keys/public.pem','wb').write(key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
"
    log "  Generated ES256 keypair"
else
    echo -e "  ${DIM}Keys exist${NC}"
fi

# Run auth migrations
cd "$ROOT_DIR/auth_service"
PYTHONPATH="$ROOT_DIR/auth_service" ENVIRONMENT=development \
    "$ROOT_DIR/auth_service/venv/bin/alembic" upgrade head 2>&1 | tail -1 | sed 's/^/  /'
cd "$ROOT_DIR"

# ─── 4. Core API setup ──────────────────────────────────────────
log "Setting up core API..."

if [ ! -d "$ROOT_DIR/core_api/venv" ]; then
    python3 -m venv "$ROOT_DIR/core_api/venv"
    "$ROOT_DIR/core_api/venv/bin/pip" install -q -r "$ROOT_DIR/core_api/requirements.txt"
    "$ROOT_DIR/core_api/venv/bin/pip" install -q -e "$ROOT_DIR/videoclip_common"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# Copy public key from auth service
mkdir -p "$ROOT_DIR/core_api/keys"
cp "$ROOT_DIR/auth_service/keys/public.pem" "$ROOT_DIR/core_api/keys/public.pem"
echo -e "  ${DIM}Public key synced${NC}"

# Create data directory
mkdir -p "$ROOT_DIR/core_api/data"

# Run core API migrations
cd "$ROOT_DIR/core_api"
PYTHONPATH="$ROOT_DIR/core_api" ENVIRONMENT=development \
    "$ROOT_DIR/core_api/venv/bin/alembic" upgrade head 2>&1 | tail -1 | sed 's/^/  /'
cd "$ROOT_DIR"

# ─── 5. Integration service setup ──────────────────────────────
log "Setting up integration service..."

if [ ! -d "$ROOT_DIR/integration_service/venv" ]; then
    python3 -m venv "$ROOT_DIR/integration_service/venv"
    "$ROOT_DIR/integration_service/venv/bin/pip" install -q -r "$ROOT_DIR/integration_service/requirements.txt"
    "$ROOT_DIR/integration_service/venv/bin/pip" install -q -e "$ROOT_DIR/videoclip_common"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# Copy public key from auth service
mkdir -p "$ROOT_DIR/integration_service/keys"
cp "$ROOT_DIR/auth_service/keys/public.pem" "$ROOT_DIR/integration_service/keys/public.pem"
echo -e "  ${DIM}Public key synced${NC}"

# ─── 6. Notification service setup ─────────────────────────────
log "Setting up notification service..."

if [ ! -d "$ROOT_DIR/notification_service/venv" ]; then
    python3 -m venv "$ROOT_DIR/notification_service/venv"
    "$ROOT_DIR/notification_service/venv/bin/pip" install -q -r "$ROOT_DIR/notification_service/requirements.txt"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# ─── 7. Orchestrator setup ──────────────────────────────────────
log "Setting up orchestrator..."

if [ ! -d "$ROOT_DIR/orchestrator/node_modules" ]; then
    cd "$ROOT_DIR/orchestrator" && npm install --silent 2>&1 | tail -1
    cd "$ROOT_DIR"
    log "  Installed npm dependencies"
else
    echo -e "  ${DIM}node_modules exists${NC}"
fi

# Create .env from example if missing
if [ ! -f "$ROOT_DIR/orchestrator/.env" ]; then
    cp "$ROOT_DIR/orchestrator/.env.example" "$ROOT_DIR/orchestrator/.env"
    log "  Created .env from example"
else
    echo -e "  ${DIM}.env exists${NC}"
fi

# ─── 8. TranscriptionBase setup ────────────────────────────────
log "Setting up TranscriptionBase..."

if [ ! -d "$ROOT_DIR/TranscriptionBase/venv" ]; then
    python3.11 -m venv "$ROOT_DIR/TranscriptionBase/venv"
    "$ROOT_DIR/TranscriptionBase/venv/bin/pip" install -q -r "$ROOT_DIR/TranscriptionBase/requirements.txt"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# ─── 9. Resemble Enhance setup ─────────────────────────────────
log "Setting up Resemble Enhance..."

if [ ! -d "$ROOT_DIR/resemble_enhance/venv" ]; then
    python3.11 -m venv "$ROOT_DIR/resemble_enhance/venv"
    CC=/usr/bin/clang CXX=/usr/bin/clang++ "$ROOT_DIR/resemble_enhance/venv/bin/pip" install -q -r "$ROOT_DIR/resemble_enhance/requirements.txt"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# ─── 10. Transcription worker setup ────────────────────────────
log "Setting up transcription worker..."

if [ ! -d "$ROOT_DIR/transcription_worker/venv" ]; then
    python3 -m venv "$ROOT_DIR/transcription_worker/venv"
    "$ROOT_DIR/transcription_worker/venv/bin/pip" install -q -r "$ROOT_DIR/transcription_worker/requirements.txt"
    log "  Installed Python dependencies"
else
    echo -e "  ${DIM}venv exists${NC}"
fi

# Create .env from example if missing
if [ ! -f "$ROOT_DIR/transcription_worker/.env" ]; then
    cp "$ROOT_DIR/transcription_worker/.env.example" "$ROOT_DIR/transcription_worker/.env"
    log "  Created .env from example"
else
    echo -e "  ${DIM}.env exists${NC}"
fi

# ─── 11. Frontend setup ─────────────────────────────────────────
log "Setting up frontend..."

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    cd "$ROOT_DIR/frontend" && npm install --silent 2>&1 | tail -1
    cd "$ROOT_DIR"
    log "  Installed npm dependencies"
else
    echo -e "  ${DIM}node_modules exists${NC}"
fi

# ─── 12. Start services ─────────────────────────────────────────
echo ""
log "Starting services..."

# Kill anything already on our ports
log "Clearing ports..."
kill_port 8001
kill_port 8002
kill_port 8005
kill_port 8006
kill_port 4000
kill_port 8000
kill_port 8010
kill_port 3000
# Kill transcription worker by PID file (no port)
if [ -f "$ROOT_DIR/.dev-logs/transcription-worker.pid" ]; then
    kill "$(cat "$ROOT_DIR/.dev-logs/transcription-worker.pid")" 2>/dev/null || true
    rm -f "$ROOT_DIR/.dev-logs/transcription-worker.pid"
fi

# Create log directory
mkdir -p "$ROOT_DIR/.dev-logs"

# Auth service
cd "$ROOT_DIR/auth_service"
ENVIRONMENT=development "$ROOT_DIR/auth_service/venv/bin/uvicorn" app.main:app \
    --port 8001 --reload \
    > "$ROOT_DIR/.dev-logs/auth.log" 2>&1 &
AUTH_PID=$!
log "  Auth service    → http://localhost:8001  (pid $AUTH_PID)"

# Notification service
cd "$ROOT_DIR/notification_service"
ENVIRONMENT=development "$ROOT_DIR/notification_service/venv/bin/uvicorn" app.main:app \
    --port 8002 --reload \
    > "$ROOT_DIR/.dev-logs/notification.log" 2>&1 &
NOTIF_PID=$!
log "  Notification    → http://localhost:8002  (pid $NOTIF_PID)"

# Core API
cd "$ROOT_DIR/core_api"
ENVIRONMENT=development "$ROOT_DIR/core_api/venv/bin/uvicorn" app.main:app \
    --port 8005 --reload \
    > "$ROOT_DIR/.dev-logs/core-api.log" 2>&1 &
CORE_PID=$!
log "  Core API        → http://localhost:8005  (pid $CORE_PID)"

# Integration service
cd "$ROOT_DIR/integration_service"
ENVIRONMENT=development "$ROOT_DIR/integration_service/venv/bin/uvicorn" app.main:app \
    --port 8006 --reload \
    > "$ROOT_DIR/.dev-logs/integration.log" 2>&1 &
INTEG_PID=$!
log "  Integration     → http://localhost:8006  (pid $INTEG_PID)"

# Orchestrator (bot job queue — requires Docker for spawning bot containers)
cd "$ROOT_DIR/orchestrator"
DATABASE_URL="postgresql://videoclip:videoclipdev@localhost:5432/videoclip_db" \
    npm run dev > "$ROOT_DIR/.dev-logs/orchestrator.log" 2>&1 &
ORCH_PID=$!
log "  Orchestrator    → http://localhost:4000  (pid $ORCH_PID)"

# TranscriptionBase (audio transcription pipeline + job processor API)
cd "$ROOT_DIR/TranscriptionBase"
DATABASE_URL="postgresql://videoclip:videoclipdev@localhost:5432/videoclip_db" \
BOT_RECORDINGS_DIR="$ROOT_DIR/BotWorker/recordings" \
DATA_DIR="$ROOT_DIR/core_api/data" \
    "$ROOT_DIR/TranscriptionBase/venv/bin/uvicorn" app.main:app \
    --port 8000 --reload \
    > "$ROOT_DIR/.dev-logs/transcription.log" 2>&1 &
TRANS_PID=$!
log "  TranscriptionBase → http://localhost:8000  (pid $TRANS_PID)"

# Resemble Enhance (audio denoiser microservice — isolated torch env)
cd "$ROOT_DIR/resemble_enhance"
"$ROOT_DIR/resemble_enhance/venv/bin/uvicorn" app:app \
    --port 8010 --reload \
    > "$ROOT_DIR/.dev-logs/resemble-enhance.log" 2>&1 &
ENHANCE_PID=$!
log "  Resemble Enhance → http://localhost:8010  (pid $ENHANCE_PID)"

# Transcription worker (poll-based — no HTTP port)
cd "$ROOT_DIR/transcription_worker"
"$ROOT_DIR/transcription_worker/venv/bin/python" -m app.main \
    > "$ROOT_DIR/.dev-logs/transcription-worker.log" 2>&1 &
WORKER_PID=$!
echo "$WORKER_PID" > "$ROOT_DIR/.dev-logs/transcription-worker.pid"
log "  Txn Worker      (poll-based)              (pid $WORKER_PID)"

# Frontend
cd "$ROOT_DIR/frontend"
npm run dev > "$ROOT_DIR/.dev-logs/frontend.log" 2>&1 &
FRONT_PID=$!
log "  Frontend        → http://localhost:3000  (pid $FRONT_PID)"

cd "$ROOT_DIR"

echo ""
echo -e "${GREEN}All services running.${NC}"
echo -e "${DIM}Stop: Ctrl+C${NC}"
echo ""
echo -e "Open ${GREEN}http://localhost:3000${NC} to get started."
echo ""

# Tail logs — Ctrl+C stops everything
TAIL_PID=""
cleanup() {
    echo ""
    log "Shutting down..."
    [ -n "$TAIL_PID" ] && kill "$TAIL_PID" 2>/dev/null || true
    kill $AUTH_PID $NOTIF_PID $CORE_PID $INTEG_PID $ORCH_PID $TRANS_PID $ENHANCE_PID $WORKER_PID $FRONT_PID 2>/dev/null || true
    wait $AUTH_PID $NOTIF_PID $CORE_PID $INTEG_PID $ORCH_PID $TRANS_PID $ENHANCE_PID $WORKER_PID $FRONT_PID 2>/dev/null || true
    rm -f "$ROOT_DIR/.dev-logs/transcription-worker.pid"
    log "Services stopped."
}
trap cleanup INT TERM

tail -f "$ROOT_DIR/.dev-logs/auth.log" "$ROOT_DIR/.dev-logs/notification.log" "$ROOT_DIR/.dev-logs/core-api.log" "$ROOT_DIR/.dev-logs/integration.log" "$ROOT_DIR/.dev-logs/orchestrator.log" "$ROOT_DIR/.dev-logs/transcription.log" "$ROOT_DIR/.dev-logs/resemble-enhance.log" "$ROOT_DIR/.dev-logs/transcription-worker.log" "$ROOT_DIR/.dev-logs/frontend.log" &
TAIL_PID=$!
wait
