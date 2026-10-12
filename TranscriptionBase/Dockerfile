# Transcription Service Dockerfile
# Uses official OpenAI Whisper for transcription

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# - ffmpeg: required for audio processing
# - git: needed by some pip packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create temp directory
RUN mkdir -p /tmp/transcription

# Copy application code
COPY app/ ./app/

# Environment variables with defaults
ENV HOST=0.0.0.0
ENV PORT=8000
ENV ASR_MODEL_SIZE=base
ENV ASR_DEVICE=cpu
ENV MAX_FILE_SIZE_MB=200
ENV DOWNLOAD_TIMEOUT=300
ENV YOUTUBE_TIMEOUT=600
ENV ASR_TIMEOUT=1800
ENV LOG_LEVEL=INFO
ENV TEMP_DIR_BASE=/tmp/transcription

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
