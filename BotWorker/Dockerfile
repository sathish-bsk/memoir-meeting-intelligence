FROM python:3.11-slim

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: Xvfb, PulseAudio + PipeWire, FFmpeg, Chromium deps
# Note: pipewire-audio-client-libraries is excluded (conflicts with pulseaudio on Trixie)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    pulseaudio \
    dbus dbus-x11 \
    pipewire pipewire-pulse wireplumber \
    ffmpeg \
    xdotool \
    # Chromium dependencies
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libwayland-client0 \
    # Fonts
    fonts-liberation \
    fonts-noto-color-emoji \
    fontconfig \
    # Utilities
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and Chromium
RUN playwright install chromium

# Configure PulseAudio for system-wide mode with null sink
RUN mkdir -p /etc/pulse && \
    echo "load-module module-native-protocol-unix auth-anonymous=1" > /etc/pulse/system.pa && \
    echo "load-module module-null-sink sink_name=bot_sink rate=48000 channels=2 format=s16le sink_properties=device.description=BotSink" >> /etc/pulse/system.pa && \
    echo "set-default-sink bot_sink" >> /etc/pulse/system.pa

# Create directories
RUN mkdir -p /tmp/recordings /tmp/screenshots

# Generate a 10-second silent WAV file for fake audio capture
RUN ffmpeg -f lavfi -i anullsrc=r=48000:cl=mono -t 10 -q:a 0 /app/silence.wav -y 2>/dev/null

# Copy application code
COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh scripts/test_audio.sh

ENV DISPLAY=:99
ENV DOCKER=1
ENV AUDIO_BACKEND=pulseaudio
ENV AUDIO_CAPTURE_METHOD=system

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health'); r.raise_for_status()"

ENTRYPOINT ["./entrypoint.sh"]
