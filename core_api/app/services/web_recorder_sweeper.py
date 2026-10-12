"""Web Recorder orphan sweeper.

When a user clicks "Start Recording" on /record:
  1. POST /api/meetings creates a meeting row with status="recording"
  2. POST /api/meetings/{id}/tab-audio/sessions creates an upload session
  3. getDisplayMedia() prompts the browser tab picker
  4. User can cancel at this point

The cancel endpoint at POST /api/meetings/{id}/web-recorder-cancel cleans
this up when the page knows the user cancelled. But the browser can lose
the chance to call that endpoint when:
  - The user closes the tab while the prompt is open
  - The user navigates away mid-prompt
  - The browser crashes
  - navigator.sendBeacon fails for any reason

Without a backstop, those failures leave meeting rows in `status=recording`
with zero uploaded chunks indefinitely. They pollute the user's meetings
list and never naturally transition to a terminal state.

This sweeper finds those orphan rows and marks them as failed/cancelled.
Runs every 5 minutes from main.py:_scheduler_loop. The 15-minute grace
window matches "user clicked Start, walked away, didn't come back" — long
enough to not flap on slow networks, short enough that ghost rows don't
linger visibly.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Meetings older than this in `recording` status with zero chunks are
# considered orphaned. Tuned to be longer than a "user is choosing which
# tab to share" window but short enough that ghost rows don't pile up.
ORPHAN_GRACE_MINUTES = 15


async def sweep_orphan_web_recorder_meetings(db: AsyncSession) -> int:
    """Mark abandoned web_recorder meetings as failed.

    Criteria:
      - source = 'web_recorder'
      - status = 'recording'
      - created_at < NOW() - INTERVAL '15 minutes'
      - either no upload session exists, OR session.total_chunks = 0

    Updates each match in place:
      - status = 'failed'
      - failure_reason = 'user_cancelled'
      - ended_at = NOW()

    Returns the number of rows marked.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ORPHAN_GRACE_MINUTES)

    # Single UPDATE: mark meetings as failed when ALL of:
    #   - source = 'web_recorder' and status = 'recording'
    #   - created at least ORPHAN_GRACE_MINUTES ago
    #   - NO chunk has ever been received for any session of this meeting
    #
    # Checking the chunks table directly (not the total_chunks counter)
    # avoids a race where a chunk row was inserted but the counter
    # update hasn't committed yet. Either signal is enough to consider
    # the recording "live."
    result = await db.execute(
        sa_text(
            """
            UPDATE meetings AS m
            SET status = 'failed',
                failure_reason = 'user_cancelled',
                ended_at = NOW()
            WHERE m.source = 'web_recorder'
              AND m.status = 'recording'
              AND m.created_at < :cutoff
              AND NOT EXISTS (
                  SELECT 1
                  FROM tab_audio_upload_sessions s
                  LEFT JOIN tab_audio_upload_chunks c ON c.session_id = s.id
                  WHERE s.meeting_id = m.id
                    AND (s.total_chunks > 0 OR c.id IS NOT NULL)
              )
            """
        ),
        {"cutoff": cutoff},
    )
    await db.commit()
    swept = result.rowcount or 0
    if swept:
        logger.info(
            f"Web recorder sweeper: marked {swept} orphan meeting(s) as failed "
            f"(no chunks received > {ORPHAN_GRACE_MINUTES} min)"
        )
    return swept
