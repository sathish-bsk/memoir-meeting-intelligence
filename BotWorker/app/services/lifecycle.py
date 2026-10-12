"""
Main meeting lifecycle orchestrator.
Wires browser, recorder, storage, events, and monitor together.
Recording is decoupled from transcription — BotWorker sends a
recording_complete event and exits; a separate worker dispatches
transcription jobs.
"""
import asyncio
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_job_logger
from app.models.enums import BotState, EventType
from app.models.schemas import JoinRequest
from app.services.base_bot import MeetingDeniedError, BotKickedError, BrowserError
from app.services.browser_capture_recorder import BrowserCaptureRecorder
from app.services.recorder import AudioRecorder
from app.services.storage import create_storage
from app.services.events import EventSender
from app.services.meeting_monitor import MeetingMonitor
from app.services.cleanup import cleanup_all
from app.services.speaker_tracker import SpeakerTracker


async def _pactl(*args: str) -> str:
    """Run `pactl <args>` and return stdout (empty string on failure)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _wait_for_sink_input_on_bot_sink(
    sink_name: str, logger, timeout: float = 10.0
) -> bool:
    """Confirm Chromium has a sink-input routed to `sink_name`.

    Returns True if a sink-input attached to that sink appears within
    `timeout` seconds. Logs the resolved sink index, the sink-input list,
    and the matching application name. Logs a warning and returns False
    on timeout — caller decides whether to proceed.
    """
    sinks = await _pactl("list", "sinks", "short")
    sink_index = None
    for line in sinks.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == sink_name:
            sink_index = parts[0]
            break

    if sink_index is None:
        logger.warning(
            f"Preflight: sink '{sink_name}' not found in `pactl list sinks short`. "
            "Recording may capture silence."
        )
        return False

    logger.info(f"Preflight: '{sink_name}' is sink index {sink_index}")

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        inputs_short = await _pactl("list", "sink-inputs", "short")
        for line in inputs_short.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == sink_index:
                # Look up the application name for the matching input.
                full = await _pactl("list", "sink-inputs")
                app_name = _extract_app_name_for_input(full, parts[0])
                logger.info(
                    f"Preflight: sink-input #{parts[0]} attached to '{sink_name}' "
                    f"(app={app_name or 'unknown'})"
                )
                return True
        await asyncio.sleep(0.5)

    logger.warning(
        f"Preflight: no sink-input attached to '{sink_name}' after {timeout:.0f}s. "
        f"Current sink-inputs:\n{(await _pactl('list', 'sink-inputs', 'short')) or '(none)'}"
    )
    return False


def _extract_app_name_for_input(full_output: str, input_index: str) -> str:
    """Pull `application.name` for a given sink-input index from `pactl list sink-inputs`."""
    in_block = False
    app_name = ""
    for line in full_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sink Input #"):
            in_block = stripped == f"Sink Input #{input_index}"
            app_name = ""
            continue
        if in_block and "application.name" in stripped and "=" in stripped:
            app_name = stripped.split("=", 1)[1].strip().strip('"')
            return app_name
    return app_name


async def run_meeting_lifecycle(request: JoinRequest, state: dict, bot_name: str):
    """
    Main coroutine that orchestrates the entire meeting lifecycle:
    launch browser -> join meeting -> record -> detect end -> transcribe -> cleanup.
    """
    job_id = request.job_id
    logger = get_job_logger(job_id)
    logger.info("Starting meeting lifecycle")

    bot = None
    recorder = None
    monitor = None
    speaker_tracker = None
    storage = create_storage()
    sink_preflight_task = None
    captions_task = None
    panel_task = None

    async with EventSender(job_id) as events:
        try:
            # Send join_started event
            await events.send_event(EventType.JOIN_STARTED, {"meeting_url": request.meeting_url})

            # --- Launch browser ---
            state["state"] = BotState.LAUNCHING_BROWSER
            from app.main import create_bot
            bot = create_bot(request.meeting_url, job_id, bot_name)
            bot.set_event_sender(events)
            await bot.launch()

            logger.info(
                f"Audio capture: backend={settings.AUDIO_BACKEND}, "
                f"method={settings.AUDIO_CAPTURE_METHOD}"
            )

            # --- Pre-start FFmpeg recorder (system mode only) ---
            # Phase 1 (2026-05-14): start the AudioRecorder NOW, BEFORE the
            # bot navigates / waits in the WR / gets admitted. FFmpeg's
            # 2-3 s cold-start happens in parallel with the bot's ~30 s
            # navigation, so by the time admission lands, audio capture is
            # already live — no cold-start latency on the user-visible
            # "click admit -> Stop Recording" path.
            #
            # System-mode safety: pre-admission chunks are silence (Meet's
            # waiting room doesn't pipe meeting audio to participants
            # waiting outside; bot_sink stays silent). The merge step's
            # silenceremove filter (storage.py) trims all leading silence
            # below -40 dB, so the pre-admission window is dropped from
            # the final recording — no privacy issue, no audio drift.
            #
            # Browser-mode path (BrowserCaptureRecorder) still uses
            # post-admission start — it captures from the Meet tab
            # directly, which doesn't exist until navigation completes.
            chunk_dir = Path(settings.STORAGE_DIR) / job_id / "raw_chunks"

            async def on_chunk_ready(chunk_path: Path, chunk_index: int):
                """Callback when a chunk is ready: save to storage and send event."""
                storage_key = await storage.save_chunk(chunk_path, job_id, chunk_index)
                state["chunks_saved"] = chunk_index + 1
                await events.send_event(
                    EventType.CHUNK_SAVED,
                    {
                        "chunk_index": chunk_index,
                        "storage_key": storage_key,
                        "file_size_bytes": chunk_path.stat().st_size,
                    },
                )

            sink_preflight_task = None
            if settings.AUDIO_CAPTURE_METHOD == "system":
                recorder = AudioRecorder(job_id, chunk_dir, on_chunk_ready=on_chunk_ready)
                await recorder.start()
                logger.info("Pre-started recorder (system mode); FFmpeg cold-start runs parallel with navigation")
                try:
                    from app.services.phase_log import log_phase
                    log_phase("recording_started", mode="system_prestart")
                except Exception:
                    pass
                # Sink-routing diagnostics in the background (same as before — just
                # moved up because the recorder is up now). System-mode only.
                sink_preflight_task = asyncio.create_task(
                    _wait_for_sink_input_on_bot_sink(
                        settings.PULSE_SINK_NAME, logger, timeout=10.0
                    )
                )

            # --- Navigate and join ---
            state["state"] = BotState.NAVIGATING
            try:
                # Hard ceiling on the whole navigate+admit path. Each
                # platform's _wait_for_admission has its own deadline
                # (WAITING_ROOM_TIMEOUT_SECONDS), but a misbehaving loop
                # has previously been observed to outrun it in prod —
                # FFmpeg keeps uploading silence chunks while the bot
                # is parked in the lobby with no way out. asyncio.wait_for
                # is the always-true fallback. Same path as a denial:
                # send BOT_DENIED + exit.
                admitted = await asyncio.wait_for(
                    bot.join_meeting(request.meeting_url),
                    timeout=settings.JOIN_MEETING_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                state["state"] = BotState.ERROR
                state["error"] = (
                    f"Lifecycle timeout: not admitted within "
                    f"{settings.JOIN_MEETING_TIMEOUT_SECONDS}s"
                )
                logger.warning(state["error"])
                await events.send_event(
                    EventType.BOT_DENIED,
                    {"reason": "lobby_timeout", "timeout_s": settings.JOIN_MEETING_TIMEOUT_SECONDS},
                )
                return
            except MeetingDeniedError:
                state["state"] = BotState.ERROR
                state["error"] = "Entry denied by host"
                await events.send_event(EventType.BOT_DENIED)
                return
            except BrowserError as e:
                state["state"] = BotState.ERROR
                state["error"] = str(e)
                await events.send_event(EventType.BOT_ERROR, {"error": str(e)})
                return

            if not admitted:
                state["state"] = BotState.ERROR
                state["error"] = "Failed to join meeting"
                await events.send_event(EventType.BOT_ERROR, {"error": "Not admitted"})
                return

            # --- In meeting ---
            state["state"] = BotState.IN_MEETING
            state["joined_at"] = datetime.utcnow()
            await events.send_event(EventType.JOINED_MEETING)

            # Wait for in-meeting toolbar to fully load (PulseAudio admission
            # can fire before the real toolbar replaces the pre-join UI).
            # Tier 1.2: adaptive wait. Two paths:
            #   - Platform overrides _is_in_meeting() (currently Meet only):
            #     poll for up to 1 s at 50 ms cadence; exit as soon as the
            #     toolbar is visible. Falls through to 1 s sleep if toolbar
            #     not visible within budget (rare — pre-join UI still up).
            #   - Platform does NOT override (Teams, Zoom): we get
            #     NotImplementedError, and fall back to the legacy 3 s sleep —
            #     bit-for-bit identical to pre-Tier-1.2 behavior. Teams/Zoom
            #     get the speedup once their _is_in_meeting() ships (plan §13).
            logger.info("Waiting for in-meeting toolbar to load...")
            try:
                # First attempt — does the platform implement _is_in_meeting()?
                first_check = await bot._is_in_meeting()
                # If we got here, platform has the override. Use adaptive poll.
                toolbar_loaded = first_check
                if not toolbar_loaded:
                    for _ in range(19):  # 19 more iters → 1 s total budget
                        await asyncio.sleep(0.05)
                        try:
                            if await bot._is_in_meeting():
                                toolbar_loaded = True
                                break
                        except Exception:
                            break
                if not toolbar_loaded:
                    # Override exists but toolbar not visible yet — use a
                    # 1 s fallback (Meet UI usually settles within 1 s of
                    # admission). Total adaptive-path budget: ~2 s.
                    await asyncio.sleep(1)
            except NotImplementedError:
                # Platform hasn't shipped _is_in_meeting() yet — preserve
                # legacy safe behavior with no speedup. Teams/Zoom land here.
                await asyncio.sleep(3)

            # Start recording FIRST to avoid losing initial audio
            chunk_dir = Path(settings.STORAGE_DIR) / job_id / "raw_chunks"

            # System-mode recorder was pre-started right after bot.launch()
            # (see "Pre-start FFmpeg recorder" block above). For browser
            # mode the recorder still starts here because it needs the
            # Meet page to be loaded first. Either way, we fire the
            # RECORDING_STARTED event now so the existing webhook handler
            # stamps recording_started_at if it isn't already set.
            if settings.AUDIO_CAPTURE_METHOD == "browser":
                # Meet-only initial scope. Teams/Zoom support deferred until
                # we have per-platform validation references — see
                # plan/browser-capture-implementation-plan.md.
                from app.main import detect_platform
                platform = detect_platform(request.meeting_url)
                if platform != "google_meet":
                    raise ValueError(
                        f"AUDIO_CAPTURE_METHOD=browser supports google_meet only "
                        f"(got {platform!r}); set AUDIO_CAPTURE_METHOD=system"
                    )
                recorder = BrowserCaptureRecorder(
                    job_id, chunk_dir, page=bot._page, on_chunk_ready=on_chunk_ready
                )
                await recorder.start()
                logger.info("Recording via browser tab capture")
                try:
                    from app.services.phase_log import log_phase
                    log_phase("recording_started", mode="browser")
                except Exception:
                    pass
                await events.send_event(EventType.RECORDING_STARTED)
            else:
                # System mode — recorder is already running. Just fire the
                # event so backend stamps recording_started_at.
                logger.info("Recording confirmed (system mode, pre-started)")
                try:
                    from app.services.phase_log import log_phase
                    log_phase("recording_started", mode="system")
                except Exception:
                    pass
                await events.send_event(EventType.RECORDING_STARTED)

            # Tier 1.8.1 + 1.8.3: in-meeting mute + diagnostic screenshot moved
            # out of bot.join_meeting() (which blocked admission from returning).
            # Both are now post-recording-start background tasks. The pre-join
            # _disable_media already muted the mic; this is a safety net only.
            # The screenshot is non-blocking (asyncio.create_task) so a slow
            # Playwright screenshot can't delay the rest of lifecycle startup.
            async def _post_record_safety_net():
                try:
                    await bot._mute_in_meeting()
                except Exception as e:
                    logger.warning(f"post-record mute safety-net failed: {e}")
            asyncio.create_task(_post_record_safety_net())
            asyncio.create_task(bot._take_screenshot("in_meeting"))

            # Enable captions and participant panel AFTER recording starts.
            # These are name-detection helpers (caption-based speaker
            # attribution + participant scraping) — useful but NOT
            # required for the recording itself. Run as background tasks
            # with timeouts so a stuck Meet UI cannot block the lifecycle
            # from reaching stop_event.wait() (witnessed 2026-05-07:
            # enable_captions took 96s and open_participant_panel never
            # returned, preventing manual_stop from being processed).
            async def _bg_with_timeout(coro_factory, label, timeout):
                try:
                    await asyncio.wait_for(coro_factory(), timeout=timeout)
                    logger.info(f"{label}: completed")
                except asyncio.TimeoutError:
                    logger.warning(
                        f"{label}: timed out after {timeout}s — proceeding without it"
                    )
                except Exception as e:
                    logger.warning(f"{label}: failed: {e}")

            captions_task = asyncio.create_task(
                _bg_with_timeout(bot.enable_captions, "enable_captions", 30)
            )
            panel_task = asyncio.create_task(
                _bg_with_timeout(bot.open_participant_panel, "open_participant_panel", 30)
            )

            # Start heartbeat
            await events.start_heartbeat()

            # Start meeting monitor
            monitor = MeetingMonitor(job_id, bot)
            await monitor.start()

            # Start active speaker tracker
            recording_dir = Path(settings.STORAGE_DIR) / job_id
            speaker_tracker = SpeakerTracker(job_id, bot, recording_dir=recording_dir)
            await speaker_tracker.start()

            # --- Wait for meeting end or manual stop ---
            stop_event = state.get("stop_event")
            if stop_event:
                # Wait for either meeting end or manual stop
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(monitor.meeting_ended.wait()),
                        asyncio.create_task(stop_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
            else:
                await monitor.meeting_ended.wait()

            # --- Meeting ended ---
            # Timing instrumentation — added 2026-05-14 to find the real
            # bottleneck in the post-stop chain. Each step logs its
            # wall-clock duration so production log analysis can pinpoint
            # which one takes the seconds. Remove once root cause is
            # identified and fixed.
            _stop_t0 = time.monotonic()
            state["state"] = BotState.MEETING_ENDED
            end_reason = monitor.end_reason or "manual_stop"
            logger.info(f"[STOP_TIMING] asyncio.wait returned end_reason={end_reason}")

            # Final participant name capture before leaving — re-enabled
            # for instrumentation. If this is the slow step, we'll see it
            # in the log below.
            _t_before_names = time.monotonic()
            try:
                final_names = await bot.get_participant_names()
                if final_names:
                    monitor.participant_names.update(final_names)
            except Exception as e:
                logger.warning(f"get_participant_names failed: {e}")
            _t_after_names = time.monotonic()
            logger.info(
                f"[STOP_TIMING] get_participant_names took "
                f"{(_t_after_names - _t_before_names) * 1000:.0f}ms"
            )

            # Parallelize leave_meeting() and send_event(MEETING_ENDED).
            # Both are independent: page.close() tears down the bot's
            # browser tab (so Meet sees the bot leave); send_event tells
            # core_api the bot is done (so the extension's polling sees
            # status="processing" and the floating button flips).
            #
            # Why parallel safety holds:
            #   * page.close() returns AFTER Playwright finishes its RPC
            #     cleanup, but the host-visible bot-leaving (Meet UI
            #     update for other participants) is driven by the bot's
            #     WebRTC peer disconnecting — which happens within ~50 ms
            #     of page.close starting. So the host sees the bot leave
            #     well BEFORE page.close returns, and well before
            #     send_event returns. The button-flip ordering ("after
            #     bot ejected") is preserved by physics, not by sequencing.
            #   * If send_event finishes first, status="processing" lands
            #     before the host's Meet UI shows the bot gone — possible
            #     in pathological cases, but the host already initiated
            #     the stop themselves, so they expect the bot to leave.
            #
            # Both paths use try/except internally so a failure in one
            # doesn't crash the other. Wrapping in gather with
            # return_exceptions=True so any individual failure doesn't
            # short-circuit the cleanup path that follows.
            _t_before_parallel = time.monotonic()
            results = await asyncio.gather(
                bot.leave_meeting(),
                events.send_event(EventType.MEETING_ENDED, {"reason": end_reason}),
                return_exceptions=True,
            )
            _t_after_parallel = time.monotonic()
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.warning(
                        f"[STOP_TIMING] parallel step {i} raised: {r}"
                    )
            logger.info(
                f"[STOP_TIMING] leave_meeting + send_event in parallel took "
                f"{(_t_after_parallel - _t_before_parallel) * 1000:.0f}ms "
                f"| TOTAL post-stop: {(_t_after_parallel - _stop_t0) * 1000:.0f}ms"
            )

            # Stop speaker tracker and save events (v2 schema including
            # the participant roster from MeetingMonitor). The roster is
            # what MeetingMonitor accumulated during the meeting via its
            # DOM panel observer — bundling it here makes the R2 file
            # self-contained for retranscription.
            if speaker_tracker:
                await speaker_tracker.stop()
                recording_dir = Path(settings.STORAGE_DIR) / job_id
                participants = set(monitor.participant_names) if monitor else set()
                speaker_tracker.save_events(recording_dir, participants=participants)

            # Stop recorder to finalize last chunk
            await monitor.stop()
            total_chunks = await recorder.stop()

            if end_reason == "bot_kicked":
                await events.send_event(EventType.BOT_KICKED)

            # --- Send recording_complete (transcription is handled by a separate worker) ---
            if total_chunks > 0:
                merged_path = await storage.get_merged_audio(job_id)
                participant_list = sorted(monitor.participant_names) if monitor else []
                await events.send_event(
                    EventType.RECORDING_COMPLETE,
                    {
                        "recording_path": f"{job_id}/{merged_path.name}",
                        "total_chunks": total_chunks,
                        "participants": participant_list,
                        "peak_participants": monitor._peak_participants if monitor else 0,
                    },
                )
            else:
                logger.warning("No chunks recorded, skipping recording_complete")

            # --- Completed ---
            state["state"] = BotState.COMPLETED
            logger.info("Meeting lifecycle completed")

        except asyncio.CancelledError:
            logger.info("Lifecycle cancelled")
            state["state"] = BotState.ERROR
            state["error"] = "Cancelled"

        except Exception as e:
            logger.exception(f"Lifecycle error: {e}")
            state["state"] = BotState.ERROR
            state["error"] = str(e)
            await events.send_event(EventType.BOT_ERROR, {"error": str(e)})

        finally:
            # Cleanup all resources
            state["state"] = BotState.CLEANING_UP
            total = await cleanup_all(
                job_id,
                monitor=monitor,
                recorder=recorder,
                bot=bot,
                events=events,
                speaker_tracker=speaker_tracker,
            )
            await events.send_event(
                EventType.CLEANUP_COMPLETE, {"total_chunks": total}
            )

            for task in (sink_preflight_task, captions_task, panel_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            if state["state"] == BotState.CLEANING_UP:
                state["state"] = BotState.COMPLETED
