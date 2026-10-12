# Resemble Enhance Microservice

Isolated speech enhancement service that runs `resemble-enhance` with its own `torch==2.1.1` environment. This keeps the main TranscriptionBase app free to upgrade PyTorch for newer models (Canary-Qwen 2.5B, IndicConformer, etc.).

## What it does

Accepts a WAV audio file via HTTP, runs the resemble-enhance neural denoiser (diffusion model), and returns the enhanced audio. Used by **Path B** in the Audio Preprocessing UI for perceptual quality comparison.

**Important:** Resemble Enhance is a generative model — it can fabricate speech sounds not in the original audio. It should only be used for listening quality comparison, never as part of the ASR pipeline.

## Prerequisites

- Docker (recommended) or Python 3.10+
- ~2GB disk for the diffusion model (downloaded on first run)
- git-lfs (for model weights download)

## Quick Start with Docker

### Build the image

```bash
cd microservices/resemble-enhance
docker build -t resemble-enhance-service .
```

### Run the container

```bash
# CPU mode (default)
docker run -d --name resemble-enhance -p 8001:8001 resemble-enhance-service

# GPU mode (requires NVIDIA Container Toolkit)
docker run -d --name resemble-enhance -p 8001:8001 --gpus all resemble-enhance-service
```

### Verify it's running

```bash
curl http://localhost:8001/health
```

Expected response:
```json
{"status": "healthy", "torch_version": "2.1.1", "device": "cpu"}
```

## Quick Start with Docker Compose

From the project root:

```bash
docker compose up -d resemble-enhance
```

## Quick Start without Docker

```bash
cd microservices/resemble-enhance
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001
```

**Warning:** This installs `torch==2.1.1` in your current environment, which may conflict with other packages. Docker is strongly recommended.

## API Endpoints

### GET /health

Health check. Returns service status and torch version.

```bash
curl http://localhost:8001/health
```

### POST /enhance

Enhance an audio file. Accepts multipart form with a WAV file.

```bash
curl -X POST http://localhost:8001/enhance \
  -F "file=@input.wav" \
  --output enhanced.wav
```

**Input:** WAV file (any sample rate, mono or stereo)
**Output:** Enhanced WAV file

## Connecting to Main App

The main TranscriptionBase app calls this microservice when Path B is selected. Configure the URL in the main app:

### Environment variable

```bash
export RESEMBLE_ENHANCE_URL=http://localhost:8001
```

### Or in the main app's config

The default URL is `http://localhost:8001`. Change it if running on a different host/port.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| Port (uvicorn) | 8001 | Service port |
| GPU | Auto-detected | Uses CUDA if available, falls back to CPU |

## Troubleshooting

### Model download hangs or fails

Resemble-enhance downloads its diffusion model (~300MB) on first use. Ensure:
- Internet access from the container
- git-lfs is installed (included in Dockerfile)
- Sufficient disk space (~2GB)

### GPU not detected in Docker

Install NVIDIA Container Toolkit:
```bash
# Ubuntu/Debian
sudo apt install nvidia-container-toolkit
sudo systemctl restart docker
```

Then run with `--gpus all` flag.

### Port already in use

Change the port mapping:
```bash
docker run -d -p 9001:8001 resemble-enhance-service
```

Then update `RESEMBLE_ENHANCE_URL=http://localhost:9001` in the main app.

### Service is slow

The diffusion model is compute-intensive. On CPU, enhancement can take several minutes per file. GPU is strongly recommended for regular use.
