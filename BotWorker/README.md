# BotWorker

Meeting bot service that joins Google Meet meetings as a participant, records audio in 30-second chunks, and transcribes the full recording via TranscriptionBase after the meeting ends.

## Architecture

```
POST /join {meeting_url}
    |
    v
Launch Chromium --> Join Google Meet --> Record audio (30s chunks)
    |                                       |
    |                                  Save chunks locally
    |                                  (storage.save_chunk)
    |                                       |
    v                                       v
Meeting ends (detected by monitor)
    |
    v
Stop recorder --> Merge all chunks --> POST merged.ogg to TranscriptionBase
    |                                       |
    v                                       v
GET /status returns transcript    Event: transcription_complete
    |
    v
Cleanup (browser, files, processes)
```

## Quick Start

### Prerequisites
- Docker (recommended) or Python 3.11+
- TranscriptionBase service running

### Docker (recommended)
```bash
docker compose up
```

Then trigger a meeting join:
```bash
curl -X POST http://localhost:8000/join \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-1", "meeting_url": "https://meet.google.com/abc-defg-hij"}'
```

### Check status
```bash
curl http://localhost:8000/status
```

## Local Development (without Docker)

### macOS
```bash
brew install pulseaudio ffmpeg
```

### Linux
```bash
sudo apt install xvfb pulseaudio ffmpeg
```

### Setup
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Start infrastructure
```bash
# Start Xvfb (Linux only, macOS uses native display)
Xvfb :99 -screen 0 1920x1080x24 &

# Start PulseAudio
pulseaudio --start

# Create null sink
pactl load-module module-null-sink sink_name=bot_sink rate=48000 channels=1 format=s16le
```

### Run
```bash
DISPLAY=:99 uvicorn app.main:app --reload
```

## Running with Docker

```bash
# Build
docker build -t botworker .

# Run (--shm-size is critical for Chromium)
docker run --shm-size=2g -p 8000:8000 \
  -v ./recordings:/tmp/recordings \
  -v ./screenshots:/tmp/screenshots \
  botworker
```

Or with docker compose (includes TranscriptionBase):
```bash
docker compose up
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/status` | Current bot status, transcript |
| POST | `/join` | Start joining a meeting |
| POST | `/stop` | Stop current meeting |

See [docs/API.md](docs/API.md) for full details.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_URL` | `http://host.docker.internal:3000` | Backend for events |
| `TRANSCRIPTION_URL` | `http://host.docker.internal:8000` | TranscriptionBase URL |
| `BOT_NAME` | `Meeting Notetaker` | Name shown in meeting |
| `CHUNK_DURATION_SECONDS` | `30` | Audio chunk length |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `STORAGE_DIR` | `/tmp/recordings` | Local storage path |
| `WAITING_ROOM_TIMEOUT_SECONDS` | `120` | Max wait for admission |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` | Heartbeat frequency |
| `MEETING_MAX_DURATION_SECONDS` | `14400` | Max meeting (4 hours) |
| `TRANSCRIPTION_TIMEOUT` | `600` | Transcription API timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

## Project Structure

```
BotWorker/
├── app/
│   ├── main.py                  # FastAPI app + endpoints
│   ├── core/
│   │   ├── config.py            # Environment settings
│   │   └── logging.py           # Job-scoped structured logging
│   ├── models/
│   │   ├── enums.py             # BotState, EventType
│   │   └── schemas.py           # Pydantic models
│   ├── selectors/
│   │   └── google_meet.py       # CSS/aria selectors
│   └── services/
│       ├── browser.py           # Playwright Google Meet automation
│       ├── recorder.py          # FFmpeg segment muxer
│       ├── storage.py           # Local/S3 chunk storage
│       ├── transcriber.py       # Post-meeting transcription
│       ├── events.py            # Backend events + heartbeats
│       ├── meeting_monitor.py   # Meeting end detection
│       ├── lifecycle.py         # Main orchestrator
│       └── cleanup.py           # Resource teardown
├── tests/                       # Unit tests (45 tests)
├── Dockerfile                   # Xvfb + PulseAudio + Chromium
├── docker-compose.yml           # Full local stack
├── entrypoint.sh                # Container startup script
└── scripts/test_audio.sh        # Audio pipeline verification
```

## Testing

```bash
source venv/bin/activate
pytest tests/ -v
```

Inside Docker:
```bash
docker exec botworker pytest tests/ -v
```

Audio pipeline verification:
```bash
docker exec botworker bash scripts/test_audio.sh
```

## Troubleshooting

**PulseAudio not starting**: Ensure `--system` mode in Docker, or `--start` for user mode locally.

**Chromium crash**: Increase shared memory with `--shm-size=2g`. Check `DISPLAY` is set correctly.

**Selectors broken**: Google may update their Meet UI. Update selectors in `app/selectors/google_meet.py`.

**No audio captured**: Verify `bot_sink` exists with `pactl list sinks short`. Check FFmpeg is reading from `bot_sink.monitor`.

**Transcription timeout**: Increase `TRANSCRIPTION_TIMEOUT` for long meetings. Default is 600s (10 min).
