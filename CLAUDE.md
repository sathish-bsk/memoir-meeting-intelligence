# Memoir — Meeting Intelligence Platform

Record → Transcribe → Analyze → CRM. Monorepo with 12 services behind a Traefik gateway.

## Quick Start

```bash
./dev.sh              # Creates DBs, runs migrations, syncs keys, launches all services
./dev.sh stop         # Stop all services
./dev.sh status       # Check what's running
./dev.sh logs [svc]   # Tail logs (stored in .dev-logs/)
```

Docker alternative: `docker compose up` from root.

## Service Map

| Service | Dir | Port | Stack | Entry Point |
|---------|-----|------|-------|-------------|
| API Gateway | `api_gateway_service/` | 80/443 (8080 dashboard) | Traefik v3.2 | Config-driven |
| Auth | `auth_service/` | 8001 | Python, FastAPI | `app/main.py` |
| Core API | `core_api/` | 8005 | Python, FastAPI | `app/main.py` |
| Integration | `integration_service/` | 8006 | Python, FastAPI | `app/main.py` |
| Notification | `notification_service/` | 8002 | Python, FastAPI | `app/main.py` |
| Orchestrator | `orchestrator/` | 4000 | Node.js, TypeScript, Express, BullMQ | `src/index.ts` |
| BotWorker | `BotWorker/` | — | Python, Playwright, FFmpeg | `entrypoint.sh` |
| TranscriptionBase | `TranscriptionBase/` | 8000 | Python, FastAPI, faster-whisper, pyannote | `app/main.py` |
| Transcription Worker | `transcription_worker/` | — | Python (polling daemon) | `app/main.py` |
| Resemble Enhance | `resemble_enhance/` | 8010 | Python 3.10, FastAPI | `app.py` |
| Frontend | `frontend/` | 3000 | Next.js 14, React, TypeScript | `src/` |
| Chrome Extension | `chrome_extension/` | — | TypeScript, React, Webpack | `src/` |

Infrastructure: PostgreSQL 16 (5432), Redis 7 (6379), MinIO (9000/9001 dev only).

## Architecture

### Auth Flow
Traefik → ForwardAuth `/auth/verify` → X-User-* / X-User-Scopes headers forwarded upstream.

### JWT
ES256 keys in `keys/` dirs. 15-min access tokens, 7-day refresh with family-based rotation. Public key must be present in auth_service, core_api, and integration_service.

### Databases
- `auth_db` — auth_service only. Migrations: `auth_service/alembic/`
- `videoclip_db` — core_api + integration_service + TranscriptionBase. Migrations: `core_api/alembic/`

### Redis
DB 0: auth sessions. DB 1: core_api cache/queues.

### Inter-Service Auth
`INTERNAL_SERVICE_KEY` header for service-to-service calls.

### Shared Library
`videoclip_common/` — JWT verification, storage abstraction (S3/R2/local), HTTP clients, RBAC. Installed via `pip install -e` in Python services.

## Development Conventions

- **Python services**: FastAPI, async SQLAlchemy, asyncpg, venv at `.venv/`
- **Node services**: TypeScript, npm
- **Config**: `.env.development` (committed), `.env` (gitignored, local overrides)
- **Health checks**: All FastAPI services expose `GET /health`
- **No enforced linting**, no pre-commit hooks, no CI/CD

## Key Patterns

- **auth_service**: Repository pattern (repos → services → API routes)
- **core_api**: Direct service layer (services → API routes)
- **integration_service**: Shares `videoclip_db` with core_api — no own migrations
- **BotWorker**: Spawned as Docker containers by orchestrator (one per meeting). Complex system deps (Xvfb, PulseAudio, Chromium) — always test in Docker
- **Transcription Worker**: Polling daemon, not an HTTP server
- **Chrome Extension**: `npm run build` → `dist/`, load as unpacked extension. Two recording modes: mixed (single stream) and separate (tab + mic tracks)

## Common Pitfalls

- **Never commit** `.env`, `keys/`, or media files (already in .gitignore)
- **Key sync**: After regenerating ES256 keys in auth_service, copy the public key to core_api and integration_service
- **TranscriptionBase** has its own detailed `CLAUDE.md` — defer to it for ML pipeline questions
- **BotWorker** won't run properly outside Docker due to system deps
- Port 8000 is shared by TranscriptionBase and BotWorker — BotWorker only runs inside Docker so no conflict in dev

## Deployment

- **Target**: AWS EC2 (t3.small) + on-demand GPU, ap-south-1
- **IaC**: `terraform/`
- **Setup**: `scripts/setup-production.sh`
- **Deploy**: `scripts/deploy.sh` (git pull + preserve secrets + restart)
- **Runtime**: systemd + Traefik (no K8s)
