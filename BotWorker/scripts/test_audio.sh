#!/bin/bash
# Audio pipeline verification script with signal-presence check.
# Run inside Docker: docker exec <container> bash scripts/test_audio.sh

set -e

echo "=== Audio Pipeline Test ==="

# 1. Check PulseAudio
echo "1. Checking PulseAudio..."
if ! pactl info > /dev/null 2>&1; then
    echo "   FAIL: PulseAudio not running"
    exit 1
fi
echo "   PASS: PulseAudio running"

# 2. Check bot_sink
echo "2. Checking bot_sink..."
if ! pactl list sinks short | grep -q "bot_sink"; then
    echo "   FAIL: bot_sink not found"
    pactl list sinks short
    exit 1
fi
echo "   PASS: bot_sink exists"

# 3. Check bot_sink.monitor
echo "3. Checking bot_sink.monitor..."
if ! pactl list sources short | grep -q "bot_sink.monitor"; then
    echo "   FAIL: bot_sink.monitor not found"
    pactl list sources short
    exit 1
fi
echo "   PASS: bot_sink.monitor exists"

# 4. Inject a 1kHz test tone into bot_sink and record from bot_sink.monitor
TONE_FILE="/tmp/test_tone.wav"
TEST_FILE="/tmp/test_capture.wav"
echo "4. Signal-presence test (1kHz tone injection)..."

# Generate a 2s 1kHz sine wave and play into bot_sink
ffmpeg -y -f lavfi -i "sine=frequency=1000:duration=2" \
    -c:a pcm_s16le -ar 48000 -ac 2 "$TONE_FILE" 2>/dev/null

# Play tone into bot_sink (background) while recording from bot_sink.monitor
PULSE_SINK=bot_sink paplay --channels=2 --rate=48000 --format=s16le "$TONE_FILE" &
PLAY_PID=$!

# Record 3s from bot_sink.monitor
timeout 5 ffmpeg -y -f pulse -i bot_sink.monitor \
    -c:a pcm_s16le -ar 48000 -ac 2 \
    -t 3 "$TEST_FILE" 2>/dev/null

wait $PLAY_PID 2>/dev/null || true

if [ ! -f "$TEST_FILE" ]; then
    echo "   FAIL: Capture file not created"
    exit 1
fi

FILE_SIZE=$(stat -c%s "$TEST_FILE" 2>/dev/null || stat -f%z "$TEST_FILE" 2>/dev/null)
echo "   Capture file size: ${FILE_SIZE} bytes"

# 5. Verify sample rate
echo "5. Checking sample rate..."
SAMPLE_RATE=$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of default=noprint_wrappers=1:nokey=1 "$TEST_FILE")
if [ "$SAMPLE_RATE" != "48000" ]; then
    echo "   FAIL: Expected 48000 Hz, got $SAMPLE_RATE Hz"
    exit 1
fi
echo "   PASS: Sample rate is 48000 Hz"

# 6. Verify channel count
echo "6. Checking channels..."
CHANNELS=$(ffprobe -v error -select_streams a:0 -show_entries stream=channels -of default=noprint_wrappers=1:nokey=1 "$TEST_FILE")
if [ "$CHANNELS" != "2" ]; then
    echo "   FAIL: Expected 2 channels, got $CHANNELS"
    exit 1
fi
echo "   PASS: Channels is 2 (stereo)"

# 7. Verify codec
echo "7. Checking codec..."
CODEC=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$TEST_FILE")
if [ "$CODEC" != "pcm_s16le" ]; then
    echo "   FAIL: Expected pcm_s16le, got $CODEC"
    exit 1
fi
echo "   PASS: Codec is pcm_s16le (lossless)"

# 8. Verify signal presence (tone must be audible in capture)
echo "8. Checking signal presence..."
MAX_VOLUME=$(ffmpeg -i "$TEST_FILE" -af volumedetect -f null - 2>&1 | grep "max_volume" | awk '{print $5}')

if [ -z "$MAX_VOLUME" ]; then
    echo "   FAIL: Could not determine max volume"
    exit 1
fi

echo "   Max volume: ${MAX_VOLUME} dB"

# Remove the minus sign for comparison (max_volume is negative dB)
ABS_VOL=$(echo "$MAX_VOLUME" | tr -d '-')
# If max_volume is quieter than -30dB, the tone wasn't captured
THRESHOLD="30.0"
QUIET=$(echo "$ABS_VOL $THRESHOLD" | awk '{if ($1 > $2) print "yes"; else print "no"}')

if [ "$QUIET" = "yes" ]; then
    echo "   FAIL: Audio too quiet (${MAX_VOLUME} dB) — tone not captured"
    exit 1
fi
echo "   PASS: Signal detected (${MAX_VOLUME} dB)"

# 9. Show active sink-inputs
echo "9. Active sink-inputs:"
pactl list sink-inputs short 2>/dev/null || echo "   (none)"

# Cleanup
rm -f "$TONE_FILE" "$TEST_FILE"

echo ""
echo "=== All audio pipeline tests PASSED ==="
