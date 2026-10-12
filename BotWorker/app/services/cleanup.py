"""
Ordered resource teardown for meeting lifecycle.
"""
from typing import Optional

from app.core.logging import get_job_logger
from app.services.base_bot import MeetingBot
from app.services.recorder import AudioRecorder
# from app.services.webrtc_recorder import WebRTCRecorder  # experimental — not used in production
from app.services.meeting_monitor import MeetingMonitor
from app.services.events import EventSender
from app.services.speaker_tracker import SpeakerTracker


async def cleanup_all(
    job_id: str,
    monitor: Optional[MeetingMonitor] = None,
    recorder: Optional[AudioRecorder] = None,
    bot: Optional[MeetingBot] = None,
    events: Optional[EventSender] = None,
    speaker_tracker: Optional[SpeakerTracker] = None,
) -> int:
    """
    Ordered teardown of all resources.
    Returns total chunk count from recorder.
    """
    logger = get_job_logger(job_id)
    logger.info("Starting cleanup...")
    total_chunks = 0

    # 0. Stop speaker tracker
    if speaker_tracker:
        try:
            await speaker_tracker.stop()
        except Exception as e:
            logger.warning(f"Speaker tracker cleanup error: {e}")

    # 1. Stop monitor
    if monitor:
        try:
            await monitor.stop()
        except Exception as e:
            logger.warning(f"Monitor cleanup error: {e}")

    # 2. Stop recorder (finalizes last chunk)
    if recorder:
        try:
            total_chunks = await recorder.stop()
        except Exception as e:
            logger.warning(f"Recorder cleanup error: {e}")

    # 3. Leave meeting and close browser
    if bot:
        try:
            await bot.leave_meeting()
        except Exception as e:
            logger.warning(f"Leave meeting error: {e}")
        try:
            await bot.close()
        except Exception as e:
            logger.warning(f"Browser close error: {e}")

    # 4. Stop heartbeat
    if events:
        try:
            await events.stop_heartbeat()
        except Exception as e:
            logger.warning(f"Heartbeat stop error: {e}")

    logger.info(f"Cleanup complete. Total chunks: {total_chunks}")
    return total_chunks
