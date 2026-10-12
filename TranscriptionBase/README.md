# Transcription Service

A production-ready audio/video transcription API using Whisper AI. Supports YouTube links, direct media URLs, and file uploads with automatic language detection.

## Features

- **Multiple Input Sources**: YouTube URLs, direct media URLs, file uploads
- **Multilingual**: Automatic language detection
- **CPU Optimized**: Uses official OpenAI Whisper
- **Concurrent Safe**: Handles multiple simultaneous requests
- **Security**: SSRF protection, size limits, YouTube domain allowlist
- **Browser-Friendly**: Built-in test page at `/`

## Quick Start

### Local Development (Step-by-Step)

#### Step 1: Setup Environment

**Note:** OpenAI Whisper requires Python 3.9-3.12. Python 3.13+ is not yet supported.

```bash
# Create virtual environment (use Python 3.11 if available)
python3.11 -m venv venv  # Or: python3 -m venv venv (if Python 3.9-3.12)
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install system dependencies (macOS)
brew install ffmpeg

# Install system dependencies (Ubuntu/Debian)
# sudo apt-get install ffmpeg
```

#### Step 2: Run the Service

```bash
# Start the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### Step 3: Test in Browser

1. Open http://localhost:8000 in your browser
2. You'll see the test page with three tabs:
   - **File Upload**: Upload audio/video files
   - **Media URL**: Enter a direct URL to media
   - **YouTube**: Enter a YouTube link

3. Click **"Run 2 Parallel Requests"** to test concurrency
4. Click **"Health Check"** to verify the service is running

#### Step 4: API Documentation

Visit http://localhost:8000/docs for interactive API documentation (Swagger UI).

---

## Step-by-Step Verification Guide

### Step 1: Skeleton Verification
```bash
# Start server
uvicorn app.main:app --reload

# In browser:
# 1. Go to http://localhost:8000
# 2. Click "Run 2 Parallel Requests" - should see two 400 responses with unique Request IDs
# 3. Go to http://localhost:8000/docs - should see API documentation

# CLI verification:
curl http://localhost:8000/health
# Expected: {"status":"ok"}

curl -X POST http://localhost:8000/transcribe -H "Content-Type: application/json" -d '{}'
# Expected: 400 error with X-Request-ID header
```

### Step 2: File Upload Verification
```bash
# In browser:
# 1. Go to http://localhost:8000
# 2. Select "File Upload" tab
# 3. Choose a small audio file
# 4. Click "Submit"
# 5. Note the Request ID in the success message

# Concurrency test: Open two browser tabs, upload files simultaneously
# Both should succeed with different Request IDs

# CLI:
curl -X POST http://localhost:8000/transcribe \
  -F "file=@sample.mp3"
```

### Step 3: Media URL Verification
```bash
# In browser:
# 1. Go to http://localhost:8000
# 2. Select "Media URL" tab
# 3. Enter a public audio URL, e.g.:
#    https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3
# 4. Click "Submit"

# Test SSRF protection (should fail):
# Enter: http://localhost/audio.mp3
# Expected: Security error

# CLI:
curl -X POST http://localhost:8000/transcribe \
  -H "Content-Type: application/json" \
  -d '{"media_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"}'
```

### Step 4: YouTube Verification
```bash
# In browser:
# 1. Go to http://localhost:8000
# 2. Select "YouTube" tab
# 3. Enter a YouTube URL (short video recommended)
# 4. Click "Submit"

# CLI:
curl -X POST http://localhost:8000/transcribe \
  -H "Content-Type: application/json" \
  -d '{"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

### Step 5: Audio Normalization Verification
```bash
# The normalization happens automatically when transcribing.
# To verify FFmpeg is working:
ffmpeg -version

# Upload any audio file - it will be converted to 16kHz mono WAV internally
# Check logs for "Normalizing audio" and "Normalized audio" messages
```

### Step 6: Full Transcription Verification
```bash
# Upload a short audio file with speech and verify you get actual transcript text
# The response should be plain text (not JSON)

# Example with a local file:
curl -X POST http://localhost:8000/transcribe \
  -F "file=@speech.mp3"

# Expected: Plain text transcription output
```

---

## Concurrency Testing

### Browser Test
Click **"Run 2 Parallel Requests"** on the test page. You should see:
- Two responses with different Request IDs
- Both completing around the same time

### CLI Concurrency Tests

```bash
# Using Python asyncio script
python3 -c "
import asyncio
import httpx

async def test():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            client.post('http://localhost:8000/transcribe', json={}),
            client.post('http://localhost:8000/transcribe', json={})
        )
        for i, r in enumerate(results):
            print(f'Request {i+1}: {r.status_code}, ID: {r.headers.get(\"X-Request-ID\")}')

asyncio.run(test())
"

# Using hey (install: brew install hey)
hey -n 10 -c 2 -m POST -H "Content-Type: application/json" -d '{}' http://localhost:8000/transcribe

# Using wrk
wrk -t2 -c2 -d5s -s post.lua http://localhost:8000/transcribe
# (requires a post.lua script)

# Using ab (Apache Bench)
ab -n 10 -c 2 -p payload.json -T application/json http://localhost:8000/transcribe
# (create payload.json with: {})
```

### Run Automated Tests
```bash
# Run all tests
pytest tests/ -v

# Run concurrency tests specifically
pytest tests/test_concurrency.py -v
```

---

## Docker Deployment

### Build and Run

```bash
# Build the image
docker build -t transcription-service .

# Run with default settings (1 worker)
docker run -p 8000:8000 transcription-service

# Run with 2 workers for better concurrency
docker run -p 8000:8000 transcription-service \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

# Run with custom model size
docker run -p 8000:8000 \
  -e ASR_MODEL_SIZE=small \
  -e LOG_LEVEL=DEBUG \
  transcription-service
```

### Workers Tradeoff

| Workers | Memory | Concurrency | Use Case |
|---------|--------|-------------|----------|
| 1 | ~2GB | Low | Development, small traffic |
| 2 | ~4GB | Medium | Production, moderate traffic |
| 4+ | ~8GB+ | High | High traffic (needs more RAM) |

Each worker loads its own model copy, so memory scales linearly with workers.

---

## Cloud Deployment

### Generic VM (Ubuntu)

```bash
# 1. SSH into your VM
ssh user@your-vm-ip

# 2. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 3. Clone/copy the code
git clone <your-repo> transcription-service
cd transcription-service

# 4. Build and run
docker build -t transcription-service .
docker run -d --name transcription -p 8000:8000 --restart unless-stopped \
  -e ASR_MODEL_SIZE=base \
  transcription-service

# 5. (Optional) Setup with systemd
sudo tee /etc/systemd/system/transcription.service << 'EOF'
[Unit]
Description=Transcription Service
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStart=/usr/bin/docker start -a transcription
ExecStop=/usr/bin/docker stop transcription

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable transcription
sudo systemctl start transcription

# 6. Open firewall
sudo ufw allow 8000/tcp
```

### AWS ECS Fargate

#### 1. Push to ECR

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Create repository
aws ecr create-repository --repository-name transcription-service

# Build and push
docker build -t transcription-service .
docker tag transcription-service:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/transcription-service:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/transcription-service:latest
```

#### 2. Create Task Definition

```json
{
  "family": "transcription-service",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::<account-id>:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "transcription",
      "image": "<account-id>.dkr.ecr.us-east-1.amazonaws.com/transcription-service:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "ASR_MODEL_SIZE", "value": "base"},
        {"name": "LOG_LEVEL", "value": "INFO"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/transcription-service",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

Save as `task-definition.json` and register:

```bash
aws ecs register-task-definition --cli-input-json file://task-definition.json
```

#### 3. Create Service

```bash
# Create CloudWatch log group
aws logs create-log-group --log-group-name /ecs/transcription-service

# Create ECS cluster (if needed)
aws ecs create-cluster --cluster-name transcription-cluster

# Create service
aws ecs create-service \
  --cluster transcription-cluster \
  --service-name transcription-service \
  --task-definition transcription-service \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"
```

#### 4. View Logs

```bash
aws logs tail /ecs/transcription-service --follow
```

---

## API Reference

### Endpoints

#### `GET /health`
Health check endpoint.

**Response:** `{"status": "ok"}`

#### `GET /`
HTML test page for browser-based testing.

#### `POST /transcribe`

Transcribe audio/video to plain text.

**Request Options:**

1. **JSON with YouTube URL:**
   ```json
   {"youtube_url": "https://www.youtube.com/watch?v=..."}
   ```

2. **JSON with Media URL:**
   ```json
   {"media_url": "https://example.com/audio.mp3"}
   ```

3. **Multipart File Upload:**
   ```bash
   curl -X POST -F "file=@audio.mp3" http://localhost:8000/transcribe
   ```

**Success Response:**
- Status: `200 OK`
- Content-Type: `text/plain; charset=utf-8`
- Headers: `X-Request-ID: <uuid>`
- Body: Plain text transcription

**Error Response:**
- Status: `400` (bad request) or `500` (server error)
- Content-Type: `application/json`
- Headers: `X-Request-ID: <uuid>`
- Body: `{"error": "...", "detail": "..."}`

---

## Configuration

All settings via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `ASR_MODEL_SIZE` | `base` | Whisper model: tiny, base, small, medium, large-v3 |
| `ASR_DEVICE` | `cpu` | Device: cpu or cuda |
| `MAX_FILE_SIZE_MB` | `200` | Maximum upload/download size |
| `DOWNLOAD_TIMEOUT` | `300` | Media URL download timeout (seconds) |
| `YOUTUBE_TIMEOUT` | `600` | YouTube download timeout (seconds) |
| `ASR_TIMEOUT` | `1800` | Transcription timeout (seconds) |
| `YOUTUBE_ALLOWED_DOMAINS` | `youtube.com,youtu.be,...` | Comma-separated allowed domains |
| `YOUTUBE_COOKIES_FROM_BROWSER` | (empty) | Browser to get cookies from: chrome, firefox, safari, edge |
| `LOG_LEVEL` | `INFO` | Logging level |
| `TEMP_DIR_BASE` | `/tmp/transcription` | Base directory for temp files |

### Model Size Recommendations

| Model | Size | Memory | Speed | Accuracy | Use Case |
|-------|------|--------|-------|----------|----------|
| `tiny` | 75MB | ~1GB | Fastest | Lower | Testing, quick drafts |
| `base` | 145MB | ~1GB | Fast | Good | **Default, balanced** |
| `small` | 488MB | ~2GB | Medium | Better | Production |
| `medium` | 1.5GB | ~5GB | Slower | Great | High accuracy needs |
| `large-v3` | 3GB | ~10GB | Slowest | Best | Maximum accuracy |

---

## Security

### SSRF Protection
- DNS resolution validates hostnames don't resolve to private IPs
- Blocks: localhost, 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, link-local
- Only allows http:// and https:// schemes

### Size Limits
- Default 200MB max file size (configurable)
- Streaming download with size checking

### YouTube Allowlist
- Only allows configured YouTube domains
- Default: youtube.com, youtu.be, www.youtube.com, m.youtube.com

---

## Troubleshooting

### FFmpeg Not Found
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Docker: included in Dockerfile
```

### yt-dlp Errors / YouTube 403 Forbidden
```bash
# Update yt-dlp (YouTube changes frequently)
pip install -U yt-dlp

# If you still get 403 errors, use browser cookies:
# This uses your logged-in YouTube session to download
export YOUTUBE_COOKIES_FROM_BROWSER=chrome   # or: firefox, safari, edge
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or in Docker:
docker run -p 8000:8000 -e YOUTUBE_COOKIES_FROM_BROWSER=chrome transcription-service
```

### Out of Memory
- Use a smaller model (`ASR_MODEL_SIZE=tiny`)
- Reduce workers
- Increase instance memory

### Slow Transcription
- Use a smaller model for faster results
- Ensure input audio isn't excessively long
- Consider GPU acceleration (`ASR_DEVICE=cuda`)

---

## License

MIT License
