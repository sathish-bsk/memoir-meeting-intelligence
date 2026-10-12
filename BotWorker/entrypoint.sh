#!/bin/bash
set -e

echo "=== BotWorker Entrypoint ==="

# Sequential Xvfb → PulseAudio bootstrap. Tier 1.3 attempted to parallelize
# these (start both, then fast-poll for readiness) to shave 1-2 s off the
# join time, but on a memory-constrained t3.micro the concurrent startup
# thrashed and Xvfb timed out even at an 8 s budget (validated in EC2 prod
# on 2026-05-12). Reverted to the legacy sequential pattern: Xvfb first,
# verified ready, then PulseAudio. Reliable across host sizes; the latency
# loss is ~1 s on a warm host vs the parallel approach.
echo "Starting Xvfb on display :99..."
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

# Verify Xvfb is ready before starting PulseAudio. The readiness signal
# is the X11 socket Xvfb creates at /tmp/.X11-unix/X<display> when it
# starts accepting connections. This requires no extra tools (xdpyinfo
# is NOT in the bot image — earlier readiness checks relying on it
# silently failed forever, validated 2026-05-12 in EC2 prod). Budget
# tunable via BOT_XVFB_READY_TIMEOUT_S (default 10 s).
XVFB_TIMEOUT_S="${BOT_XVFB_READY_TIMEOUT_S:-10}"
xvfb_iters=$((XVFB_TIMEOUT_S * 10))   # 100 ms per iteration
xvfb_ready=0
for i in $(seq 1 $xvfb_iters); do
    if kill -0 $XVFB_PID 2>/dev/null && [ -S /tmp/.X11-unix/X99 ]; then
        xvfb_ready=1
        break
    fi
    sleep 0.1
done
if [ "$xvfb_ready" = "0" ]; then
    echo "ERROR: Xvfb failed to start within ${XVFB_TIMEOUT_S} s"
    exit 1
fi
echo "Xvfb ready (PID: $XVFB_PID)"

echo "Starting PulseAudio audio backend..."
export PULSE_SERVER=unix:/run/pulse/native
pulseaudio \
    --system \
    --daemonize \
    --no-cpu-limit \
    --disallow-exit \
    --exit-idle-time=-1 \
    --log-level=warning

# Verify PulseAudio is accepting commands. Budget tunable via
# BOT_PA_READY_TIMEOUT_S (default 10 s).
PA_TIMEOUT_S="${BOT_PA_READY_TIMEOUT_S:-10}"
pa_iters=$((PA_TIMEOUT_S * 10))       # 100 ms per iteration
pa_ready=0
for i in $(seq 1 $pa_iters); do
    if pactl info > /dev/null 2>&1; then
        pa_ready=1
        break
    fi
    sleep 0.1
done
if [ "$pa_ready" = "0" ]; then
    echo "ERROR: PulseAudio failed to respond within ${PA_TIMEOUT_S} s"
    exit 1
fi
echo "PulseAudio ready"

# Explicit routing: ensure Chromium outputs to bot_sink
pactl set-default-sink bot_sink 2>/dev/null || true
pactl set-default-source bot_sink.monitor 2>/dev/null || true
export PULSE_SINK=bot_sink

# Diagnostics
echo "=== Audio Diagnostics ==="
pactl info 2>/dev/null | grep -E "Default Sink|Default Source|Server Name" || true
pactl list sinks short 2>/dev/null || true
pactl list sources short 2>/dev/null || true
echo "========================="

# Verify bot_sink exists.
# Tier 1.4: tightened cadence from 1 s × 10 attempts to 200 ms × 50 attempts.
# Total budget is identical (10 s) but typical hit time is sub-second, so
# the bot starts ~0.5-1 s earlier on the common path. Slow-path behavior
# (FATAL after 10 s) is unchanged.
bot_sink_ready=0
for i in $(seq 1 50); do
    if pactl list sinks short 2>/dev/null | grep -q "bot_sink"; then
        bot_sink_ready=1
        echo "bot_sink verified"
        break
    fi
    sleep 0.2
done
if [ "$bot_sink_ready" = "0" ]; then
    echo "ERROR: bot_sink not found after 10 s"
    pactl list sinks short 2>/dev/null
    exit 1
fi

# --- Commented out: PipeWire + webrtc capture path (experimental) ---
# To re-enable, set AUDIO_BACKEND=pipewire and AUDIO_CAPTURE_METHOD=webrtc
#
# if [ "$AUDIO_BACKEND" = "pipewire" ]; then
#     export XDG_RUNTIME_DIR=/tmp/runtime-root
#     mkdir -p "$XDG_RUNTIME_DIR"
#     chmod 700 "$XDG_RUNTIME_DIR"
#     mkdir -p /run/dbus
#     dbus-daemon --system --fork 2>/dev/null || true
#     eval $(dbus-launch --sh-syntax)
#     if [ "$AUDIO_CAPTURE_METHOD" = "webrtc" ]; then
#         echo "load-module module-native-protocol-unix auth-anonymous=1" > /etc/pulse/system.pa
#     fi
#     pipewire &
#     sleep 1
#     wireplumber &
#     sleep 1
#     pipewire-pulse &
#     sleep 2
#     export PULSE_SERVER=unix:$XDG_RUNTIME_DIR/pulse/native
#     if [ "$AUDIO_CAPTURE_METHOD" != "webrtc" ]; then
#         pactl load-module module-null-sink sink_name=bot_sink rate=48000 channels=2 format=s16le
#         pactl set-default-sink bot_sink
#         pactl set-default-source bot_sink.monitor
#     else
#         pactl set-source-mute @DEFAULT_SOURCE@ 1 2>/dev/null || true
#     fi
# fi

echo "=== Starting BotWorker API ==="
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
