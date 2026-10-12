#!/bin/bash
# ============================================================================
# Memoir Production Setup Script
# ============================================================================
# Run this ONCE after cloning the repo on a fresh Ubuntu 22.04 EC2 instance.
#
# Prerequisites:
#   1. EC2 t3.small+ (Ubuntu 22.04) running in Mumbai (ap-south-1)
#   2. SSH access as ubuntu user (EC2 default)
#   3. Elastic IP attached
#   4. RDS PostgreSQL instance created (see deployment-questions-answers.md)
#
# Usage:
#   # First, clone the repo:
#   git clone <YOUR_REPO_URL> /opt/memoir/app
#
#   # Then run this script as root:
#   sudo bash /opt/memoir/app/scripts/setup-production.sh
#
# What this script does:
#   1. Verifies ubuntu user exists
#   2. Installs Python 3.11, Node.js 20, Docker, ffmpeg
#   3. Configures UFW firewall, fail2ban, swap
#   4. Installs and configures Redis
#   5. Generates JWT keypair
#   6. Creates Python venvs and installs dependencies
#   7. Builds Node.js services (orchestrator + frontend)
#   8. Builds BotWorker Docker image
#   9. Creates all systemd service units
#   10. Sets up Traefik (reverse proxy + SSL)
#   11. Starts all services
#
# What you still need to do AFTER this script:
#   - Fill in /etc/secrets/env/*.env files with real credentials
#   - Run database migrations
#   - Set DNS A record for your domain
#   - Add Vast.ai GPU IP to security group + UFW
#   - Update TRANSCRIPTION_SERVICE_URL in env files
#   - Restart services after filling env files
# ============================================================================

set -euo pipefail

# ─── Colors for output ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARNING:${NC} $1"; }
err() { echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $1"; }
section() { echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"; }

# ─── Pre-flight checks ─────────────────────────────────────────────────────

if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root (use sudo)"
    exit 1
fi

if [ ! -f /etc/lsb-release ] || ! grep -q "22.04" /etc/lsb-release; then
    warn "This script is designed for Ubuntu 22.04. Proceed with caution."
fi

APP_DIR="/opt/memoir/app"
if [ ! -d "$APP_DIR" ]; then
    err "Repository not found at $APP_DIR"
    echo "  Clone it first: git clone <YOUR_REPO_URL> /opt/memoir/app"
    exit 1
fi

# ─── Configuration ──────────────────────────────────────────────────────────

DEPLOY_USER="ubuntu"
DOMAIN="app.meetmemoir.com"  # Change if using a different domain

# Redis password — reuse existing if already configured (idempotent re-run)
if [ -f /etc/secrets/env/auth_service.env ] && grep -q "REDIS_URL=redis://:" /etc/secrets/env/auth_service.env 2>/dev/null; then
    REDIS_PASSWORD=$(grep "^REDIS_URL=" /etc/secrets/env/auth_service.env | sed 's|.*://:\(.*\)@.*|\1|')
    log "Reusing existing Redis password from env files"
else
    REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))" 2>/dev/null || openssl rand -base64 24)
fi

# Internal service key — reuse existing if already set (idempotent re-run)
if [ -f /etc/secrets/env/auth_service.env ] && grep -q "^INTERNAL_SERVICE_KEY=" /etc/secrets/env/auth_service.env 2>/dev/null; then
    EXISTING_KEY=$(grep "^INTERNAL_SERVICE_KEY=" /etc/secrets/env/auth_service.env | cut -d= -f2)
    if [[ "$EXISTING_KEY" != *"CHANGE_ME"* && -n "$EXISTING_KEY" ]]; then
        INTERNAL_SERVICE_KEY="$EXISTING_KEY"
        log "Reusing existing INTERNAL_SERVICE_KEY from env files"
    else
        INTERNAL_SERVICE_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    fi
else
    INTERNAL_SERVICE_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
fi

section "Step 1/13: Verify Deploy User"

log "Using existing '$DEPLOY_USER' user (EC2 default)"

# Ensure ubuntu has docker group (added in Step 3)
usermod -aG sudo $DEPLOY_USER 2>/dev/null || true

section "Step 2/13: Install System Packages"

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

# Disable needrestart interactive prompts
if [ -f /etc/needrestart/needrestart.conf ]; then
    sed -i "s/#\$nrconf{restart} = 'i';/\$nrconf{restart} = 'a';/" /etc/needrestart/needrestart.conf
fi

apt update && apt upgrade -y

# Python 3.11
if ! python3.11 --version &>/dev/null; then
    add-apt-repository ppa:deadsnakes/ppa -y
    apt update
    apt install -y python3.11 python3.11-venv python3.11-dev
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
    # Fix apt_pkg module for Python 3.11 (needed by add-apt-repository and Docker install)
    APT_PKG_SO=$(find /usr/lib/python3/dist-packages -name "apt_pkg*.so" 2>/dev/null | head -1)
    if [ -n "$APT_PKG_SO" ] && [ ! -f /usr/lib/python3/dist-packages/apt_pkg.so ]; then
        ln -sf "$APT_PKG_SO" /usr/lib/python3/dist-packages/apt_pkg.so
        log "Fixed apt_pkg symlink for Python 3.11"
    fi
    log "Python 3.11 installed"
else
    log "Python 3.11 already installed"
fi

# Node.js 20
if ! node --version 2>/dev/null | grep -q "v20"; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt install -y nodejs
    log "Node.js 20 installed"
else
    log "Node.js 20 already installed"
fi

# Essential packages
apt install -y \
    build-essential git curl wget unzip \
    ffmpeg libpq-dev libffi-dev libssl-dev \
    jq htop tmux \
    python3-pip

log "System packages installed"
log "  Python: $(python3 --version)"
log "  Node:   $(node --version)"
log "  npm:    $(npm --version)"
log "  ffmpeg: $(ffmpeg -version 2>&1 | head -1)"

section "Step 3/13: Install Docker"

if ! docker --version &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    log "Docker installed"
else
    log "Docker already installed"
fi

usermod -aG docker $DEPLOY_USER
systemctl enable docker
systemctl start docker

# Docker log rotation
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" }
}
EOF
systemctl restart docker

# Bot network
docker network create bot-network 2>/dev/null || log "bot-network already exists"

log "Docker configured"

section "Step 4/13: Firewall + Security"

# UFW
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP (Traefik redirect)"
ufw allow 443/tcp comment "HTTPS (Traefik)"
ufw allow from 172.16.0.0/12 to any port 4000 comment "Orchestrator from Docker containers"
ufw allow from 172.16.0.0/12 to any port 8005 comment "Core API from Docker containers"
ufw allow 6379/tcp comment "Redis (GPU box progress tracking, password-protected)"
ufw --force enable
log "UFW firewall configured (22, 80, 443, 6379, Docker→4000/8005 open)"

# fail2ban
apt install -y fail2ban
cat > /etc/fail2ban/jail.local << 'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
bantime = 3600
findtime = 600
EOF
systemctl enable fail2ban
systemctl restart fail2ban
log "fail2ban configured"

# SSH hardening
cat > /etc/ssh/sshd_config.d/hardened.conf << EOF
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AuthenticationMethods publickey
MaxAuthTries 3
MaxSessions 5
LoginGraceTime 30
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
AllowUsers $DEPLOY_USER
EOF

if sshd -t 2>/dev/null; then
    systemctl restart sshd
    log "SSH hardened (key-only, no root login)"
else
    err "SSH config test failed — skipping restart (fix manually)"
    rm -f /etc/ssh/sshd_config.d/hardened.conf
fi

# Auto security updates
apt install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
log "Auto security updates enabled"

section "Step 5/13: Swap File"

if swapon --show | grep -q "/swapfile"; then
    log "Swap already exists, skipping"
else
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
    sysctl -p > /dev/null
    log "4 GB swap file created (swappiness=10)"
fi

section "Step 6/13: Install & Configure Redis"

if systemctl is-active --quiet redis-server; then
    log "Redis already running, skipping configuration"
else
    apt install -y redis-server

    cat > /etc/redis/redis.conf << REDISEOF
bind 0.0.0.0
port 6379
requirepass ${REDIS_PASSWORD}
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
maxmemory 512mb
maxmemory-policy allkeys-lru
protected-mode yes
loglevel notice
logfile /var/log/redis/redis-server.log
daemonize no
pidfile /var/run/redis/redis-server.pid
dir /var/lib/redis
REDISEOF

    chown redis:redis /etc/redis/redis.conf
    systemctl enable redis-server
    systemctl restart redis-server
fi

if redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q "PONG"; then
    log "Redis configured with password"
else
    warn "Redis ping failed with generated password — Redis may have been configured with a different password"
fi

section "Step 7/13: Create Directories + Generate Secrets"

# Data directories
mkdir -p /data/recordings /data/core_api /data/backups
chown -R $DEPLOY_USER:$DEPLOY_USER /data

# Secrets
mkdir -p /etc/secrets/jwt /etc/secrets/env
chown -R $DEPLOY_USER:$DEPLOY_USER /etc/secrets
chmod 700 /etc/secrets/jwt /etc/secrets/env

# Logs
mkdir -p /var/log/memoir
chown -R $DEPLOY_USER:$DEPLOY_USER /var/log/memoir

# Journald retention
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/memoir.conf << 'EOF'
[Journal]
SystemMaxUse=2G
MaxRetentionSec=30d
Compress=yes
EOF
systemctl restart systemd-journald

# JWT keypair
if [ ! -f /etc/secrets/jwt/private.pem ]; then
    openssl ecparam -genkey -name prime256v1 -noout -out /etc/secrets/jwt/private.pem
    openssl ec -in /etc/secrets/jwt/private.pem -pubout -out /etc/secrets/jwt/public.pem 2>/dev/null
    chmod 600 /etc/secrets/jwt/private.pem
    chmod 644 /etc/secrets/jwt/public.pem
    chown $DEPLOY_USER:$DEPLOY_USER /etc/secrets/jwt/*
    log "JWT ES256 keypair generated"
else
    log "JWT keypair already exists"
fi

# Copy .env.production templates
log "Copying .env.production templates to /etc/secrets/env/"

declare -A ENV_MAP=(
    [auth_service]="auth_service"
    [core_api]="core_api"
    [integration_service]="integration_service"
    [notification_service]="notification_service"
    [orchestrator]="orchestrator"
    [transcription_base]="TranscriptionBase"
    [transcription_worker]="transcription_worker"
)

for svc in "${!ENV_MAP[@]}"; do
    src_dir="${ENV_MAP[$svc]}"
    src="$APP_DIR/$src_dir/.env.production"
    dst="/etc/secrets/env/${svc}.env"
    if [ -f "$src" ] && [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        log "  Copied $src_dir -> $svc"
    elif [ -f "$dst" ]; then
        log "  $svc already exists, skipping"
    else
        warn "  $src_dir/.env.production not found at $src"
    fi
done

# Inject auto-generated credentials into env files
log "Injecting generated credentials into env files..."

# Redis password
for envfile in auth_service core_api integration_service orchestrator; do
    sed -i "s|redis://localhost:6379|redis://:${REDIS_PASSWORD}@localhost:6379|g" /etc/secrets/env/${envfile}.env 2>/dev/null || true
done

# Internal service key
sed -i "s|<CHANGE_ME_STRONG_RANDOM_KEY>|${INTERNAL_SERVICE_KEY}|g" /etc/secrets/env/*.env

# EC2 worker polls slowly (60s) so Vast.ai GPU worker (5s) gets priority
sed -i "s|POLL_INTERVAL_SECONDS=5|POLL_INTERVAL_SECONDS=60|" /etc/secrets/env/transcription_worker.env

# Public webhook URL so Vast.ai GPU worker can reach core_api (not localhost)
sed -i "s|BOT_CALLBACK_URL=http://localhost:8005/api/internal/webhooks/bot-events|BOT_CALLBACK_URL=https://app.meetmemoir.com/api/internal/webhooks/bot-events|" /etc/secrets/env/core_api.env

chmod 600 /etc/secrets/env/*.env
chown $DEPLOY_USER:$DEPLOY_USER /etc/secrets/env/*.env

log "Directories and secrets created"

section "Step 8/13: Python Virtual Environments"

cd "$APP_DIR"

# Only EC2 services — TranscriptionBase and resemble_enhance run on GPU (Vast.ai), not here
for svc in auth_service core_api integration_service notification_service transcription_worker; do
    if [ ! -d "$svc/venv" ]; then
        log "Setting up $svc venv..."
        python3 -m venv "$svc/venv"
        "$svc/venv/bin/pip" install --upgrade pip -q

        # Skip videoclip-common from requirements.txt (relative path breaks on EC2;
        # it's installed separately below via pip install -e)
        grep -v 'videoclip-common' "$svc/requirements.txt" | "$svc/venv/bin/pip" install -q -r /dev/stdin
        log "  $svc: done"
    else
        log "  $svc: venv already exists, skipping"
    fi
done

# Services that need the shared videoclip_common package
if [ -d "videoclip_common" ]; then
    for svc in core_api integration_service; do
        "$svc/venv/bin/pip" install -e videoclip_common -q 2>/dev/null || true
    done
    log "  Installed videoclip_common into core_api + integration_service"
fi

chown -R $DEPLOY_USER:$DEPLOY_USER "$APP_DIR"

# Inject Fernet keys now that venvs exist
FERNET_GEN="$APP_DIR/auth_service/venv/bin/python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""

FERNET_KEY=$(eval $FERNET_GEN 2>/dev/null || echo "")
if [ -n "$FERNET_KEY" ]; then
    sed -i "s|<GENERATE_FERNET_KEY>|${FERNET_KEY}|" /etc/secrets/env/auth_service.env
    log "Auth FERNET_KEY generated"
else
    warn "Could not generate Fernet key — fill FERNET_KEY manually in auth_service.env"
fi

# Generate Fernet keys for all encryption fields
CALENDAR_FERNET=$(eval $FERNET_GEN 2>/dev/null || echo "")
ZOOM_FERNET=$(eval $FERNET_GEN 2>/dev/null || echo "")
CRM_FERNET=$(eval $FERNET_GEN 2>/dev/null || echo "")
SLACK_FERNET=$(eval $FERNET_GEN 2>/dev/null || echo "")
TEAMS_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)

# integration_service
sed -i "s|<GENERATE_FERNET_KEY_1>|${CALENDAR_FERNET}|" /etc/secrets/env/integration_service.env
sed -i "s|<GENERATE_FERNET_KEY_2>|${ZOOM_FERNET}|" /etc/secrets/env/integration_service.env
sed -i "s|<GENERATE_FERNET_KEY_4>|${CRM_FERNET}|" /etc/secrets/env/integration_service.env
log "  CALENDAR_ENCRYPTION_KEY, ZOOM_ENCRYPTION_KEY, CRM_ENCRYPTION_KEY generated (integration_service)"

# core_api — ZOOM_ENCRYPTION_KEY (share same key as integration_service for consistency)
sed -i "s|<GENERATE_FERNET_KEY>|${ZOOM_FERNET}|" /etc/secrets/env/core_api.env
log "  ZOOM_ENCRYPTION_KEY generated (core_api)"

# TEAMS_CLOUD_CLIENT_STATE_SECRET — same across services
sed -i "s|<GENERATE_RANDOM_SECRET>|${TEAMS_SECRET}|g" /etc/secrets/env/core_api.env
sed -i "s|<GENERATE_RANDOM_SECRET>|${TEAMS_SECRET}|g" /etc/secrets/env/integration_service.env
log "  TEAMS_CLOUD_CLIENT_STATE_SECRET generated"

# SLACK_ENCRYPTION_KEY — already in .env.production template, but add if placeholder was used
if grep -q "SLACK_ENCRYPTION_KEY=.*GENERATE" /etc/secrets/env/integration_service.env 2>/dev/null; then
    sed -i "s|SLACK_ENCRYPTION_KEY=.*GENERATE.*|SLACK_ENCRYPTION_KEY=${SLACK_FERNET}|" /etc/secrets/env/integration_service.env
    log "  SLACK_ENCRYPTION_KEY generated"
fi

log "All encryption keys generated and injected"

section "Step 9/13: Build Node.js Services + BotWorker"

cd "$APP_DIR"

# Orchestrator
log "Building orchestrator..."
cd orchestrator
sudo -u $DEPLOY_USER npm ci --silent 2>/dev/null
sudo -u $DEPLOY_USER npm run build 2>/dev/null
sudo -u $DEPLOY_USER npm prune --production --silent 2>/dev/null
cd "$APP_DIR"
log "  Orchestrator built"

# Frontend
log "Building frontend..."
cd frontend
sudo -u $DEPLOY_USER npm ci --silent 2>/dev/null
sudo -u $DEPLOY_USER bash -c "AUTH_SERVICE_URL=http://localhost:8001 CORE_API_URL=http://localhost:8005 INTEGRATION_SERVICE_URL=http://localhost:8006 npm run build" 2>/dev/null
cd "$APP_DIR"
log "  Frontend built"

# BotWorker Docker image
if [ -d "BotWorker" ]; then
    log "Building BotWorker Docker image..."
    cd BotWorker
    docker build -t botworker:latest . -q
    cd "$APP_DIR"
    log "  BotWorker image built"
else
    warn "BotWorker directory not found — skipping Docker build"
fi

section "Step 10/13: Traefik (Reverse Proxy + SSL)"

mkdir -p /opt/traefik/certs /opt/traefik/dynamic

# SSL certificate storage (Let's Encrypt ACME)
touch /opt/traefik/certs/certs.json
chmod 600 /opt/traefik/certs/certs.json

# Static config
cat > /opt/traefik/traefik.yml << EOF
api:
  dashboard: false

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
          permanent: true
  websecure:
    address: ":443"
    http:
      tls:
        certResolver: letsencrypt
      middlewares:
        - security-headers@file

certificatesResolvers:
  letsencrypt:
    acme:
      email: "hello@meetmemoir.com"
      storage: /certs/certs.json
      tlsChallenge: {}

providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true

log:
  level: INFO
  format: json

accessLog:
  format: json
  filters:
    statusCodes:
      - "400-599"
EOF

# Copy existing middleware + TLS configs
if [ -f "$APP_DIR/api_gateway_service/traefik/dynamic/middlewares.yml" ]; then
    cp "$APP_DIR/api_gateway_service/traefik/dynamic/middlewares.yml" /opt/traefik/dynamic/
    # Fix ForwardAuth address from Docker DNS to localhost
    sed -i 's|http://auth-service:8000/auth/verify|http://127.0.0.1:8001/auth/verify|g' /opt/traefik/dynamic/middlewares.yml
    sed -i "s|\${CORS_ORIGIN:-http://localhost:3000}|https://${DOMAIN}|g" /opt/traefik/dynamic/middlewares.yml
    # Remove request-id middleware (uses Go templates, not supported in file provider)
    sed -i '/request-id:/,/X-Request-ID.*RequestID/d' /opt/traefik/dynamic/middlewares.yml
    log "Copied middlewares.yml (ForwardAuth → 127.0.0.1:8001, CORS → https://${DOMAIN})"
fi

if [ -f "$APP_DIR/api_gateway_service/traefik/dynamic/tls-options.yml" ]; then
    cp "$APP_DIR/api_gateway_service/traefik/dynamic/tls-options.yml" /opt/traefik/dynamic/
    log "Copied tls-options.yml"
fi

# File-based routers
cat > /opt/traefik/dynamic/routers.yml << REOF
http:
  services:
    auth-svc:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8001"
    core-api-svc:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8005"
    integration-svc:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8006"
    frontend-svc:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:3000"

  routers:
    auth-verify:
      rule: "Host(\`${DOMAIN}\`) && Path(\`/auth/verify\`)"
      entryPoints: [websecure]
      middlewares: [security-headers]
      service: auth-svc
      priority: 30
      tls:
        certResolver: letsencrypt

    calendar-webhooks:
      rule: "Host(\`${DOMAIN}\`) && (PathPrefix(\`/api/webhooks/google-calendar\`) || PathPrefix(\`/api/webhooks/microsoft-calendar\`) || PathPrefix(\`/api/webhooks/teams-meeting-events\`) || PathPrefix(\`/api/webhooks/google-meet-events\`))"
      entryPoints: [websecure]
      middlewares: [chain-webhooks]
      service: core-api-svc
      priority: 26
      tls:
        certResolver: letsencrypt

    core-api-internal:
      rule: "Host(\`${DOMAIN}\`) && PathPrefix(\`/api/internal\`)"
      entryPoints: [websecure]
      middlewares: [chain-webhooks]
      service: core-api-svc
      priority: 25
      tls:
        certResolver: letsencrypt

    core-api-shared:
      rule: "Host(\`${DOMAIN}\`) && PathPrefix(\`/api/shared\`)"
      entryPoints: [websecure]
      middlewares: [chain-webhooks]
      service: core-api-svc
      priority: 25
      tls:
        certResolver: letsencrypt

    auth-public:
      rule: "Host(\`${DOMAIN}\`) && (PathPrefix(\`/auth/login\`) || PathPrefix(\`/auth/signup\`) || PathPrefix(\`/auth/refresh\`) || PathPrefix(\`/auth/forgot-password\`) || PathPrefix(\`/auth/reset-password\`) || PathPrefix(\`/auth/verify-email\`) || PathPrefix(\`/auth/oauth\`))"
      entryPoints: [websecure]
      middlewares: [chain-auth-public]
      service: auth-svc
      priority: 20
      tls:
        certResolver: letsencrypt

    core-api:
      rule: "Host(\`${DOMAIN}\`) && PathPrefix(\`/api\`)"
      entryPoints: [websecure]
      middlewares: [chain-api]
      service: core-api-svc
      priority: 15
      tls:
        certResolver: letsencrypt

    integration:
      rule: "Host(\`${DOMAIN}\`) && PathPrefix(\`/integrations/\`)"
      entryPoints: [websecure]
      middlewares: [chain-api]
      service: integration-svc
      priority: 15
      tls:
        certResolver: letsencrypt

    auth-protected:
      rule: "Host(\`${DOMAIN}\`) && PathPrefix(\`/auth\`)"
      entryPoints: [websecure]
      middlewares: [chain-auth-protected]
      service: auth-svc
      priority: 10
      tls:
        certResolver: letsencrypt

    frontend:
      rule: "Host(\`${DOMAIN}\`)"
      entryPoints: [websecure]
      middlewares: [chain-frontend]
      service: frontend-svc
      priority: 1
      tls:
        certResolver: letsencrypt
REOF

# Start Traefik
docker stop traefik 2>/dev/null || true
docker rm traefik 2>/dev/null || true

docker run -d \
  --name traefik \
  --restart unless-stopped \
  --network host \
  -v /opt/traefik/traefik.yml:/etc/traefik/traefik.yml:ro \
  -v /opt/traefik/dynamic/:/etc/traefik/dynamic/:ro \
  -v /opt/traefik/certs/:/certs/ \
  traefik:v3.2

log "Traefik started (--network host mode)"

section "Step 11/13: Create systemd Service Units"

# ── auth_service ──
cat > /etc/systemd/system/memoir-auth.service << 'UNIT'
[Unit]
Description=Memoir Auth Service
After=network.target redis-server.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/auth_service
EnvironmentFile=/etc/secrets/env/auth_service.env
ExecStart=/opt/memoir/app/auth_service/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-auth
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/auth_service
ReadOnlyPaths=/etc/secrets/jwt

[Install]
WantedBy=multi-user.target
UNIT

# ── core_api ──
cat > /etc/systemd/system/memoir-core-api.service << 'UNIT'
[Unit]
Description=Memoir Core API
After=network.target redis-server.service memoir-auth.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/core_api
EnvironmentFile=/etc/secrets/env/core_api.env
ExecStart=/opt/memoir/app/core_api/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8005 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-core-api
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/core_api /data/core_api /data/recordings
ReadOnlyPaths=/etc/secrets/jwt

[Install]
WantedBy=multi-user.target
UNIT

# ── integration_service ──
cat > /etc/systemd/system/memoir-integration.service << 'UNIT'
[Unit]
Description=Memoir Integration Service
After=network.target redis-server.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/integration_service
EnvironmentFile=/etc/secrets/env/integration_service.env
ExecStart=/opt/memoir/app/integration_service/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8006 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-integration
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/integration_service
ReadOnlyPaths=/etc/secrets/jwt

[Install]
WantedBy=multi-user.target
UNIT

# ── notification_service ──
cat > /etc/systemd/system/memoir-notification.service << 'UNIT'
[Unit]
Description=Memoir Notification Service
After=network.target

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/notification_service
EnvironmentFile=/etc/secrets/env/notification_service.env
ExecStart=/opt/memoir/app/notification_service/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8002 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-notification
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/notification_service

[Install]
WantedBy=multi-user.target
UNIT

# ── orchestrator ──
# To activate the pre-warmed bot pool (plan/bot_prewarmed_pool.md), flip
# BOT_POOL_ENABLED=true in /etc/secrets/env/orchestrator.env (or via a
# drop-in at /etc/systemd/system/memoir-orchestrator.service.d/pool.conf)
# and restart. Default is OFF — deploying the pool code without flipping
# the flag keeps today's cold-start behavior.
cat > /etc/systemd/system/memoir-orchestrator.service << 'UNIT'
[Unit]
Description=Memoir Bot Orchestrator
After=network.target redis-server.service docker.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/orchestrator
EnvironmentFile=/etc/secrets/env/orchestrator.env
ExecStart=/usr/bin/node dist/index.js
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-orchestrator
SupplementaryGroups=docker
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

# ── frontend ──
cat > /etc/systemd/system/memoir-frontend.service << 'UNIT'
[Unit]
Description=Memoir Frontend (Next.js)
After=network.target

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/frontend
Environment=NODE_ENV=production
Environment=PORT=3000
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-frontend
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/frontend

[Install]
WantedBy=multi-user.target
UNIT

# ── resemble_enhance (neural speech enhancement microservice) ──
cat > /etc/systemd/system/memoir-resemble-enhance.service << 'UNIT'
[Unit]
Description=Memoir Resemble Enhance (Speech Enhancement)
After=network.target

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/resemble_enhance
ExecStart=/opt/memoir/app/resemble_enhance/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8010 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-enhance
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/resemble_enhance /tmp

[Install]
WantedBy=multi-user.target
UNIT

# ── transcription_base (FastAPI — Whisper + pyannote processing) ──
cat > /etc/systemd/system/memoir-transcription-base.service << 'UNIT'
[Unit]
Description=Memoir TranscriptionBase (Whisper + Pyannote)
After=network.target

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/TranscriptionBase
EnvironmentFile=/etc/secrets/env/transcription_base.env
ExecStart=/opt/memoir/app/TranscriptionBase/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-txn-base
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/TranscriptionBase /data/core_api /data/recordings /tmp

[Install]
WantedBy=multi-user.target
UNIT

# ── transcription_worker (job poller/dispatcher) ──
cat > /etc/systemd/system/memoir-transcription-worker.service << 'UNIT'
[Unit]
Description=Memoir Transcription Worker
After=network.target memoir-transcription-base.service

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/app/transcription_worker
EnvironmentFile=/etc/secrets/env/transcription_worker.env
ExecStart=/opt/memoir/app/transcription_worker/venv/bin/python -m app.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-txn-worker
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/memoir/app/transcription_worker

[Install]
WantedBy=multi-user.target
UNIT

# ── pg_activity_monitor (RDS slow-query snapshotter) ──
# Polls pg_stat_activity, logs any non-idle query running > 1s along with
# wait_event_type (Lock / IO / LWLock / Client / none). Use this to diagnose
# the source of [DB_QUERY] / [DB_POOL] slow-handler events in core_api logs.
# Tail with: sudo journalctl -u memoir-pg-monitor -f
mkdir -p /opt/memoir/scripts
cp /opt/memoir/app/scripts/pg_activity_monitor.py /opt/memoir/scripts/pg_activity_monitor.py
chmod +x /opt/memoir/scripts/pg_activity_monitor.py

cat > /etc/systemd/system/memoir-pg-monitor.service << 'UNIT'
[Unit]
Description=Memoir Postgres pg_stat_activity snapshotter (slow-query diagnosis)
After=network.target

[Service]
Type=exec
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/memoir/scripts
EnvironmentFile=/etc/secrets/env/core_api.env
Environment=PG_ACTIVITY_THRESHOLD_MS=1000
Environment=PG_ACTIVITY_POLL_SEC=0.5
ExecStart=/opt/memoir/app/core_api/venv/bin/python /opt/memoir/scripts/pg_activity_monitor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=memoir-pg-monitor
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

# ── master target ──
cat > /etc/systemd/system/memoir.target << 'UNIT'
[Unit]
Description=Memoir Application Stack
Wants=memoir-auth.service memoir-core-api.service memoir-integration.service memoir-notification.service memoir-orchestrator.service memoir-frontend.service memoir-pg-monitor.service

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

# Enable core services (transcription runs on GPU, not EC2)
systemctl enable memoir-auth memoir-core-api memoir-integration memoir-notification memoir-orchestrator memoir-frontend memoir-pg-monitor memoir.target

log "7 core systemd units created and enabled (transcription/resemble disabled — GPU only)"

# ── RDS pg_stat_statements bootstrap (one-time, manual) ──
# pg_stat_statements gives us mean_exec_time / call count per normalized
# query, which is the canonical answer to "what queries are actually slow
# in production". On AWS RDS this requires TWO manual steps that this
# script cannot perform automatically (they need AWS console / aws CLI
# with RDS write permissions):
#
#   1. Modify the DB parameter group:
#        shared_preload_libraries = pg_stat_statements
#      then REBOOT the RDS instance for the param to take effect.
#
#   2. In each application database, run as the superuser:
#        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
#
# After both steps, query the top offenders with:
#   SELECT round(mean_exec_time::numeric, 1) AS avg_ms,
#          calls,
#          round(total_exec_time::numeric, 0) AS total_ms,
#          left(query, 100) AS q
#   FROM pg_stat_statements
#   WHERE userid = (SELECT oid FROM pg_roles WHERE rolname='memoir_admin')
#   ORDER BY mean_exec_time DESC LIMIT 20;
#
# Skipping in automated setup because the reboot causes 30-60s of DB
# downtime — operator should schedule it deliberately.
log "[manual step] pg_stat_statements: add to RDS parameter group + reboot, then CREATE EXTENSION (see comments above)"

section "Step 12/13: Create Helper Scripts"

mkdir -p /opt/memoir/scripts

# ── Deploy script ──
cat > /opt/memoir/scripts/deploy.sh << 'DEPLOY'
#!/bin/bash
set -euo pipefail
echo "=== Memoir Deploy === $(date)"
cd /opt/memoir/app

echo "Pulling latest code..."
git pull origin main

echo "Updating Python deps..."
for svc in auth_service core_api integration_service notification_service transcription_worker; do
    [ -f "$svc/requirements.txt" ] && "$svc/venv/bin/pip" install -q -r "$svc/requirements.txt"
done

echo "Building orchestrator..."
cd orchestrator && npm ci --silent && npm run build && npm prune --production --silent && cd ..

echo "Building frontend..."
cd frontend && npm ci --silent && AUTH_SERVICE_URL=http://localhost:8001 CORE_API_URL=http://localhost:8005 INTEGRATION_SERVICE_URL=http://localhost:8006 npm run build && cd ..

echo "Running auth_service migrations..."
cd auth_service && ENVIRONMENT=production PYTHONPATH=/opt/memoir/app/auth_service venv/bin/alembic upgrade head && cd ..

echo "Running core_api migrations..."
cd core_api && ENVIRONMENT=production PYTHONPATH=/opt/memoir/app/core_api venv/bin/alembic upgrade head && cd ..

echo "Rebuilding BotWorker..."
# MUST be before orchestrator restart so the pool spawns warm containers
# from the new image. Otherwise pool members boot with the stale image and
# only get recycled at the 15 min idle-age cap.
cd BotWorker && docker build -t botworker:latest . -q && cd ..

echo "Restarting services..."
ENABLED_SVCS=$(systemctl list-unit-files 'memoir-*.service' --state=enabled --no-legend | awk '{print $1}')
for svc in $ENABLED_SVCS; do
    sudo systemctl restart $svc
    sleep 2
    if systemctl is-active --quiet $svc; then echo "  $svc: OK"; else echo "  $svc: FAILED"; exit 1; fi
done

echo "=== Deploy complete === $(date)"
DEPLOY

# ── Health check script ──
cat > /opt/memoir/scripts/health-check.sh << 'HEALTH'
#!/bin/bash
SERVICES=("auth:8001" "core-api:8005" "integration:8006" "notification:8002" "orchestrator:4000")
FAILED=0
for svc in "${SERVICES[@]}"; do
    NAME="${svc%%:*}"; PORT="${svc#*:}"
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:${PORT}/health" 2>/dev/null || echo "000")
    if [ "$STATUS" -ge 200 ] && [ "$STATUS" -lt 400 ]; then echo "OK   $NAME"; else echo "FAIL $NAME ($STATUS)"; FAILED=1; fi
done
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:3000" 2>/dev/null || echo "000")
if [ "$STATUS" -ge 200 ] && [ "$STATUS" -lt 400 ]; then echo "OK   frontend"; else echo "FAIL frontend ($STATUS)"; FAILED=1; fi
DISK=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
MEM=$(free | grep Mem | awk '{printf("%.0f", $3/$2 * 100)}')
echo "DISK ${DISK}%  MEM ${MEM}%"
exit $FAILED
HEALTH

# ── Status script ──
cat > /opt/memoir/scripts/status.sh << 'STATUS'
#!/bin/bash
echo "=== Memoir Service Status ==="
for svc in $(systemctl list-unit-files 'memoir-*.service' --state=enabled --no-legend | awk '{print $1}'); do
    if systemctl is-active --quiet $svc; then
        echo "  ● $svc: RUNNING"
    else
        echo "  ○ $svc: STOPPED"
    fi
done
echo ""
echo "=== Traefik ==="
docker ps --filter name=traefik --format "  ● traefik: {{.Status}}" 2>/dev/null || echo "  ○ traefik: NOT RUNNING"
echo ""
echo "=== Resources ==="
echo "  Disk: $(df -h / | tail -1 | awk '{print $3"/"$2" ("$5")"}')"
echo "  RAM:  $(free -h | grep Mem | awk '{print $3"/"$2}')"
echo "  Swap: $(free -h | grep Swap | awk '{print $3"/"$2}')"
echo "  Load: $(uptime | awk -F'load average:' '{print $2}')"
STATUS

chmod +x /opt/memoir/scripts/*.sh
chown -R $DEPLOY_USER:$DEPLOY_USER /opt/memoir/scripts

# Set up cron for health check (every 5 min)
(crontab -u $DEPLOY_USER -l 2>/dev/null || true; echo "*/5 * * * * /opt/memoir/scripts/health-check.sh >> /var/log/memoir/health.log 2>&1") | sort -u | crontab -u $DEPLOY_USER -

log "Helper scripts created at /opt/memoir/scripts/"

# ════════════════════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════════════════════

section "SETUP COMPLETE"

echo -e "${GREEN}Generated credentials (SAVE THESE NOW):${NC}"
echo ""
echo "  Redis password:       $REDIS_PASSWORD"
echo "  Internal service key: $INTERNAL_SERVICE_KEY"
echo ""
echo -e "${GREEN}JWT keypair:${NC}"
echo "  Private: /etc/secrets/jwt/private.pem"
echo "  Public:  /etc/secrets/jwt/public.pem"
echo ""

section "Step 13/13: Start Services + Run Migrations"

# Run database migrations
log "Running database migrations..."
cd "$APP_DIR/auth_service"
sudo -u $DEPLOY_USER bash -c "ENVIRONMENT=production PYTHONPATH=. venv/bin/alembic upgrade head" 2>&1 && log "  auth_service migrations: OK" || warn "  auth_service migrations: FAILED"

cd "$APP_DIR/core_api"
sudo -u $DEPLOY_USER bash -c "ENVIRONMENT=production PYTHONPATH=. venv/bin/alembic upgrade head" 2>&1 && log "  core_api migrations: OK" || warn "  core_api migrations: FAILED"

cd "$APP_DIR"

# Start core services (transcription/resemble run on GPU, not EC2)
log "Starting core services..."
systemctl start memoir-auth
sleep 3
systemctl start memoir-core-api memoir-integration memoir-notification memoir-orchestrator memoir-frontend
sleep 3

# Verify
log "Service status:"
for svc in memoir-auth memoir-core-api memoir-integration memoir-notification memoir-orchestrator memoir-frontend; do
    if systemctl is-active --quiet $svc; then
        log "  ● $svc: RUNNING"
    else
        warn "  ○ $svc: FAILED — check: journalctl -u $svc -n 20"
    fi
done

echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  NEXT STEPS (manual):${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "  1. FILL IN REMAINING ENV FILES — edit files in /etc/secrets/env/"
echo "     Auto-injected: Redis, internal service key, all Fernet keys, Teams secret,"
echo "     Google OAuth, Slack OAuth, R2 storage, database URLs."
echo "     Still need: Microsoft/Azure AD (OUTLOOK_CLIENT_ID/SECRET),"
echo "     Groq API key, Anthropic API key, HuggingFace token, SendGrid API key."
echo "     Then restart: sudo systemctl restart memoir-auth memoir-core-api memoir-integration memoir-notification"
echo ""
echo "  2. SET DNS A RECORD:"
echo "     Point ${DOMAIN} → $(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<YOUR_ELASTIC_IP>')"
echo ""
echo "  3. VERIFY:"
echo "     /opt/memoir/scripts/health-check.sh"
echo "     /opt/memoir/scripts/status.sh"
echo ""
echo "  4. GPU SETUP (Vast.ai) — see plan/production-deployment-guide.md Steps 29-34"
echo ""

# Save setup info
cat > /opt/memoir/SETUP_INFO << SETUPEOF
Setup completed: $(date)
Redis password: ${REDIS_PASSWORD}
Internal service key: ${INTERNAL_SERVICE_KEY}
JWT private key: /etc/secrets/jwt/private.pem
JWT public key: /etc/secrets/jwt/public.pem
Env files: /etc/secrets/env/
Scripts: /opt/memoir/scripts/
SETUPEOF
chmod 600 /opt/memoir/SETUP_INFO
chown $DEPLOY_USER:$DEPLOY_USER /opt/memoir/SETUP_INFO

log "Setup info saved to /opt/memoir/SETUP_INFO"
log "Total setup time: script completed at $(date)"
