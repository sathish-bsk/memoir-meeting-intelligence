FROM python:3.10-slim

WORKDIR /app

# System dependencies for soundfile and audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    git \
    git-lfs \
    && git lfs install \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app.py .

# Expose port
EXPOSE 8001

# Run the service
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
