"""
Configuration management via environment variables.
"""
import os
from typing import List

_VALID_CAPTURE_METHODS = {"system", "browser"}
_AUDIO_CAPTURE_METHOD = os.getenv("AUDIO_CAPTURE_METHOD", "system")
if _AUDIO_CAPTURE_METHOD not in _VALID_CAPTURE_METHODS:
    if _AUDIO_CAPTURE_METHOD == "webrtc":
        raise ValueError(
            "AUDIO_CAPTURE_METHOD=webrtc was retired. "
            "Use 'browser' (Google Meet only, experimental) or 'system' (default)."
        )
    raise ValueError(
        f"AUDIO_CAPTURE_METHOD must be one of {sorted(_VALID_CAPTURE_METHODS)}, "
        f"got {_AUDIO_CAPTURE_METHOD!r}"
    )


class Settings:
    """Application settings loaded from environment variables."""

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Backend URL for sending events
    BACKEND_URL: str = os.getenv("BACKEND_URL", "http://host.docker.internal:3000")

    # TranscriptionBase URL
    TRANSCRIPTION_URL: str = os.getenv("TRANSCRIPTION_URL", "http://host.docker.internal:8000")
    TRANSCRIPTION_TIMEOUT: int = int(os.getenv("TRANSCRIPTION_TIMEOUT", "600"))

    # Bot settings
    BOT_NAME: str = os.getenv("BOT_NAME", "Meeting Notetaker")
    DISPLAY: str = os.getenv("DISPLAY", ":99")

    # Phase 3 (notetaker-email plan §5) — authenticated browser bot
    # identity. Defaults to "guest" (today's anonymous Chromium); set
    # to "authenticated" to use the persistent profile loaded from
    # R2 object keys for Google Meet + Microsoft Teams Chromium profile
    # tarballs (written by scripts/bootstrap-bot-session.py). Any auth
    # failure transparently falls back to "guest" so recording is never
    # blocked. Profile bytes live in the same R2 bucket as recordings;
    # bot fetches via the existing videoclip_common storage abstraction.
    MEMOIR_BOT_AUTH_MODE: str = os.getenv("MEMOIR_BOT_AUTH_MODE", "guest")
    MEMOIR_BOT_INVITE_EMAIL: str = os.getenv("MEMOIR_BOT_INVITE_EMAIL", "hello@meetmemoir.com")
    MEMOIR_BOT_GOOGLE_PROFILE_KEY: str = os.getenv("MEMOIR_BOT_GOOGLE_PROFILE_KEY", "")
    MEMOIR_BOT_MICROSOFT_PROFILE_KEY: str = os.getenv("MEMOIR_BOT_MICROSOFT_PROFILE_KEY", "")

    # PulseAudio
    PULSE_SINK_NAME: str = os.getenv("PULSE_SINK_NAME", "bot_sink")

    # Recording
    # Tier 1.8.5: default lowered from 30 → 10 so the first chunk file lands on
    # disk ~20 s earlier (UX/visibility, not recording-start latency). FFmpeg
    # is capturing from spawn; this just shortens the segment-muxer flush
    # interval. Override via CHUNK_DURATION_SECONDS env var.
    CHUNK_DURATION_SECONDS: int = int(os.getenv("CHUNK_DURATION_SECONDS", "10"))
    FFMPEG_AUDIO_BITRATE: str = os.getenv("FFMPEG_AUDIO_BITRATE", "128k")

    # Storage
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")
    STORAGE_DIR: str = os.getenv("STORAGE_DIR", "/tmp/recordings")

    # Timeouts
    WAITING_ROOM_TIMEOUT_SECONDS: int = int(os.getenv("WAITING_ROOM_TIMEOUT_SECONDS", "120"))
    # Lifecycle-level upper bound on the entire navigate + admit path
    # (asyncio.wait_for wrap around bot.join_meeting). Defends against
    # any platform-internal _wait_for_admission loop that doesn't honor
    # its own deadline — observed 2026-05-29 prod: bot stayed in the
    # Meet lobby for >2.5 min uploading silent FFmpeg chunks despite
    # the 120 s WAITING_ROOM_TIMEOUT. This is the always-true ceiling.
    # Set higher than WAITING_ROOM_TIMEOUT_SECONDS by 30 s so platform
    # logic gets first refusal; if it fails, the wrap fires.
    JOIN_MEETING_TIMEOUT_SECONDS: int = int(os.getenv("JOIN_MEETING_TIMEOUT_SECONDS", "150"))
    HEARTBEAT_INTERVAL_SECONDS: int = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))
    MEETING_MAX_DURATION_SECONDS: int = int(os.getenv("MEETING_MAX_DURATION_SECONDS", "14400"))
    MEETING_END_POLL_INTERVAL: int = int(os.getenv("MEETING_END_POLL_INTERVAL", "2"))
    NOONE_JOINED_TIMEOUT_SECONDS: int = int(os.getenv("NOONE_JOINED_TIMEOUT_SECONDS", "1200"))

    # Audio backend
    AUDIO_BACKEND: str = os.getenv("AUDIO_BACKEND", "pulseaudio")

    # Capture method.
    # - "system" (default): PulseAudio bot_sink.monitor → FFmpeg.
    # - "browser" (experimental, Google Meet only for now): Chromium
    #   extension + offscreen document + AudioWorklet → localhost
    #   WebSocket → BotWorker. See plan/browser-capture-implementation-plan.md.
    # The validator at module top ensures only these two values are accepted.
    AUDIO_CAPTURE_METHOD: str = _AUDIO_CAPTURE_METHOD

    # Browser args — base set is shared between system and browser modes.
    # Fake-media flags are intentionally omitted: they replace real audio
    # devices with synthetic ones and prevent Chromium from routing output
    # through the PulseAudio default sink (bot_sink), which is what FFmpeg
    # records. Keep the audio service in-process and disable background
    # throttling so meeting playback follows a simpler, more stable render
    # path.
    #
    # NOTE on `--disable-features`: Chromium accepts only ONE
    # --disable-features switch on the command line; later occurrences
    # override earlier ones rather than merging. So the disabled features
    # are computed up-front and emitted as a single flag. Adding a second
    # --disable-features=... anywhere in this list is a bug.
    #
    # Both modes leave AudioServiceOutOfProcess at Chrome's default
    # (out-of-process). The May 6 commit fbdecde disabled it for system
    # mode, forcing audio into the renderer — empirically this produced
    # constant "shhh" hiss over speech because Meet's UI work starves the
    # audio thread under load. Reverted 2026-05-12 after the user reported
    # bot recordings sounded measurably worse than a May 5 reference.
    # If you ever consider re-disabling, A/B against the May 5 reference.
    _DISABLED_FEATURES: List[str] = ["WebRtcHideLocalIpsWithMdns"]
    if os.getenv("DOCKER", "") == "1":
        _DISABLED_FEATURES.append("TranslateUI")

    BROWSER_ARGS: List[str] = (
        [
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",
            f"--disable-features={','.join(_DISABLED_FEATURES)}",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ]
        # Extension flags ONLY when AUDIO_CAPTURE_METHOD=browser. The
        # extension is what implements tab-audio capture in browser mode;
        # in system mode loading it would be a pointless attack surface.
        + (
            [
                "--load-extension=/app/extension",
                "--disable-extensions-except=/app/extension",
                "--auto-accept-this-tab-capture",
            ]
            if _AUDIO_CAPTURE_METHOD == "browser"
            else []
        )
        + (
            [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-first-run",
                "--disable-component-update",
            ]
            if os.getenv("DOCKER", "") == "1"
            else []
        )
    )

    # Screenshots directory
    SCREENSHOTS_DIR: str = os.getenv("SCREENSHOTS_DIR", "/tmp/screenshots")

    # Auto-exit after lifecycle completes (for orchestrator-managed containers)
    AUTO_EXIT: bool = os.getenv("AUTO_EXIT", "0") == "1"
    AUTO_EXIT_DELAY_SECONDS: int = int(os.getenv("AUTO_EXIT_DELAY_SECONDS", "5"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
