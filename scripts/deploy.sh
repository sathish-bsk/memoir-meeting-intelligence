#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Memoir — Production Deploy Script
# Run on EC2: ./scripts/deploy.sh [--skip-frontend]
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/opt/memoir/app"
SECRETS_DIR="/etc/secrets/env"

# ─── Parse flags ───────────────────────────────────────────────
SKIP_FRONTEND=false
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=true ;;
  esac
done

cd "$APP_DIR"

# ─── 1. Pull latest code ──────────────────────────────────────
echo "==> Pulling latest code..."
git pull

# ─── 2. Preserve existing secrets ─────────────────────────────
echo "==> Saving existing secrets..."
declare -A SECRETS

# Read all key=value pairs we need to preserve from deployed env files
SECRET_KEYS="INTERNAL_SERVICE_KEY FERNET_KEY REDIS_URL DATABASE_URL ZOOM_ENCRYPTION_KEY CALENDAR_ENCRYPTION_KEY SLACK_ENCRYPTION_KEY SLACK_CLIENT_ID SLACK_CLIENT_SECRET SLACK_SIGNING_SECRET CRM_ENCRYPTION_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY S3_ENDPOINT_URL S3_BUCKET S3_REGION STORAGE_BACKEND BACKEND_CALLBACK_URL GROQ_API_KEY ANTHROPIC_API_KEY HF_AUTH_TOKEN TRANSCRIPTION_SERVICE_URL OAUTH_GOOGLE_REDIRECT_URI TEAMS_CLOUD_CLIENT_STATE_SECRET OUTLOOK_CLIENT_ID OUTLOOK_CLIENT_SECRET SENDGRID_API_KEY"

for envfile in "$SECRETS_DIR"/*.env; do
  svc=$(basename "$envfile" .env)
  while IFS= read -r line; do
    # Skip comments and empty lines
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ -z "$key" ]] && continue
    for sk in $SECRET_KEYS; do
      if [[ "$key" == "$sk" && "$value" != *"CHANGE_ME"* && "$value" != *"GENERATE"* && "$value" != "" ]]; then
        SECRETS["${svc}::${key}"]="$value"
      fi
    done
  done < "$envfile"
done

# ─── 3. Copy fresh env files from repo ────────────────────────
echo "==> Updating env files from repo..."

declare -A SVC_MAP=(
  [auth_service]="auth_service"
  [core_api]="core_api"
  [notification_service]="notification_service"
  [integration_service]="integration_service"
  [orchestrator]="orchestrator"
  [transcription_base]="TranscriptionBase"
  [transcription_worker]="transcription_worker"
)

for svc in "${!SVC_MAP[@]}"; do
  src_dir="${SVC_MAP[$svc]}"
  src_file="$APP_DIR/$src_dir/.env.production"
  dst_file="$SECRETS_DIR/${svc}.env"

  if [[ -f "$src_file" ]]; then
    sudo cp "$src_file" "$dst_file"
    sudo chown ubuntu:ubuntu "$dst_file"
    sudo chmod 600 "$dst_file"
    echo "   Copied $src_dir/.env.production -> $svc.env"
  else
    echo "   WARN: $src_file not found, skipping"
  fi
done

# ─── 4. Re-inject preserved secrets ───────────────────────────
echo "==> Re-injecting secrets..."

for compound_key in "${!SECRETS[@]}"; do
  svc="${compound_key%%::*}"
  key="${compound_key##*::}"
  value="${SECRETS[$compound_key]}"
  envfile="$SECRETS_DIR/${svc}.env"

  if [[ -f "$envfile" ]]; then
    # Replace placeholder or existing value for this key
    if grep -q "^${key}=" "$envfile"; then
      sudo sed -i "s|^${key}=.*|${key}=${value}|" "$envfile"
    else
      echo "${key}=${value}" | sudo tee -a "$envfile" > /dev/null
    fi
  fi
done

echo "   Secrets re-injected"

# ─── 4b. EC2-specific overrides (GPU worker gets priority) ────
# Slow EC2 worker polling so Vast.ai GPU worker (5s) claims jobs first
sudo sed -i "s|POLL_INTERVAL_SECONDS=5|POLL_INTERVAL_SECONDS=60|" "$SECRETS_DIR/transcription_worker.env"
# Public webhook URL so Vast.ai GPU worker can reach core_api
sudo sed -i "s|BOT_CALLBACK_URL=http://localhost:8005/api/internal/webhooks/bot-events|BOT_CALLBACK_URL=https://app.meetmemoir.com/api/internal/webhooks/bot-events|" "$SECRETS_DIR/core_api.env"

# ─── 5. Install/update Python dependencies ────────────────────
echo "==> Updating Python dependencies..."
# Only EC2 services — TranscriptionBase and resemble_enhance run on GPU (Vast.ai)
for svc in auth_service core_api notification_service integration_service transcription_worker; do
  if [[ -f "$APP_DIR/$svc/requirements.txt" && -d "$APP_DIR/$svc/venv" ]]; then
    # Skip videoclip-common line (relative path; installed separately below)
    grep -v 'videoclip-common' "$APP_DIR/$svc/requirements.txt" | "$APP_DIR/$svc/venv/bin/pip" install -q -r /dev/stdin 2>/dev/null || true
  fi
done

# Install videoclip_common in services that need it
for svc in core_api integration_service notification_service; do
  if [[ -d "$APP_DIR/$svc/venv" && -d "$APP_DIR/videoclip_common" ]]; then
    "$APP_DIR/$svc/venv/bin/pip" install -q -e "$APP_DIR/videoclip_common" 2>/dev/null || true
  fi
done

# ─── 5b. Install/update Node.js dependencies (orchestrator) ──
# Note: --include=dev (not --production) is REQUIRED — the build needs
# typescript/tsc which is a devDependency. Previously --production +
# silenced stderr + `|| true` meant tsc was never installed, npm run
# build silently no-op'd ("tsc: not found" hidden), and the dist/
# stayed at whatever was last committed. Symptoms: source edits to
# orchestrator never landed in production until someone manually ran
# npm install + build on the box. We surface stderr now so any future
# breakage is loud.
if [[ -f "$APP_DIR/orchestrator/package.json" ]]; then
  echo "   Updating orchestrator dependencies..."
  (cd "$APP_DIR/orchestrator" && npm install --include=dev -q && npm run build)
fi

# ─── 6. Run database migrations ───────────────────────────────
echo "==> Running migrations..."
# Export DATABASE_URL from env files (can't source directly — placeholders have angle brackets)
AUTH_DB_URL=$(grep '^DATABASE_URL=' "$SECRETS_DIR/auth_service.env" | cut -d= -f2-)
CORE_DB_URL=$(grep '^DATABASE_URL=' "$SECRETS_DIR/core_api.env" | cut -d= -f2-)

(cd "$APP_DIR/auth_service" && DATABASE_URL="$AUTH_DB_URL" PYTHONPATH=. ./venv/bin/alembic upgrade head 2>&1 | tail -1)
(cd "$APP_DIR/core_api" && DATABASE_URL="$CORE_DB_URL" PYTHONPATH=. ./venv/bin/alembic upgrade head 2>&1 | tail -1)
cd "$APP_DIR"

# ─── 7. Rebuild frontend (unless skipped) ─────────────────────
if [[ "$SKIP_FRONTEND" == false ]]; then
  echo "==> Rebuilding frontend..."
  cd "$APP_DIR/frontend" && npm run build 2>&1 | tail -3
  cd "$APP_DIR"
else
  echo "==> Skipping frontend build (--skip-frontend)"
fi

# ─── 8. Restart all services ──────────────────────────────────
echo "==> Restarting services..."
SERVICES=(
  memoir-auth
  memoir-core-api
  memoir-notification
  memoir-integration
  memoir-orchestrator
  # memoir-resemble-enhance  # Disabled — GPU only
  # memoir-transcription-base  # Disabled — GPU handles transcription
  # memoir-transcription-worker  # Disabled — GPU handles transcription
  memoir-frontend
)

for svc in "${SERVICES[@]}"; do
  sudo systemctl restart "$svc" 2>/dev/null || echo "   WARN: $svc not found"
done

sleep 3

# ─── 9. Health check ──────────────────────────────────────────
echo ""
echo "==> Service status:"
for svc in "${SERVICES[@]}"; do
  status=$(systemctl is-active "$svc" 2>/dev/null || echo "not found")
  if [[ "$status" == "active" ]]; then
    echo "   $svc: active"
  else
    echo "   $svc: $status  <-- CHECK LOGS: sudo journalctl -u $svc -n 20"
  fi
done

echo ""
echo "Deploy complete!"
