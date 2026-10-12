"""
Internal webhook routes for receiving events from the Orchestrator.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.meeting import Meeting
from app.models.memoir_summary import MemoirSummary
from app.models.speaker import MeetingParticipant
from app.models.transcription import Transcription
from app.models.transcription_job import TranscriptionJob
from app.services import meeting_status_cache
from videoclip_common.events import RedisEventPublisher
from videoclip_common.service_auth import verify_internal_call
from videoclip_common.storage import create_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/webhooks", tags=["webhooks"])


# Source → human-readable platform label for emails. Used by
# _email_platform_label below.
#
# Sources fall into three buckets:
#
# 1. Source-derived label (no URL parsing). These either have no real
#    meeting URL (upload, web_recorder post-migration-040) or the URL
#    field stores something that isn't a browser-shaped host (zoom_cloud
#    stores a Zoom recording UUID; teams_cloud stores a Teams join URL
#    that's mostly opaque; link_upload stores the source link).
# 2. URL-host parse. bot/calendar/extension/extension_tab_audio meetings
#    have real `https://meet.google.com/...` or similar URLs; splitting
#    on "/" and taking index 2 gives the host, matches historical behavior.
# 3. Generic fallback for unknown sources or missing URL — returns None,
#    email templates render this as no Platform: field.
_SOURCE_PLATFORM_LABEL = {
    "web_recorder": "Web Recorder",
    "upload": "Upload",
    "link_upload": "Link Upload",
    "zoom_cloud": "Zoom Cloud",
    "teams_cloud": "Teams Cloud",
}


def _email_platform_label(meeting: Meeting) -> str | None:
    """Return a platform label suitable for email template `platform=` field.

    Branches on meeting.source first so web_recorder + upload (which have
    no meeting_url after migration 040) render a friendly name instead of
    a NoneType error or the "session" / "local" gibberish that the old
    `meeting_url.split("/")[2]` code produced. Also covers zoom_cloud +
    teams_cloud where the URL field isn't a browser host. Bot / calendar
    / extension_tab_audio / extension all fall through to URL-host parse,
    preserving historical email behavior.
    """
    label = _SOURCE_PLATFORM_LABEL.get(meeting.source or "")
    if label:
        return label
    if meeting.meeting_url:
        try:
            return meeting.meeting_url.split("/")[2]
        except (IndexError, AttributeError):
            return None
    return None


def _replace_names_in_obj(obj, replacements: dict[str, str]):
    """Recursive find-and-replace of speaker names across dict/list/str."""
    if isinstance(obj, str):
        for old, new in replacements.items():
            obj = obj.replace(old, new)
        return obj
    if isinstance(obj, dict):
        return {k: _replace_names_in_obj(v, replacements) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_names_in_obj(item, replacements) for item in obj]
    return obj


def _build_name_replacement_map(
    speaker_name_map: dict[str, str] | None,
    speaker_map: dict,
) -> dict[str, str]:
    """Build a safety-net replacement map for SPEAKER_XX labels in summary.json.

    The primary name resolution happens in TranscriptionBase (before P5 runs).
    This only catches any remaining SPEAKER_XX labels that weren't replaced.
    """
    replacements = {}
    if not speaker_map:
        return replacements

    for label, v in speaker_map.items():
        name = v.get("name", "") if isinstance(v, dict) else ""
        if name and label.startswith("SPEAKER_"):
            replacements[label] = name

    return replacements


def _build_llm_inferred_names(
    summary_data: dict | None,
    speaker_name_map: dict[str, str] | None,
    label_stats_keys: list[str],
) -> dict[str, str]:
    """Extract LLM participant names and map them to diarization labels.

    Returns {SPEAKER_XX: "LLM Name"} for labels that have LLM-inferred names.
    """
    if not summary_data:
        return {}

    p5_participants = []
    meeting_info = summary_data.get("meeting", {})
    if isinstance(meeting_info, dict):
        p5_participants = meeting_info.get("participants", [])

    if not p5_participants:
        return {}

    llm_names = {}

    if speaker_name_map:
        reverse_overlap = {v: k for k, v in speaker_name_map.items()}
        for p in p5_participants:
            name = p.get("name", "") if isinstance(p, dict) else ""
            if not name or name.startswith("SPEAKER_"):
                continue
            if name in reverse_overlap:
                llm_names[reverse_overlap[name]] = name
                continue
            for overlap_name, label in reverse_overlap.items():
                if label not in llm_names and name.lower() in overlap_name.lower():
                    llm_names[label] = name
                    break

    if not llm_names and label_stats_keys:
        llm_name_list = [
            p.get("name", "") if isinstance(p, dict) else ""
            for p in p5_participants
            if isinstance(p, dict) and p.get("name") and not p.get("name", "").startswith("SPEAKER_")
        ]
        sorted_labels = sorted(label_stats_keys)
        for i, name in enumerate(llm_name_list):
            if i < len(sorted_labels):
                llm_names[sorted_labels[i]] = name

    return llm_names


async def _get_meeting_by_job_id(db: AsyncSession, job_id: str) -> Meeting:
    """Look up a meeting by its bot_job_id.

    For CRM extraction jobs (bot_job_id = "crm-extract-{meeting_id}"),
    extract the meeting_id and look up directly.
    """
    import uuid as _uuid

    # Handle synthetic job IDs that embed the meeting_id
    for prefix in ("crm-extract-", "intel-", "intel-rerun-"):
        if job_id.startswith(prefix):
            meeting_id_str = job_id[len(prefix):]
            try:
                meeting_uuid = _uuid.UUID(meeting_id_str)
            except ValueError:
                raise HTTPException(404, f"Invalid meeting_id in job_id: {job_id}")
            result = await db.execute(
                select(Meeting).where(Meeting.id == meeting_uuid)
            )
            meeting = result.scalar_one_or_none()
            if not meeting:
                raise HTTPException(404, f"No meeting found for id: {meeting_id_str}")
            return meeting

    # Detailed timing for the SELECT itself — separates query time from
    # session/cursor overhead. Logged unconditionally so we always have
    # the data when meeting_lookup>100ms is observed at the higher level.
    _t_before = time.monotonic()
    result = await db.execute(
        select(Meeting).where(Meeting.bot_job_id == job_id)
    )
    _t_after_execute = time.monotonic()
    meeting = result.scalar_one_or_none()
    _t_after_scalar = time.monotonic()
    exec_ms = (_t_after_execute - _t_before) * 1000
    scalar_ms = (_t_after_scalar - _t_after_execute) * 1000
    if exec_ms > 100 or scalar_ms > 100:
        logger.info(
            f"[MEETING_LOOKUP_DETAIL] db.execute={exec_ms:.0f}ms "
            f"scalar_one_or_none={scalar_ms:.0f}ms total={(exec_ms + scalar_ms):.0f}ms"
        )
    if not meeting:
        raise HTTPException(404, f"No meeting found for job_id: {job_id}")
    return meeting


async def _handle_joined_meeting(db: AsyncSession, meeting: Meeting, payload: dict) -> None:
    """Handle joined_meeting event: bot has joined the call.

    Phase 2 (2026-05-14): also stamps recording_started_at in the same
    commit. With Phase 1's pre-started FFmpeg, the bot has been capturing
    audio since waiting-room entry — the pre-admission silence is trimmed
    at merge. So the moment admission is signaled (by bot or by extension
    fast-path), recording IS effectively underway and the button can
    safely flip to "Stop Recording" without audio loss.

    Idempotent on recording_started_at — only sets if currently null, so
    duplicate joined_meeting events (extension fast-path racing with the
    bot's own webhook) don't overwrite an earlier timestamp.
    """
    now = datetime.now(timezone.utc)
    meeting.status = "recording"
    meeting.started_at = now
    if meeting.recording_started_at is None:
        meeting.recording_started_at = now
    await db.commit()
    logger.info(f"Meeting {meeting.id} status -> recording")


async def _handle_meeting_ended(db: AsyncSession, meeting: Meeting, payload: dict) -> None:
    """Handle meeting_ended event: the call has ended, processing begins."""
    meeting.status = "processing"
    if not meeting.ended_at:
        meeting.ended_at = datetime.now(timezone.utc)
    if meeting.started_at and meeting.ended_at:
        meeting.duration_seconds = int(
            (meeting.ended_at - meeting.started_at).total_seconds()
        )
    await db.commit()
    logger.info(f"Meeting {meeting.id} status -> processing")


async def _handle_recording_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle recording_complete event: bot finished recording, transcription job queued.

    The orchestrator has already inserted a transcription_jobs row. We just
    update the meeting status so the frontend shows "waiting for transcription".
    Also creates MeetingParticipant records from bot-captured participant names.
    """
    meeting.status = "waiting_for_transcription"
    if not meeting.ended_at:
        meeting.ended_at = datetime.now(timezone.utc)
    if meeting.started_at and meeting.ended_at:
        meeting.duration_seconds = int(
            (meeting.ended_at - meeting.started_at).total_seconds()
        )

    # Create MeetingParticipant records from bot-captured names (deduplicated).
    # ON CONFLICT DO NOTHING against the partial unique index from migration 040
    # so duplicate webhook deliveries (orchestrator retries on slow DB →
    # webhook timeout → retry) don't trip UniqueViolationError. Pre-migration
    # this used plain db.add() which worked because nothing enforced uniqueness;
    # post-migration the second delivery would 500. Same pattern tab_audio.py
    # already uses for the web recorder participant upsert.
    #
    # Performance: single multi-row INSERT instead of N per-row round-trips.
    # Each round-trip to RDS under concurrent v3-page load costs ~500 ms
    # (network + WAL flush + index check), so 2 participants used to add
    # ~1 s to dispatch and 5+ participants pushed it past 3 s. Batching
    # collapses the whole list to one round-trip. Same ON CONFLICT
    # semantics — any row that conflicts on the partial unique index is
    # silently skipped, individually, by Postgres.
    BOT_NAMES = {"meeting notetaker", "memoir notetaker", "memoir bot", "memoir", "notetaker"}
    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}
    participants = event_data.get("participants", [])
    if participants:
        from sqlalchemy import text as sa_text
        import uuid as _uuid

        # Build the deduplicated list first so we know the row count up
        # front. seen_names dedupes within the payload; ON CONFLICT in
        # SQL dedupes against rows already in the table.
        seen_names: set[str] = set()
        rows: list[tuple[str, str]] = []  # (uuid, name) pairs
        for name in participants:
            if not name or not isinstance(name, str):
                continue
            name_stripped = name.strip()
            if not name_stripped or name_stripped.lower() in BOT_NAMES:
                continue
            if name_stripped in seen_names:
                continue
            seen_names.add(name_stripped)
            rows.append((str(_uuid.uuid4()), name_stripped))

        if rows:
            # Multi-row VALUES with positional placeholders. We can't use
            # asyncpg's executemany() because it doesn't carry ON CONFLICT
            # semantics reliably across our SQLAlchemy version + we want
            # the single network round-trip the multi-row form guarantees.
            values_sql = ", ".join(
                f"(:id_{i}, :mid, :name_{i}, :src, 0, 0, 0, 0, NOW(), NOW())"
                for i in range(len(rows))
            )
            params: dict[str, str] = {"mid": str(meeting.id), "src": "bot_capture"}
            for i, (row_id, row_name) in enumerate(rows):
                params[f"id_{i}"] = row_id
                params[f"name_{i}"] = row_name

            await db.execute(
                sa_text(
                    f"""
                    INSERT INTO meeting_participants
                        (id, meeting_id, platform_display_name, mapping_source,
                         mapping_confidence, talk_time_seconds, segment_count,
                         word_count, created_at, updated_at)
                    VALUES {values_sql}
                    ON CONFLICT (meeting_id, (LOWER(platform_display_name)), mapping_source)
                    WHERE diarization_label IS NULL
                    DO NOTHING
                    """
                ),
                params,
            )
            logger.info(
                f"Meeting {meeting.id}: upserted {len(rows)} participant rows from bot (batched, idempotent)"
            )

    await db.commit()
    logger.info(f"Meeting {meeting.id} status -> waiting_for_transcription")


def _parse_version_from_path(output_base_path: str) -> int | None:
    """Extract version number from a versioned output path like 'meetings/{id}/v2/'."""
    import re
    match = re.search(r"/v(\d+)/?$", output_base_path.rstrip("/") + "/")
    return int(match.group(1)) if match else None


async def _handle_preprocess_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle preprocess_complete webhook (operation type 1 done).

    No meeting status change. Queues operation type 4 (enhance) with
    the preprocess R2 path so the enhance step knows where to find the audio.
    """
    import json as _json
    import uuid as _uuid

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    tj_id = event_data.get("transcription_job_id")
    meeting_id_str = str(meeting.id)

    # Read the preprocess job's result + params to forward to enhance
    tj = None
    tj_params = {}
    tj_result = {}
    if tj_id:
        result = await db.execute(
            select(TranscriptionJob).where(TranscriptionJob.id == _uuid.UUID(tj_id))
        )
        tj = result.scalar_one_or_none()
        if tj:
            tj_params = tj.params if isinstance(tj.params, dict) else {}
            tj_result = (tj.result if tj and tj.result else {}) or {}

    preprocess_path = tj_result.get("preprocess_path", f"meetings/{meeting_id_str}/preprocess/")
    output_base_path = tj_params.get("output_base_path", f"meetings/{meeting_id_str}/v1/")

    # Tier 1.5 — persist the recording_path returned by Type 1 immediately so
    # meeting.recording_path reflects the correct extension (FLAC vs WAV) for
    # the audio-urls endpoint. Without this, the placeholder set at meeting
    # creation (always .wav) would persist until Type 5 completes, breaking
    # frontend playback for FLAC-encoded uploads. Type 5's webhook later
    # preserves this value via the fallback in _handle_transcription_complete.
    recording_path_from_t1 = tj_result.get("recording_path")
    if recording_path_from_t1:
        meeting.recording_path = recording_path_from_t1
        await db.commit()

    # Tier 1.4 — fetch CRM contacts ONCE per meeting and propagate via params
    # so Type 5 + Type 7 don't each re-fetch from the network. Embedding in
    # params is canonical SaaS-job-pipeline practice (state travels with the
    # job, survives redeploys, consistent on retranscribe). If the fetch
    # fails here, downstream stages fall back to the existing HTTP fetch
    # path — no behavior regression.
    crm_contacts = []
    crm_contacts_fetched = False
    try:
        from app.clients.crm_client import list_crm_contacts
        crm_contacts = await list_crm_contacts(str(meeting.team_id))
        # Mark fetched=True regardless of result count — a successful empty
        # response is a valid state ("team has 0 CRM contacts") and should
        # NOT trigger the downstream HTTP-fetch fallback.
        crm_contacts_fetched = True
        logger.info(f"Meeting {meeting.id}: fetched {len(crm_contacts)} CRM contacts for params propagation")
    except Exception as e:
        # Leave crm_contacts_fetched=False so Type 5 falls back to HTTP fetch.
        logger.warning(f"Meeting {meeting.id}: CRM contact prefetch failed (non-fatal, downstream will fallback): {e}")

    # Tier 2.1 — adaptive Type 4 skip when audio doesn't need Resemble Enhance.
    # Decision is made in Type 1 (`skip_enhance` flag based on SNR + clipping)
    # and surfaced in tj_result. We gate the skip on:
    #   - global env var ENABLE_ADAPTIVE_SKIP (default ON; flip OFF for fast rollback)
    #   - per-team flag (TODO: teams.adaptive_skip_enabled column, defaults TRUE)
    # If the skip fires, we synthesize an enhance_path that points at the
    # preprocess output so Type 7 + Type 5 transparently read preprocessed_audio.
    import os as _os
    adaptive_skip_globally_enabled = _os.environ.get("ENABLE_ADAPTIVE_SKIP", "true").lower() != "false"
    skip_enhance = bool(tj_result.get("skip_enhance", False)) and adaptive_skip_globally_enabled
    snr_db = tj_result.get("snr_db")
    clipping_pct = tj_result.get("clipping_pct")

    if skip_enhance:
        # Audit log: every skip decision logged with the signals that fired.
        # Easy to grep/filter later when reviewing whether a transcript came
        # from the skip path vs. the full Resemble path.
        logger.info(
            f"Meeting {meeting.id}: Tier 2.1 skip_enhance fired "
            f"(snr_db={snr_db}, clipping_pct={clipping_pct}); "
            f"queuing Type 7 directly with enhance_path = preprocess_path"
        )

        # Surface the skip in the loader UI: enhance row jumps to 100% with
        # a "Skipped" label. The frontend renders any 100%-complete stage
        # whose label contains "skipped" in amber as an explicit skip
        # indicator (vs. greyed-out "pending" which would mislead).
        try:
            await meeting_status_cache.get_shared_redis().hset(
                f"meeting_progress:{meeting.id}",
                mapping={
                    "enhance_percent": "100",
                    "enhance_label": "Skipped (clean audio)",
                },
            )
        except Exception as _e:
            logger.warning(
                f"Meeting {meeting.id}: failed to write skip indicator to "
                f"progress redis (non-fatal, loader UI may show pending): {_e}"
            )


        # Queue Type 7 (fast P5) directly, skipping Type 4. enhance_path
        # aliases preprocess_path so Type 7's existing download logic for
        # `enhanced_audio.wav` will fail; the file-lookup adapter at
        # _run_asr_p5_fast handles enhance_skipped=True by reading
        # preprocessed_audio.wav instead.
        # Look up the original Type 1 job for ASR-relevant params (mirrors
        # _handle_enhance_complete's lookup so Type 7 gets meeting_title,
        # participant_names, language, model_size, etc.).
        original_tj_result = await db.execute(
            select(TranscriptionJob).where(
                TranscriptionJob.meeting_id == meeting.id,
                TranscriptionJob.operation_type == 1,
            ).order_by(TranscriptionJob.created_at.desc()).limit(1)
        )
        original_tj = original_tj_result.scalar_one_or_none()
        original_params = {}
        if original_tj and original_tj.params:
            original_params = original_tj.params if isinstance(original_tj.params, dict) else {}

        # Fast-email speaker-attribution change (2026-05-13): diarization (T6)
        # now runs BEFORE T7 so the fast email can show real speaker names. The
        # adaptive-skip path queues T6 directly (skipping T4); when T6
        # completes, _handle_diarization_complete queues T7. All ASR-relevant
        # params are carried through T6 so T7 can build its prompt later.
        diar_params = {
            "preprocess_path": preprocess_path,
            "enhance_path": preprocess_path,  # alias — points at preprocess output
            "enhance_skipped": True,           # informational flag for adapter
            "output_base_path": output_base_path,
            "team_id": str(meeting.team_id),
            "input_audio_path": original_params.get("input_audio_path", ""),
            "meeting_title": original_params.get("meeting_title"),
            "participant_names": original_params.get("participant_names", []),
            "language": original_params.get("language"),
            "engine": original_params.get("engine"),
            "model_size": original_params.get("model_size"),
            "phone_mode": original_params.get("phone_mode", True),
            "recording_source": original_params.get("recording_source", "bot"),
            "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
            "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            "max_retries": 2,
            "crm_contacts": crm_contacts,
            "crm_contacts_fetched": crm_contacts_fetched,  # Tier 1.4 fix
            # Audit signals stored in params so post-hoc analysis can correlate
            # transcript quality with the skip decision.
            "tier_2_1_signals": {
                "snr_db": snr_db,
                "clipping_pct": clipping_pct,
                "skipped": True,
            },
        }

        from sqlalchemy import text as sa_text
        await db.execute(
            sa_text(
                "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
                "VALUES (:mid, :bjid, 6, 'waiting', :params)"
            ),
            {
                "mid": meeting_id_str,
                "bjid": (tj.bot_job_id if tj else f"diar-{meeting_id_str}"),
                "params": _json.dumps(diar_params),
            },
        )
        await db.commit()
        logger.info(f"Meeting {meeting.id}: Type 4 SKIPPED; diarization (Type 6) queued directly (T7 follows after T6)")
        return

    # Standard path — Type 4 runs as before
    enhance_params = {
        "preprocess_path": preprocess_path,
        "output_base_path": output_base_path,
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
        "crm_contacts": crm_contacts,  # Tier 1.4 — propagated via chain
        "crm_contacts_fetched": crm_contacts_fetched,  # Tier 1.4 fix
        "tier_2_1_signals": {
            "snr_db": snr_db,
            "clipping_pct": clipping_pct,
            "skipped": False,
        },
    }

    from sqlalchemy import text as sa_text
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 4, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": (tj.bot_job_id if tj else f"enhance-{meeting_id_str}"),
            "params": _json.dumps(enhance_params),
        },
    )
    await db.commit()
    logger.info(f"Meeting {meeting.id}: enhance job queued (operation_type=4)")


async def _handle_enhance_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle enhance_complete webhook (operation type 4 done).

    Queues operation type 7 (fast ASR + fast P5 + email send). Type 7 then
    queues Type 6 (diarization), which queues Type 5 (polish), etc.
    """
    import json as _json
    import uuid as _uuid

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    tj_id = event_data.get("transcription_job_id")
    meeting_id_str = str(meeting.id)

    tj = None
    tj_params = {}
    tj_result = {}
    if tj_id:
        result = await db.execute(
            select(TranscriptionJob).where(TranscriptionJob.id == _uuid.UUID(tj_id))
        )
        tj = result.scalar_one_or_none()
        if tj:
            tj_params = tj.params if isinstance(tj.params, dict) else {}
            tj_result = (tj.result if tj and tj.result else {}) or {}

    enhance_path = tj_result.get("enhance_path", f"meetings/{meeting_id_str}/enhance/")
    preprocess_path = tj_params.get("preprocess_path", f"meetings/{meeting_id_str}/preprocess/")
    output_base_path = tj_params.get("output_base_path", f"meetings/{meeting_id_str}/v1/")

    # Look up the original Type 1 job to forward ASR-relevant params to Type 7
    original_tj_result = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.operation_type == 1,
        ).order_by(TranscriptionJob.created_at.desc()).limit(1)
    )
    original_tj = original_tj_result.scalar_one_or_none()
    original_params = {}
    if original_tj and original_tj.params:
        original_params = original_tj.params if isinstance(original_tj.params, dict) else {}

    # Fast-email speaker-attribution change (2026-05-13): diarization (T6) now
    # runs BEFORE T7. This handler queues T6; T7 is queued later by
    # _handle_diarization_complete. All ASR-relevant params are carried through
    # T6 so the T6→T7 hop can rebuild T7 params without re-reading Type 1.
    diar_params = {
        "preprocess_path": preprocess_path,
        "enhance_path": enhance_path,
        "output_base_path": output_base_path,
        "team_id": str(meeting.team_id),
        "input_audio_path": original_params.get("input_audio_path", ""),
        "meeting_title": original_params.get("meeting_title"),
        "participant_names": original_params.get("participant_names", []),
        "language": original_params.get("language"),
        "engine": original_params.get("engine"),
        "model_size": original_params.get("model_size"),
        "phone_mode": original_params.get("phone_mode", True),
        "recording_source": original_params.get("recording_source", "bot"),
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
        # Tier 1.4 — propagate CRM contacts from Type 4's params (originally
        # injected in _handle_preprocess_complete)
        "crm_contacts": tj_params.get("crm_contacts", []),
        "crm_contacts_fetched": tj_params.get("crm_contacts_fetched", False),
        # Tier 2.1 — propagate audit signals (enhance_skipped is False here
        # since Type 4 actually ran). Stored in params for analytics.
        "enhance_skipped": False,
        "tier_2_1_signals": tj_params.get("tier_2_1_signals", {}),
    }

    from sqlalchemy import text as sa_text
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 6, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": (tj.bot_job_id if tj else f"diar-{meeting_id_str}"),
            "params": _json.dumps(diar_params),
        },
    )
    await db.commit()
    logger.info(f"Meeting {meeting.id}: diarization job queued (operation_type=6) — T7 follows after T6")


async def _handle_fast_p5_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle fast_p5_complete webhook (operation type 7 done).

    Fast-email speaker-attribution change (2026-05-13): Type 6 has already
    run BEFORE Type 7 in the new order. So this handler now queues Type 5
    (polish) directly. Diarization paths are inherited from T7's params
    (originally set by _handle_diarization_complete).
    """
    import json as _json
    import uuid as _uuid

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    tj_id = event_data.get("transcription_job_id")
    meeting_id_str = str(meeting.id)

    tj = None
    tj_params = {}
    tj_result = {}
    if tj_id:
        result = await db.execute(
            select(TranscriptionJob).where(TranscriptionJob.id == _uuid.UUID(tj_id))
        )
        tj = result.scalar_one_or_none()
        if tj:
            tj_params = tj.params if isinstance(tj.params, dict) else {}
            tj_result = (tj.result if tj and tj.result else {}) or {}

    # Tier 4.0.c — persist Type 7's prompt_log (fast P5 LLM call cost)
    type7_prompt_log = tj_result.get("prompt_log") or []
    if type7_prompt_log:
        try:
            from app.services.intelligence_store import store_all_costs
            recording_source = tj_params.get("recording_source") or "bot"
            await store_all_costs(
                db, meeting,
                {"prompt_log": type7_prompt_log},
                operation_type="fast_p5",
                recording_source=recording_source,
            )
            await db.commit()
            logger.info(
                f"Meeting {meeting.id}: persisted {len(type7_prompt_log)} Type 7 prompt_log entries"
            )
        except Exception as e:
            logger.warning(f"Meeting {meeting.id}: Type 7 prompt_log persistence failed (non-fatal): {e}")

    preprocess_path = tj_params.get("preprocess_path", f"meetings/{meeting_id_str}/preprocess/")
    enhance_path = tj_params.get("enhance_path", f"meetings/{meeting_id_str}/enhance/")
    diarization_path = tj_params.get("diarization_path", f"meetings/{meeting_id_str}/diarization/")
    output_base_path = tj_params.get("output_base_path", f"meetings/{meeting_id_str}/v1/")

    # ── T6-failure recovery hop ──
    # If this T7 was the best-effort run after a T6 failure
    # (params.was_diarization_failed=True), don't queue T5 yet — queue the
    # T6 retry first. _handle_diarization_complete will queue T5 directly
    # when it sees params.is_t6_retry=True on the retry job.
    if tj_params.get("was_diarization_failed", False):
        retry_params = tj_params.get("t6_retry_params") or {}
        if not retry_params:
            # Defensive: if t6_retry_params somehow wasn't carried, give up
            # cleanly — better to halt than to start T5 with no diarization.
            logger.warning(
                f"Meeting {meeting.id}: T7-after-diar-fail completed but "
                f"t6_retry_params missing — cannot retry T6, marking fast_email_only"
            )
            meeting.status = "fast_email_only"
            meeting.intelligence_status = "failed"
            if not meeting.ended_at:
                meeting.ended_at = datetime.now(timezone.utc)
            await db.commit()
            return

        bjid = tj.bot_job_id if tj else f"diar-retry-{meeting_id_str}"
        from sqlalchemy import text as sa_text
        await db.execute(
            sa_text(
                "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
                "VALUES (:mid, :bjid, 6, 'waiting', :params)"
            ),
            {
                "mid": meeting_id_str,
                "bjid": bjid,
                "params": _json.dumps(retry_params),
            },
        )
        await db.commit()
        logger.info(
            f"Meeting {meeting.id}: T7 (best-effort) done after diar fail — "
            f"queued T6 retry (operation_type=6, is_t6_retry=true)"
        )
        return

    # Look up type-1 job for original ASR-relevant params (mirrors the previous
    # logic in _handle_diarization_complete, which previously queued Type 5).
    original_tj_result = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.operation_type == 1,
        ).order_by(TranscriptionJob.created_at.desc()).limit(1)
    )
    original_tj = original_tj_result.scalar_one_or_none()
    original_params = {}
    if original_tj:
        original_params = original_tj.params if isinstance(original_tj.params, dict) else {}

    # Queue operation type 5 (polish: alignment + speakers + LLM corr + polished P5)
    asr_params = {
        "preprocess_path": preprocess_path,
        "enhance_path": enhance_path,
        "diarization_path": diarization_path,
        "output_base_path": output_base_path,
        "team_id": str(meeting.team_id),
        "input_audio_path": tj_params.get("input_audio_path", original_params.get("input_audio_path", "")),
        "meeting_title": tj_params.get("meeting_title", original_params.get("meeting_title")),
        "participant_names": tj_params.get("participant_names", original_params.get("participant_names", [])),
        "language": tj_params.get("language", original_params.get("language")),
        "engine": tj_params.get("engine", original_params.get("engine")),
        "model_size": tj_params.get("model_size", original_params.get("model_size")),
        "phone_mode": tj_params.get("phone_mode", original_params.get("phone_mode", True)),
        "diarization": original_params.get("diarization", False),
        "vocab_correction": original_params.get("vocab_correction", False),
        "vocabulary": original_params.get("vocabulary", ""),
        "llm_correction": original_params.get("llm_correction", False),
        "recording_source": tj_params.get("recording_source", original_params.get("recording_source", "bot")),
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
        # Tier 1.4 — propagate CRM contacts from Type 7's params
        "crm_contacts": tj_params.get("crm_contacts", []),
        "crm_contacts_fetched": tj_params.get("crm_contacts_fetched", False),
        # Tier 2.1 — propagate enhance_skipped + audit signals
        "enhance_skipped": tj_params.get("enhance_skipped", False),
        "tier_2_1_signals": tj_params.get("tier_2_1_signals", {}),
    }

    from sqlalchemy import text as sa_text
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 5, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": (original_tj.bot_job_id if original_tj else f"asr-{meeting_id_str}"),
            "params": _json.dumps(asr_params),
        },
    )
    await db.commit()
    logger.info(f"Meeting {meeting.id}: ASR+postprocess job queued (operation_type=5) — direct from T7")


async def _handle_diarization_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle diarization_complete webhook (operation type 6 done).

    Fast-email speaker-attribution change (2026-05-13): T6 now runs BEFORE T7
    so the fast email can show real speaker names. This handler queues T7
    (fast ASR + fast P5 + email) with diarization_path set so T7 can pull
    pyannote turns + speaker_events.json to name segments before the fast
    P5 prompt is built.
    """
    import json as _json
    import uuid as _uuid

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    tj_id = event_data.get("transcription_job_id")
    meeting_id_str = str(meeting.id)

    tj = None
    tj_params = {}
    tj_result = {}
    if tj_id:
        result = await db.execute(
            select(TranscriptionJob).where(TranscriptionJob.id == _uuid.UUID(tj_id))
        )
        tj = result.scalar_one_or_none()
        if tj:
            tj_params = tj.params if isinstance(tj.params, dict) else {}
            tj_result = (tj.result if tj and tj.result else {}) or {}

    diarization_path = tj_result.get("diarization_path", f"meetings/{meeting_id_str}/diarization/")
    preprocess_path = tj_params.get("preprocess_path", f"meetings/{meeting_id_str}/preprocess/")
    enhance_path = tj_params.get("enhance_path", f"meetings/{meeting_id_str}/enhance/")
    output_base_path = tj_params.get("output_base_path", f"meetings/{meeting_id_str}/v1/")

    # Look up type-1 job to backfill any ASR-relevant params not on T6's row.
    original_tj_result = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.operation_type == 1,
        ).order_by(TranscriptionJob.created_at.desc()).limit(1)
    )
    original_tj = original_tj_result.scalar_one_or_none()
    original_params = {}
    if original_tj and original_tj.params:
        original_params = original_tj.params if isinstance(original_tj.params, dict) else {}

    # ── T6-failure recovery hop ──
    # If this T6 is the RETRY after a first-attempt failure
    # (params.is_t6_retry=True), the best-effort T7 has already run — queue
    # T5 directly (with this retry's diarization_path) instead of queuing
    # another T7. Without this branch we'd run T7 twice on the recovery path.
    if tj_params.get("is_t6_retry", False):
        from sqlalchemy import text as sa_text
        asr_params = {
            "preprocess_path": preprocess_path,
            "enhance_path": enhance_path,
            "diarization_path": diarization_path,  # NEW from this retry
            "output_base_path": output_base_path,
            "team_id": str(meeting.team_id),
            "input_audio_path": tj_params.get(
                "input_audio_path", original_params.get("input_audio_path", "")
            ),
            "meeting_title": tj_params.get(
                "meeting_title", original_params.get("meeting_title")
            ),
            "participant_names": tj_params.get(
                "participant_names",
                original_params.get("participant_names", []),
            ),
            "language": tj_params.get("language", original_params.get("language")),
            "engine": tj_params.get("engine", original_params.get("engine")),
            "model_size": tj_params.get(
                "model_size", original_params.get("model_size")
            ),
            "phone_mode": tj_params.get(
                "phone_mode", original_params.get("phone_mode", True)
            ),
            "diarization": original_params.get("diarization", False),
            "vocab_correction": original_params.get("vocab_correction", False),
            "vocabulary": original_params.get("vocabulary", ""),
            "llm_correction": original_params.get("llm_correction", False),
            "recording_source": tj_params.get(
                "recording_source",
                original_params.get("recording_source", "bot"),
            ),
            "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
            "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            "max_retries": 2,
            "crm_contacts": tj_params.get("crm_contacts", []),
            "crm_contacts_fetched": tj_params.get("crm_contacts_fetched", False),
            "enhance_skipped": tj_params.get("enhance_skipped", False),
            "tier_2_1_signals": tj_params.get("tier_2_1_signals", {}),
        }
        await db.execute(
            sa_text(
                "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
                "VALUES (:mid, :bjid, 5, 'waiting', :params)"
            ),
            {
                "mid": meeting_id_str,
                "bjid": (original_tj.bot_job_id if original_tj else f"asr-{meeting_id_str}"),
                "params": _json.dumps(asr_params),
            },
        )
        await db.commit()
        logger.info(
            f"Meeting {meeting.id}: T6 retry succeeded — queuing T5 directly "
            f"(operation_type=5, skipping T7 since best-effort T7 already ran)"
        )
        return

    # Queue operation type 7 (fast ASR + fast P5 + email)
    fast_params = {
        "preprocess_path": preprocess_path,
        "enhance_path": enhance_path,
        "diarization_path": diarization_path,
        "output_base_path": output_base_path,
        "team_id": str(meeting.team_id),
        # input_audio_path is the directory under which speaker_events.json
        # lives — T7 reads it from {input_audio_path}/speaker_events.json to
        # map SPEAKER_XX to real names.
        "input_audio_path": tj_params.get("input_audio_path", original_params.get("input_audio_path", "")),
        "meeting_title": tj_params.get("meeting_title", original_params.get("meeting_title")),
        "participant_names": tj_params.get("participant_names", original_params.get("participant_names", [])),
        "language": tj_params.get("language", original_params.get("language")),
        "engine": tj_params.get("engine", original_params.get("engine")),
        "model_size": tj_params.get("model_size", original_params.get("model_size")),
        "phone_mode": tj_params.get("phone_mode", original_params.get("phone_mode", True)),
        "recording_source": tj_params.get("recording_source", original_params.get("recording_source", "bot")),
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
        # Tier 1.4 — propagate CRM contacts from Type 6's params
        "crm_contacts": tj_params.get("crm_contacts", []),
        "crm_contacts_fetched": tj_params.get("crm_contacts_fetched", False),
        # Tier 2.1 — propagate enhance_skipped + audit signals
        "enhance_skipped": tj_params.get("enhance_skipped", False),
        "tier_2_1_signals": tj_params.get("tier_2_1_signals", {}),
    }

    from sqlalchemy import text as sa_text
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 7, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": (tj.bot_job_id if tj else f"fast-{meeting_id_str}"),
            "params": _json.dumps(fast_params),
        },
    )
    await db.commit()
    logger.info(f"Meeting {meeting.id}: fast ASR+P5 job queued (operation_type=7) — direct from T6")


async def _handle_transcription_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle transcription_complete webhook from TranscriptionBase.

    TranscriptionBase has already saved all result files to main storage
    and updated the transcription_jobs row. This handler reads the result
    paths from the job row, loads transcript text from storage, and creates
    the Transcription + MemoirSummary records with the correct version.
    """
    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    meeting_id_str = str(meeting.id)
    transcription_job_id = event_data.get("transcription_job_id")

    # Look up the transcription job for result paths
    tj = None
    if transcription_job_id:
        import uuid as _uuid
        result = await db.execute(
            select(TranscriptionJob).where(
                TranscriptionJob.id == _uuid.UUID(transcription_job_id)
            )
        )
        tj = result.scalar_one_or_none()

    # Read result data from the transcription job row
    tj_result = (tj.result if tj and tj.result else {}) or {}
    tj_params = {}
    if tj and tj.params:
        tj_params = tj.params if isinstance(tj.params, dict) else {}

    word_count = tj_result.get("word_count", 0)
    speaker_count = tj_result.get("speaker_count", 0)
    language = tj_result.get("language", "en")
    segments_path = tj_result.get("segments_path", f"meetings/{meeting_id_str}/transcript_segments.json")
    text_path = tj_result.get("transcript_path", f"meetings/{meeting_id_str}/transcript.txt")
    # Tier 1.5 — prefer Type 1's recording_path (already persisted to
    # meeting.recording_path by _handle_preprocess_complete) over the
    # hardcoded .wav fallback. Type 5 no longer returns recording_path
    # (it doesn't know the source extension), so this branch normally
    # uses meeting.recording_path. The .wav literal remains only for
    # edge cases where neither Type 1 nor the meeting row carry a value
    # (e.g., legacy or partially-rebuilt jobs).
    recording_path = (
        tj_result.get("recording_path")
        or meeting.recording_path
        or f"meetings/{meeting_id_str}/recording.wav"
    )

    # Determine version from output_base_path or from existing records
    output_base_path = tj_params.get("output_base_path", "")
    version = _parse_version_from_path(output_base_path)
    if version is None:
        # Legacy path or first transcription — determine from existing records
        max_ver_result = await db.execute(
            select(func.max(Transcription.version)).where(
                Transcription.meeting_id == meeting.id
            )
        )
        max_version = max_ver_result.scalar() or 0
        version = max_version + 1

    # Read full transcript text from storage for search indexing
    storage = create_storage()
    full_text = ""
    try:
        full_text = await storage.read_text(text_path)
    except Exception as e:
        logger.warning(f"Could not read transcript text from {text_path}: {e}")

    # Read segments for speaker count validation
    segments = []
    try:
        segments = await storage.read_json(segments_path)
        if segments and not speaker_count:
            speaker_ids = {s.get("speaker") for s in segments if isinstance(s, dict) and s.get("speaker")}
            speaker_count = len(speaker_ids)
    except Exception:
        pass

    # Recompute word_count from text if not provided
    if not word_count and full_text:
        word_count = len(full_text.split())

    # Analysis path — read from job result or derive from segments path
    analysis_path = tj_result.get("analysis_path")
    if not analysis_path:
        base_dir = segments_path.rsplit("/", 1)[0]
        analysis_path = f"{base_dir}/analysis.json"

    # Read analysis data (coaching metrics, intelligence/memoir results)
    analysis_data = {}
    try:
        if await storage.exists(analysis_path):
            analysis_data = await storage.read_json(analysis_path)
        else:
            await storage.save_json(analysis_path, {})
    except Exception:
        pass

    # Read summary.json from P5-only run (fast-path delivery)
    summary_path = tj_result.get("summary_path")
    summary_data = {}
    if summary_path:
        try:
            if await storage.exists(summary_path):
                raw_summary = await storage.read_json(summary_path)
                summary_data = raw_summary.get("summary", {}) if isinstance(raw_summary, dict) else {}
                logger.info(f"Meeting {meeting.id}: loaded summary.json")
        except Exception as e:
            logger.warning(f"Could not read summary.json from {summary_path}: {e}")

    # Fallback: read intelligence.json if summary.json not available (legacy jobs)
    intelligence_path = tj_result.get("intelligence_path")
    intelligence_data = {}
    if intelligence_path:
        try:
            if await storage.exists(intelligence_path):
                intelligence_data = await storage.read_json(intelligence_path)
                if not summary_data:
                    summary_data = intelligence_data.get("summary", {})
                logger.info(f"Meeting {meeting.id}: loaded intelligence.json ({intelligence_data.get('call_type', 'unknown')})")
        except Exception as e:
            logger.warning(f"Could not read intelligence.json from {intelligence_path}: {e}")

    # Extract confidence score from intelligence results
    overall_confidence = None
    intelligence = analysis_data.get("intelligence", {})
    if isinstance(intelligence, dict):
        overall_confidence = intelligence.get("overall_confidence_score")

    # Get recording source from job params (bot, zoom_cloud, upload)
    recording_source = tj_params.get("recording_source", "bot")

    # --- Update meeting record ---
    meeting.recording_path = recording_path
    # NOTE: Do NOT touch meeting.original_recording_path — it was set at creation time
    meeting.transcript_path = segments_path
    meeting.analysis_path = analysis_path
    if intelligence_path:
        meeting.intelligence_path = intelligence_path
    meeting.status = "completed"
    meeting.intelligence_status = "processing"
    if not meeting.ended_at:
        meeting.ended_at = datetime.now(timezone.utc)
    if meeting.started_at and meeting.ended_at:
        meeting.duration_seconds = int(
            (meeting.ended_at - meeting.started_at).total_seconds()
        )
    if not meeting.duration_seconds and segments:
        last_end = max((s.get("end", 0) for s in segments), default=0)
        if last_end > 0:
            meeting.duration_seconds = int(last_end)

    # Create Transcription record with version and tsvector search index
    transcription = Transcription(
        meeting_id=meeting.id,
        version=version,
        recording_source=recording_source,
        speaker_count=speaker_count,
        word_count=word_count,
        language=language,
        segments_path=segments_path,
        full_text_path=text_path,
        search_vector=func.to_tsvector("english", full_text) if full_text else None,
    )
    db.add(transcription)

    # Extract denormalized fields for list-view display
    summary_excerpt = None
    primary_participant_name = None
    primary_participant_company = None

    # Try intelligence data first, then P5 summary
    intel = intelligence_data or intelligence
    if isinstance(intel, dict) and intel.get("narrative_summary"):
        narrative = intel["narrative_summary"]
        if len(narrative) > 150:
            summary_excerpt = narrative[:150].rsplit(" ", 1)[0] + "..."
        else:
            summary_excerpt = narrative

        call_meta = intel.get("call_metadata") or {}
        if isinstance(call_meta, dict):
            for p in (call_meta.get("participants") or []):
                if isinstance(p, dict) and p.get("role") not in ("rep", "representative", "internal"):
                    primary_participant_name = p.get("name") or None
                    primary_participant_company = p.get("company") or None
                    break

    # Fallback to P5 summary for list-view fields
    if not summary_excerpt and summary_data:
        p5_inner = summary_data.get("summary", summary_data) if isinstance(summary_data, dict) else {}
        tldr = p5_inner.get("tldr") or ""
        if tldr:
            if len(tldr) > 150:
                summary_excerpt = tldr[:150].rsplit(" ", 1)[0] + "..."
            else:
                summary_excerpt = tldr

        if not primary_participant_name:
            p5_meeting = summary_data.get("meeting", {}) if isinstance(summary_data, dict) else {}
            for p in (p5_meeting.get("participants") or []):
                if isinstance(p, dict) and p.get("is_recorder") is not True:
                    primary_participant_name = p.get("name") or None
                    break

    # Create MemoirSummary with matching version and confidence from analysis
    memoir_summary = MemoirSummary(
        meeting_id=meeting.id,
        version=version,
        overall_confidence_score=overall_confidence,
        crm_push_status=None,
        field_changes_path=analysis_path,
        summary_excerpt=summary_excerpt,
        primary_participant_name=primary_participant_name,
        primary_participant_company=primary_participant_company,
    )
    db.add(memoir_summary)

    await db.commit()
    logger.info(
        f"Meeting {meeting.id} status -> completed "
        f"(v{version}, transcript: {word_count} words, {speaker_count} speakers)"
    )

    # Extract detected introductions from LLM analysis (for speaker mapping)
    detected_introductions = None
    if isinstance(intelligence, dict):
        detected_introductions = intelligence.get("detected_introductions")

    # Load speaker name map from active-speaker overlap analysis (best-effort)
    speaker_name_map = None
    try:
        base_dir = segments_path.rsplit("/", 1)[0]
        name_map_rel_path = f"{base_dir}/speaker_name_map.json"
        if await storage.exists(name_map_rel_path):
            speaker_name_map = await storage.read_json(name_map_rel_path)
            logger.info(f"Meeting {meeting.id}: loaded speaker_name_map ({len(speaker_name_map)} entries)")
    except Exception as e:
        logger.debug(f"Meeting {meeting.id}: speaker_name_map not available: {e}")

    # Load voice embeddings (needed for both matching and storage)
    base_dir = segments_path.rsplit("/", 1)[0]
    embeddings_rel_path = f"{base_dir}/embeddings.json"
    embeddings_data = None
    try:
        if await storage.exists(embeddings_rel_path):
            embeddings_data = await storage.read_json(embeddings_rel_path)
    except Exception:
        pass

    # Build LLM-inferred name -> label mapping from P5 summary (for speaker mapping fallback)
    label_keys = list({s.get("speaker", "") for s in segments if s.get("speaker")})
    llm_inferred_names = _build_llm_inferred_names(summary_data, speaker_name_map, label_keys)

    # Run speaker mapping pipeline (best-effort)
    speaker_map = None
    try:
        from app.services.speaker_mapping import run_speaker_mapping

        speaker_map = await run_speaker_mapping(
            db, meeting, transcription, segments,
            detected_introductions=detected_introductions,
            speaker_name_map=speaker_name_map,
            embeddings_data=embeddings_data,
            llm_inferred_names=llm_inferred_names,
        )
        if speaker_map:
            await db.commit()
            logger.info(f"Meeting {meeting.id}: speaker mapping completed ({len(speaker_map)} mapped)")

            # Update segments with resolved speaker names and save back to storage
            updated = 0
            for seg in segments:
                label = seg.get("speaker", "")
                if label in speaker_map:
                    seg["speaker"] = speaker_map[label]["name"]
                    updated += 1
            if updated > 0:
                await storage.save_json(segments_path, segments)
                logger.info(f"Meeting {meeting.id}: updated {updated} segments with speaker names")
    except Exception as e:
        logger.error(f"Speaker mapping failed for meeting {meeting.id}: {e}")

    # Replace LLM-inferred short names with voice embedding full names in summary.json
    if speaker_map and summary_path:
        try:
            replacement_map = _build_name_replacement_map(
                speaker_name_map, speaker_map
            )
            if replacement_map:
                raw = await storage.read_json(summary_path)
                raw = _replace_names_in_obj(raw, replacement_map)
                await storage.save_json(summary_path, raw)
                summary_data = raw.get("summary", {}) if isinstance(raw, dict) else {}
                logger.info(
                    f"Meeting {meeting.id}: replaced {len(replacement_map)} names in summary.json: "
                    f"{replacement_map}"
                )
        except Exception as e:
            logger.warning(f"Summary name replacement failed for meeting {meeting.id}: {e}")

    # Enrich summary action items with owner emails from speaker profiles
    if summary_data and speaker_map and summary_path:
        try:
            # P1.5 (2026-05-31): batched SpeakerProfile lookup. The loop
            # over speaker_map.items() previously fired one SELECT per
            # diarization label (6-8 round-trips per meeting at ~500 ms
            # each under load). Same N+1 we fixed today on the GET
            # /speakers read path — this is the write-path equivalent
            # that was missed in that sweep. See plan/performance-
            # optimization-audit.md §1.5.
            from app.models.speaker import SpeakerProfile

            profile_ids: list[uuid.UUID] = []
            for mapping in speaker_map.values():
                pid = mapping.get("profile_id")
                if pid:
                    try:
                        profile_ids.append(uuid.UUID(pid))
                    except (ValueError, TypeError):
                        continue

            profiles_by_id: dict[uuid.UUID, SpeakerProfile] = {}
            if profile_ids:
                prof_result = await db.execute(
                    select(SpeakerProfile).where(SpeakerProfile.id.in_(profile_ids))
                )
                profiles_by_id = {p.id: p for p in prof_result.scalars().all()}

            # Build name → email lookup from mapped speaker profiles
            name_to_email: dict[str, str] = {}
            for label, mapping in speaker_map.items():
                profile_id = mapping.get("profile_id")
                if profile_id:
                    try:
                        pid_uuid = uuid.UUID(profile_id)
                    except (ValueError, TypeError):
                        continue
                    profile = profiles_by_id.get(pid_uuid)
                    if profile and profile.email:
                        name_to_email[mapping["name"].lower()] = profile.email

            if name_to_email:
                # Load raw summary, update action items, save back
                raw_summary = await storage.read_json(summary_path)
                inner = raw_summary.get("summary", {}) if isinstance(raw_summary, dict) else {}
                inner_summary = inner.get("summary", inner) if isinstance(inner, dict) else {}
                action_items = inner_summary.get("action_items", [])

                enriched = 0
                for item in action_items:
                    owner = (item.get("owner") or "").lower()
                    if owner in name_to_email and not item.get("owner_email"):
                        item["owner_email"] = name_to_email[owner]
                        enriched += 1

                if enriched:
                    await storage.save_json(summary_path, raw_summary)
                    # Reload summary_data for email dispatch
                    summary_data = raw_summary.get("summary", {}) if isinstance(raw_summary, dict) else {}
                    logger.info(f"Meeting {meeting.id}: enriched {enriched} action items with owner emails")
        except Exception as e:
            logger.warning(f"Meeting {meeting.id}: action item email enrichment failed (non-fatal): {e}")

    # Store voice embeddings for mapped speakers (best-effort)
    if speaker_map and embeddings_data:
        try:
            from app.services.voice_embeddings import store_embeddings_for_speakers

            emb_results = await store_embeddings_for_speakers(
                db, meeting.id, speaker_map, embeddings_data
            )
            if emb_results:
                await db.commit()
                logger.info(f"Meeting {meeting.id}: voice embeddings stored for {len(emb_results)} speakers")
        except Exception as e:
            logger.error(f"Voice embedding storage failed for meeting {meeting.id}: {e}")

    # Queue intelligence pipeline as a separate job (operation_type=3)
    try:
        output_base_path = tj_params.get("output_base_path", f"meetings/{meeting_id_str}/v1/")
        participant_names = []
        if speaker_map:
            participant_names = [
                v["name"] for v in speaker_map.values()
                if isinstance(v, dict) and v.get("name")
            ]

        import json as _json
        intel_params = {
            "segments_path": segments_path,
            "speaker_map": speaker_map or {},
            "participant_names": participant_names,
            "team_id": str(meeting.team_id),
            "call_date": (meeting.started_at or meeting.created_at).strftime("%Y-%m-%d") if (meeting.started_at or meeting.created_at) else None,
            "output_base_path": output_base_path,
            "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
            "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            "max_retries": 2,
            "auto_chain": True,
            "recording_source": recording_source,
            "version": version,
        }
        from sqlalchemy import text as sa_text
        await db.execute(
            sa_text(
                "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
                "VALUES (:mid, :bjid, 3, 'waiting', :params)"
            ),
            {
                "mid": meeting_id_str,
                "bjid": f"intel-{meeting_id_str}",
                "params": _json.dumps(intel_params),
            },
        )
        await db.commit()
        logger.info(f"Meeting {meeting.id}: intelligence job queued (operation_type=3)")
    except Exception as e:
        logger.error(f"Failed to queue intelligence job for meeting {meeting.id}: {e}")
        meeting.intelligence_status = "failed"
        await db.commit()

    # Publish meeting_completed event via Redis
    try:
        publisher = RedisEventPublisher(settings.REDIS_URL)
        await publisher.publish(
            "meeting_completed",
            {
                "meeting_id": meeting_id_str,
                "team_id": str(meeting.team_id),
                "created_by": str(meeting.created_by),
                "title": meeting.title,
                "duration_seconds": meeting.duration_seconds,
                "speaker_count": speaker_count,
                "word_count": word_count,
                "overall_confidence": overall_confidence,
            },
        )
        await publisher.close()
    except Exception as e:
        logger.error(f"Failed to publish meeting_completed event: {e}")

    # summary_data already loaded from summary.json (or fallback from intelligence.json) above

    # Store summary_json on the MemoirSummary record
    try:
        if summary_data and memoir_summary:
            memoir_summary.summary_json = summary_data
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to store summary_json for meeting {meeting.id}: {e}")

    # Dispatch notification (in-app + email + slack based on preferences).
    # Email send is gated by an atomic claim on meetings.summary_email_sent_at
    # — if the fast-P5 path already sent (or another concurrent polished
    # webhook is mid-send), the claim returns False and we skip the email
    # block but still fire the in-app + Slack notifications.
    try:
        from app.api.meetings_internal import (
            _claim_summary_email_gate,
            _compute_delivery_minutes,
            _get_type1_started_at,
            _release_summary_email_gate,
        )
        from app.clients.auth_client import batch_get_users
        from app.clients.notification_client import send_meeting_summary_email
        from app.services.notification_dispatcher import dispatch_notification

        created_by_str = str(meeting.created_by)
        users = await batch_get_users([created_by_str])
        owner = users.get(created_by_str)

        if owner and owner.get("email"):
            call_url = f"{settings.FRONTEND_URL}/meetings/{meeting_id_str}"

            if not summary_data:
                logger.info(f"Meeting {meeting.id}: skipping email — no P5 summary (transcript too short)")
            else:
                claimed = await _claim_summary_email_gate(db, meeting.id)
                if not claimed:
                    logger.info(
                        f"Meeting {meeting.id}: skipping polished email — gate already "
                        "claimed (fast-P5 path or concurrent webhook)"
                    )
                else:
                    started_at = await _get_type1_started_at(db, meeting.id)
                    delivery_minutes = _compute_delivery_minutes(started_at)

                    recorder_sent_ok = False
                    try:
                        recorder_sent_ok = await send_meeting_summary_email(
                            to_email=owner["email"],
                            recipient_type="recorder",
                            recipient_name=owner.get("name") or "there",
                            meeting_id=meeting_id_str,
                            meeting_title=meeting.title or "Untitled Call",
                            meeting_date=(meeting.started_at or meeting.created_at).isoformat() if (meeting.started_at or meeting.created_at) else None,
                            duration_seconds=meeting.duration_seconds,
                            platform=_email_platform_label(meeting),
                            summary_data=summary_data,
                            created_by=created_by_str,
                            owner_name=owner.get("name"),
                            recording_source=recording_source,
                            meeting_ended_at=meeting.ended_at.isoformat() if meeting.ended_at else (meeting.started_at or meeting.created_at).isoformat() if (meeting.started_at or meeting.created_at) else None,
                            meeting_created_at=meeting.created_at.isoformat() if meeting.created_at else None,
                            unsubscribe_url=f"{settings.FRONTEND_URL}/settings/notifications",
                            delivery_minutes=delivery_minutes,
                        )
                    finally:
                        if not recorder_sent_ok:
                            await _release_summary_email_gate(db, meeting.id)
                            logger.error(
                                f"Meeting {meeting.id}: polished recorder email failed; "
                                "gate released for next attempt"
                            )

                    # Participant fan-out only when the recorder send succeeded.
                    if recorder_sent_ok:
                        try:
                            await _send_participant_emails(
                                db, meeting, summary_data, memoir_summary, settings,
                                delivery_minutes=delivery_minutes,
                            )
                        except Exception as e:
                            logger.error(f"Failed to send participant emails for meeting {meeting.id}: {e}")

            # In-app notification + Slack — always fire (different surfaces).
            await dispatch_notification(
                db=db,
                event_type="transcription_complete",
                user_id=meeting.created_by,
                team_id=meeting.team_id,
                title=f"Call processed: {meeting.title or 'Untitled Call'}",
                body="Transcript and AI analysis are ready.",
                link=f"/meetings/{meeting_id_str}",
                slack_text=f"*{meeting.title or 'Untitled Call'}* is ready — <{call_url}|View call>",
            )
            await db.commit()
        else:
            logger.warning(f"Could not dispatch notification: owner not found for {created_by_str}")
    except Exception as e:
        logger.error(f"Failed to dispatch transcription_complete notification: {e}")

    # Tier 4.0.b — persist per-call LLM cost data from Type 5
    # (LLMCorrector, PunctuationRefiner, polished P5). Forward the
    # prompt_log emitted in result_dict to the existing AiPromptLog path.
    type5_prompt_log = tj_result.get("prompt_log") or []
    if type5_prompt_log:
        try:
            from app.services.intelligence_store import store_all_costs
            await store_all_costs(
                db, meeting,
                {"prompt_log": type5_prompt_log},
                operation_type="transcription",
                recording_source=recording_source,
            )
            await db.commit()
            logger.info(
                f"Meeting {meeting.id}: persisted {len(type5_prompt_log)} Type 5 prompt_log entries"
            )
        except Exception as e:
            logger.warning(f"Meeting {meeting.id}: Type 5 prompt_log persistence failed (non-fatal): {e}")

    # Clean up local recording files on EC2 (already uploaded to R2)
    if settings.STORAGE_BACKEND == "s3":
        try:
            import shutil
            from pathlib import Path

            recordings_dir = Path(settings.BOT_RECORDINGS_DIR).resolve()
            data_dir = Path(settings.DATA_DIR).resolve()

            # 1. Clean recording source dir (bot: {jobId}/, upload: uploads/{id}/, zoom_cloud/{id}/)
            input_audio_path = tj_params.get("input_audio_path", "")
            if input_audio_path:
                audio_parent = recordings_dir / input_audio_path
                # Go up to the meeting/job directory (e.g. uploads/{id}/ or {jobId}/)
                source_dir = audio_parent.parent
                if source_dir != recordings_dir and source_dir.exists():
                    shutil.rmtree(str(source_dir), ignore_errors=True)

            # 2. Clean output results dir (DATA_DIR/meetings/{id}/v1/)
            output_base = tj_params.get("output_base_path", "")
            if output_base:
                output_dir = data_dir / output_base
                if output_dir.exists():
                    shutil.rmtree(str(output_dir), ignore_errors=True)

            # 3. Clean meeting-level recording.wav (DATA_DIR/meetings/{id}/recording.wav)
            if recording_path:
                rec_file = data_dir / recording_path
                if rec_file.exists():
                    rec_file.unlink(missing_ok=True)
                # Remove empty parent dir
                try:
                    rec_file.parent.rmdir()
                except OSError:
                    pass

            logger.info(f"Meeting {meeting.id}: cleaned up local files after R2 processing")
        except Exception as e:
            logger.warning(f"Meeting {meeting.id}: local cleanup failed (non-fatal): {e}")


async def _send_participant_emails(
    db: AsyncSession,
    meeting: Meeting,
    summary_data: dict,
    memoir_summary: MemoirSummary | None,
    settings_obj,
    delivery_minutes: int | None = None,
) -> None:
    """Send meeting summary emails to internal and external participants.

    Internal participants get an email if their notification preferences allow it.
    External participants only get emails if external_share_approved is True and
    they are not in the EmailSuppression table.
    """
    import secrets as _secrets

    from sqlalchemy.orm import selectinload

    from app.clients.notification_client import send_meeting_summary_email
    from app.models.notification import (
        EmailSuppression,
        EmailUnsubscribeToken,
        NotificationPreference,
    )
    from app.models.team import Team
    from app.services.speaker_mapping import is_internal_participant

    meeting_id_str = str(meeting.id)

    # Load team for domain matching
    team_result = await db.execute(select(Team).where(Team.id == meeting.team_id))
    team = team_result.scalar_one_or_none()
    if not team:
        logger.warning(f"Team not found for meeting {meeting.id}, skipping participant emails")
        return

    # Query participants with eager-loaded speaker_profile
    participants_result = await db.execute(
        select(MeetingParticipant)
        .where(MeetingParticipant.meeting_id == meeting.id)
        .options(selectinload(MeetingParticipant.speaker_profile))
    )
    participants = participants_result.scalars().all()

    if not participants:
        return

    # Determine platform — source-aware label (handles NULL URL + web_recorder)
    platform = _email_platform_label(meeting)

    meeting_date = meeting.started_at.isoformat() if meeting.started_at else None
    created_by_str = str(meeting.created_by)
    sent_count = 0

    for participant in participants:
        try:
            sp = participant.speaker_profile
            if not sp or not sp.email:
                continue

            # Skip the recorder — they already got the recorder email
            if sp.internal_user_id and str(sp.internal_user_id) == created_by_str:
                continue

            email = sp.email
            name = sp.display_name or email.split("@")[0]

            if is_internal_participant(sp, team):
                # Internal participant — check notification preferences
                pref_result = await db.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.user_id == sp.internal_user_id,
                        NotificationPreference.team_id == meeting.team_id,
                        NotificationPreference.event_type == "participant_summary",
                        NotificationPreference.channel == "email",
                    )
                )
                pref = pref_result.scalar_one_or_none()
                # Default to enabled if no preference exists
                if pref and not pref.enabled:
                    continue

                ok = await send_meeting_summary_email(
                    to_email=email,
                    recipient_type="internal",
                    recipient_name=name,
                    meeting_id=meeting_id_str,
                    meeting_title=meeting.title or "Untitled Call",
                    meeting_date=meeting_date,
                    duration_seconds=meeting.duration_seconds,
                    platform=platform,
                    summary_data=summary_data,
                    created_by=created_by_str,
                    recipient_email=email,
                    unsubscribe_url=f"{settings_obj.FRONTEND_URL}/settings/notifications",
                    delivery_minutes=delivery_minutes,
                )
                if ok:
                    sent_count += 1
                else:
                    logger.warning(
                        f"Meeting {meeting.id}: internal participant email to "
                        f"{email} not accepted by notification service"
                    )

            else:
                # External participant — requires approval + not suppressed
                if not memoir_summary or not memoir_summary.external_share_approved:
                    continue

                # Check suppression list
                suppression_result = await db.execute(
                    select(EmailSuppression.id).where(
                        EmailSuppression.email == email.lower(),
                        EmailSuppression.team_id == meeting.team_id,
                    ).limit(1)
                )
                if suppression_result.scalar_one_or_none():
                    continue

                # Generate unsubscribe token
                token = _secrets.token_urlsafe(32)
                unsub_token = EmailUnsubscribeToken(
                    token=token,
                    email=email.lower(),
                    team_id=meeting.team_id,
                    event_type="external_summary",
                )
                db.add(unsub_token)
                await db.flush()

                unsubscribe_url = f"{settings_obj.FRONTEND_URL}/unsubscribe?token={token}"

                ok = await send_meeting_summary_email(
                    to_email=email,
                    recipient_type="external",
                    recipient_name=name,
                    meeting_id=meeting_id_str,
                    meeting_title=meeting.title or "Untitled Call",
                    meeting_date=meeting_date,
                    duration_seconds=meeting.duration_seconds,
                    platform=platform,
                    summary_data=summary_data,
                    created_by=created_by_str,
                    recipient_email=email,
                    unsubscribe_url=unsubscribe_url,
                    team_id=str(meeting.team_id),
                    delivery_minutes=delivery_minutes,
                )
                if ok:
                    sent_count += 1
                else:
                    logger.warning(
                        f"Meeting {meeting.id}: external participant email to "
                        f"{email} not accepted by notification service"
                    )

        except Exception as e:
            logger.error(f"Failed to send participant email to {participant.id}: {e}")
            continue

    if sent_count:
        await db.commit()
        logger.info(f"Meeting {meeting.id}: sent {sent_count} participant emails")


async def _handle_crm_extraction_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle crm_extraction_complete webhook from TranscriptionBase.

    Reads the CRM extraction result from the transcription_jobs row
    and upserts it into call_intelligence.crm_extraction.
    """
    import uuid as _uuid
    from app.models.call_intelligence import CallIntelligence

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    transcription_job_id = event_data.get("transcription_job_id")
    if not transcription_job_id:
        logger.warning(f"CRM extraction complete for meeting {meeting.id} but no transcription_job_id in payload")
        return

    # Load the job to get result
    result = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.id == _uuid.UUID(transcription_job_id)
        )
    )
    tj = result.scalar_one_or_none()
    if not tj or not tj.result:
        logger.warning(f"CRM extraction job {transcription_job_id} not found or has no result")
        return

    tj_result = tj.result if isinstance(tj.result, dict) else {}
    crm_data = tj_result.get("crm_extraction")
    if not crm_data:
        logger.warning(f"CRM extraction job {transcription_job_id} result has no crm_extraction key")
        return

    # Upsert call_intelligence.crm_extraction — order by latest to handle
    # retranscribed meetings (multiple rows; no version column).
    ci_result = await db.execute(
        select(CallIntelligence)
        .where(CallIntelligence.meeting_id == meeting.id)
        .order_by(CallIntelligence.version.desc())
        .limit(1)
    )
    existing_ci = ci_result.scalar_one_or_none()

    if existing_ci:
        existing_ci.crm_extraction = crm_data
    else:
        existing_ci = CallIntelligence(
            meeting_id=meeting.id,
            team_id=meeting.team_id,
            created_by=meeting.created_by,
            call_type="uncertain",
            crm_extraction=crm_data,
            processing_status="completed",
        )
        db.add(existing_ci)

    await db.commit()
    logger.info(f"CRM extraction stored for meeting {meeting.id} (job {transcription_job_id})")

    # Log CRM extraction cost (was previously missing — fixed in migration 023)
    prompt_log = tj_result.get("prompt_log", [])
    if prompt_log:
        try:
            from app.services.intelligence_store import store_all_costs
            await store_all_costs(
                db,
                meeting,
                tj_result,  # contains prompt_log[]
                operation_type="crm_extraction",
            )
            await db.commit()
            logger.info(f"CRM extraction cost logged for meeting {meeting.id}")
        except Exception as e:
            logger.warning(f"Failed to log CRM extraction cost for meeting {meeting.id}: {e}")

    # Clear Redis progress key — pipeline complete, no longer needed
    try:
        await meeting_status_cache.get_shared_redis().delete(
            f"meeting_progress:{meeting.id}"
        )
    except Exception as e:
        logger.warning(f"Failed to clear meeting progress for {meeting.id}: {e}")


async def _handle_failure(
    db: AsyncSession, meeting: Meeting, payload: dict, reason: str
) -> None:
    """Handle failure events: mark meeting as failed."""
    meeting.status = "failed"
    if not meeting.ended_at:
        meeting.ended_at = datetime.now(timezone.utc)
    await db.commit()
    event_data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    error_msg = event_data.get("error") or payload.get("error") or "unknown"
    logger.warning(f"Meeting {meeting.id} failed: {reason} - {error_msg}")


async def _handle_diarization_failed(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle diarization_failed (T6) — fast-email-best-effort + retry once.

    Per plan §10.1 / FR-6:
    - First failure (no completed T7 yet for this meeting, or failing T6 was
      NOT a retry): queue T7 with diarization_path=null so the fast email
      still goes out with UNKNOWN labels (today's behavior). Then re-queue T6
      with params.is_t6_retry=true to attempt diarization one more time
      before T5.
    - Retry failure (failing T6 had params.is_t6_retry=True): T5/T3/T2 are
      skipped and meeting.status='fast_email_only'. No fallback to
      _handle_failure (which would set status='failed').

    The new status value 'fast_email_only' lives in the existing
    meetings.status String(100) column — no migration. Frontend renders this
    with a 'Polished version unavailable' banner.
    """
    import json as _json
    import uuid as _uuid
    from sqlalchemy import text as sa_text

    event_data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    tj_id = event_data.get("transcription_job_id")
    error_msg = event_data.get("error") or payload.get("error") or "unknown"
    meeting_id_str = str(meeting.id)

    # Pull the failing T6 job to inspect params.is_t6_retry + preserve forwards
    failed_tj = None
    failed_params = {}
    if tj_id:
        try:
            result = await db.execute(
                select(TranscriptionJob).where(TranscriptionJob.id == _uuid.UUID(tj_id))
            )
            failed_tj = result.scalar_one_or_none()
            if failed_tj and failed_tj.params:
                failed_params = failed_tj.params if isinstance(failed_tj.params, dict) else {}
        except Exception as e:
            logger.warning(f"Meeting {meeting.id}: failed to load failing T6 row: {e}")

    is_retry_failure = bool(failed_params.get("is_t6_retry", False))

    # Also gate on whether T7 has already completed for this meeting — belt
    # and braces alongside the params flag.
    t7_completed_q = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.operation_type == 7,
            TranscriptionJob.status == "completed",
        ).limit(1)
    )
    t7_already_completed = t7_completed_q.scalar_one_or_none() is not None

    if is_retry_failure or (t7_already_completed and not is_retry_failure):
        # Retry failure path. (The OR clause above is defensive: if for any
        # reason the params flag wasn't set but T7 has completed, we're
        # already past the first-failure path so treat as a retry-failure.)
        meeting.status = "fast_email_only"
        # Also mark intelligence_status terminal so the frontend stops polling.
        # Without this, intelligence_status stays "pending" forever because
        # T3/T2 (which normally set it) never run.
        meeting.intelligence_status = "failed"
        if not meeting.ended_at:
            meeting.ended_at = datetime.now(timezone.utc)
        await db.commit()
        logger.warning(
            f"Meeting {meeting.id}: T6 retry also failed — marking "
            f"fast_email_only (T5/T3/T2 skipped). Error: {error_msg}"
        )
        return

    # First failure path — strictly serial recovery chain:
    #   queue ONLY T7 (best-effort, diarization_path=null) here.
    #   When T7 completes, _handle_fast_p5_complete sees
    #   params.was_diarization_failed=True and queues T6 retry (instead of T5).
    #   When T6 retry completes, _handle_diarization_complete sees
    #   params.is_t6_retry=True and queues T5 directly (skipping T7 — already ran).
    # Earlier draft queued T7 + T6 retry in parallel; that created a race where
    # T7 → _handle_fast_p5_complete could queue T5 before T6 retry produced
    # diarization output.
    logger.warning(
        f"Meeting {meeting.id}: T6 first attempt failed — queuing T7 "
        f"best-effort (T6 retry follows after T7 completes). Error: {error_msg}"
    )

    # Look up the original Type 1 job for ASR-relevant params (mirrors
    # _handle_enhance_complete's pattern).
    original_tj_result = await db.execute(
        select(TranscriptionJob).where(
            TranscriptionJob.meeting_id == meeting.id,
            TranscriptionJob.operation_type == 1,
        ).order_by(TranscriptionJob.created_at.desc()).limit(1)
    )
    original_tj = original_tj_result.scalar_one_or_none()
    original_params = {}
    if original_tj and original_tj.params:
        original_params = original_tj.params if isinstance(original_tj.params, dict) else {}

    # Fast-path defaults — pull from the failing T6 job's params (which were
    # set by _handle_preprocess_complete or _handle_enhance_complete).
    preprocess_path = failed_params.get(
        "preprocess_path", f"meetings/{meeting_id_str}/preprocess/"
    )
    enhance_path = failed_params.get(
        "enhance_path", f"meetings/{meeting_id_str}/enhance/"
    )
    output_base_path = failed_params.get(
        "output_base_path", f"meetings/{meeting_id_str}/v1/"
    )

    fast_params = {
        "preprocess_path": preprocess_path,
        "enhance_path": enhance_path,
        # diarization_path NOT set on purpose — T7's speaker-attribution block
        # will see this missing and fall back to UNKNOWN labels.
        "output_base_path": output_base_path,
        "team_id": str(meeting.team_id),
        "input_audio_path": failed_params.get(
            "input_audio_path", original_params.get("input_audio_path", "")
        ),
        "meeting_title": failed_params.get(
            "meeting_title", original_params.get("meeting_title")
        ),
        "participant_names": failed_params.get(
            "participant_names", original_params.get("participant_names", [])
        ),
        "language": failed_params.get("language", original_params.get("language")),
        "engine": failed_params.get("engine", original_params.get("engine")),
        "model_size": failed_params.get("model_size", original_params.get("model_size")),
        "phone_mode": failed_params.get(
            "phone_mode", original_params.get("phone_mode", True)
        ),
        "recording_source": failed_params.get(
            "recording_source", original_params.get("recording_source", "bot")
        ),
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "max_retries": 2,
        "crm_contacts": failed_params.get("crm_contacts", []),
        "crm_contacts_fetched": failed_params.get("crm_contacts_fetched", False),
        "enhance_skipped": failed_params.get("enhance_skipped", False),
        "tier_2_1_signals": failed_params.get("tier_2_1_signals", {}),
        # Two flags driving the serial recovery chain in downstream handlers:
        # 1) Audit/observability — grep `was_diarization_failed=True` to count
        #    best-effort runs.
        # 2) Behavioral — _handle_fast_p5_complete reads this and queues T6
        #    retry (instead of the normal T5) when set.
        "was_diarization_failed": True,
    }

    # Stash the original T6 params (verbatim minus is_t6_retry flag) onto T7
    # params so _handle_fast_p5_complete can rebuild the T6 retry insert with
    # the same payload after T7 completes.
    fast_params["t6_retry_params"] = {**failed_params, "is_t6_retry": True}

    bjid = failed_tj.bot_job_id if failed_tj else f"recover-{meeting_id_str}"

    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, operation_type, status, params) "
            "VALUES (:mid, :bjid, 7, 'waiting', :params)"
        ),
        {
            "mid": meeting_id_str,
            "bjid": bjid,
            "params": _json.dumps(fast_params),
        },
    )
    await db.commit()
    logger.info(
        f"Meeting {meeting.id}: queued T7 (best-effort, no diarization) "
        f"after diarization_failed — T6 retry will be queued by "
        f"_handle_fast_p5_complete when T7 finishes"
    )


async def _handle_intelligence_complete(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle intelligence_complete webhook from TranscriptionBase (operation_type=3)."""
    meeting_id = meeting.id
    team_id = meeting.team_id
    transcript_path = meeting.transcript_path
    analysis_path_fallback = meeting.analysis_path

    event_data = payload.get("data", {})
    if not isinstance(event_data, dict):
        event_data = {}

    tj_id = event_data.get("transcription_job_id")
    storage = create_storage()

    tj = None
    if tj_id:
        import uuid as _uuid
        result = await db.execute(
            select(TranscriptionJob).where(
                TranscriptionJob.id == _uuid.UUID(tj_id)
            )
        )
        tj = result.scalar_one_or_none()

    tj_result = (tj.result if tj and tj.result else {}) or {}
    tj_params = {}
    if tj and tj.params:
        tj_params = tj.params if isinstance(tj.params, dict) else {}

    intelligence_path = tj_result.get("intelligence_path")
    intelligence_data = {}
    if intelligence_path:
        try:
            if await storage.exists(intelligence_path):
                intelligence_data = await storage.read_json(intelligence_path)
                meeting.intelligence_path = intelligence_path
                logger.info(f"Meeting {meeting_id}: loaded intelligence.json ({intelligence_data.get('call_type', 'unknown')})")
        except Exception as e:
            logger.warning(f"Could not read intelligence.json: {e}")

    # Replace LLM-inferred participants with mapped speakers BEFORE saving analysis.json
    memoir_data = intelligence_data.get("memoir")
    logger.info(f"Meeting {meeting_id}: memoir_data present={memoir_data is not None}, transcript_path={transcript_path}")

    if memoir_data:
        call_meta = memoir_data.get("call_metadata") or {}
        llm_participants = call_meta.get("participants") or []
        mapped_parts_result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.platform_display_name.isnot(None),
            )
        )
        mapped_parts = mapped_parts_result.scalars().all()

        if mapped_parts:
            db_participants = []
            for mp in mapped_parts:
                llm_role = None
                for lp in llm_participants:
                    if isinstance(lp, dict):
                        lp_name = lp.get("name") or ""
                        if (lp_name == mp.diarization_label or
                                lp_name.upper().replace(" ", "_") == mp.diarization_label):
                            llm_role = lp.get("role")
                            break
                if not llm_role and mp.talk_time_seconds:
                    max_talk = max((p.talk_time_seconds or 0) for p in mapped_parts)
                    llm_role = "rep" if mp.talk_time_seconds == max_talk else "prospect"
                db_participants.append({
                    "name": mp.platform_display_name,
                    "role": llm_role or "participant",
                })
            if db_participants:
                call_meta["participants"] = db_participants
                memoir_data["call_metadata"] = call_meta
                logger.info(f"Meeting {meeting_id}: replaced LLM participants with {len(db_participants)} mapped speakers")

    # Update analysis.json with memoir data
    if memoir_data:
        try:
            segments_path = transcript_path or ""
            base_dir = segments_path.rsplit("/", 1)[0] if "/" in segments_path else ""
            a_path = f"{base_dir}/analysis.json" if base_dir else analysis_path_fallback
            if a_path:
                analysis = {}
                try:
                    if await storage.exists(a_path):
                        analysis = await storage.read_json(a_path)
                except Exception:
                    pass
                analysis["intelligence"] = memoir_data
                await storage.save_json(a_path, analysis)
                logger.info(f"Meeting {meeting_id}: analysis.json updated with memoir data")
        except Exception as e:
            logger.error(f"Failed to update analysis.json with memoir data for meeting {meeting_id}: {e}")

    if intelligence_data and intelligence_data.get("call_type") not in (None, "internal"):
        try:
            from app.services.intelligence_store import store_intelligence_data
            await store_intelligence_data(db, meeting, intelligence_data)
            await db.commit()
            logger.info(f"Meeting {meeting_id}: intelligence data stored in DB")
        except Exception as e:
            logger.error(f"Intelligence DB storage failed for meeting {meeting_id}: {e}")
            await db.rollback()

    if intelligence_data:
        try:
            from app.services.intelligence_store import store_deal_data, store_all_costs
            await store_deal_data(db, meeting, intelligence_data)

            pipeline_cost_path = tj_result.get("pipeline_cost_path")
            pipeline_cost_data = None
            if pipeline_cost_path:
                try:
                    if await storage.exists(pipeline_cost_path):
                        pipeline_cost_data = await storage.read_json(pipeline_cost_path)
                except Exception:
                    pass

            version = tj_params.get("version", 1)
            op_type = "retranscribe" if version > 1 else "transcription"
            recording_source = tj_params.get("recording_source", "bot")

            await store_all_costs(
                db, meeting, intelligence_data,
                operation_type=op_type,
                recording_source=recording_source,
                pipeline_cost_data=pipeline_cost_data,
            )
            await db.commit()
            logger.info(f"Meeting {meeting_id}: deal data + pipeline costs stored")
        except Exception as e:
            logger.error(f"Deal/cost storage failed for meeting {meeting_id}: {e}")
            await db.rollback()

    try:
        from app.services.cost_calculator import check_budget_after_meeting
        await check_budget_after_meeting(db, team_id)
        await db.commit()
    except Exception:
        await db.rollback()

    # Re-fetch meeting to get a clean session state after potential rollbacks
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one()

    meeting.intelligence_status = "completed"

    # Update MemoirSummary with Phase 10 data for list-view display
    if memoir_data:
        try:
            from sqlalchemy import select as _sel
            ms_result = await db.execute(
                _sel(MemoirSummary)
                .where(MemoirSummary.meeting_id == meeting_id)
                .order_by(MemoirSummary.version.desc())
                .limit(1)
            )
            ms = ms_result.scalar_one_or_none()
            if ms:
                narrative = memoir_data.get("narrative_summary") or ""
                if narrative:
                    ms.summary_excerpt = (narrative[:150].rsplit(" ", 1)[0] + "...") if len(narrative) > 150 else narrative
                call_meta = memoir_data.get("call_metadata") or {}
                for p in (call_meta.get("participants") or []):
                    if isinstance(p, dict) and p.get("role") not in ("rep", "representative", "internal"):
                        ms.primary_participant_name = p.get("name")
                        ms.primary_participant_company = p.get("company")
                        break
                ms.overall_confidence_score = memoir_data.get("overall_confidence_score")
        except Exception as e:
            logger.warning(f"Failed to update MemoirSummary list fields: {e}")
            await db.rollback()
            result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            meeting = result.scalar_one()
            meeting.intelligence_status = "completed"

    await db.commit()

    is_auto_chain = tj_params.get("auto_chain", False)
    if is_auto_chain and intelligence_data and intelligence_data.get("call_type") not in (None, "internal"):
        try:
            from app.services.crm_extraction import create_crm_extraction_job
            await create_crm_extraction_job(db, meeting.id)
            logger.info(f"Meeting {meeting_id}: CRM extraction job auto-queued after intelligence")
        except Exception as e:
            logger.error(f"Failed to queue CRM extraction for meeting {meeting_id}: {e}")

        try:
            from app.services.crm_push_dispatcher import dispatch_crm_auto_push
            await dispatch_crm_auto_push(db, meeting, intelligence_data)
        except Exception as e:
            logger.error(f"CRM auto-push failed for meeting {meeting_id}: {e}")

    try:
        publisher = RedisEventPublisher(settings.REDIS_URL)
        await publisher.publish("intelligence_completed", {
            "meeting_id": str(meeting_id),
            "team_id": str(team_id),
        })
        await publisher.close()
    except Exception:
        pass

    logger.info(f"Meeting {meeting_id}: intelligence processing completed")


async def _handle_intelligence_failed(
    db: AsyncSession, meeting: Meeting, payload: dict
) -> None:
    """Handle intelligence_failed webhook — mark meeting intelligence as failed."""
    meeting.intelligence_status = "failed"
    await db.commit()
    logger.warning(f"Meeting {meeting.id}: intelligence pipeline failed")


@router.post("/bot-events")
async def bot_events(
    payload: dict[str, Any],
    _: None = Depends(verify_internal_call),
    db: AsyncSession = Depends(get_db),
):
    """Receive events from the Bot Orchestrator and TranscriptionBase.

    Event types:
    - joined_meeting: Bot joined the call
    - meeting_ended: Call ended, processing begins
    - recording_complete: Bot finished recording, transcription job queued
    - preprocess_complete: Preprocessing + diarization done (type 1) → queues type 4
    - enhance_complete: Resemble-enhance done (type 4) → queues type 7 (fast)
    - fast_p5_complete: Fast ASR + P5 + email send (type 7) → queues type 6
    - diarization_complete: Diarization done (type 6) → queues type 5
    - transcription_complete: ASR + postprocess done (type 5) → queues type 3
    - intelligence_complete: Intelligence pipeline (P1-P4, P6) finished
    - intelligence_failed: Intelligence pipeline failed
    - transcription_failed: Pipeline processing failed
    - bot_error: Bot encountered an error
    - bot_denied: Bot was denied entry to the meeting
    """
    # Timing instrumentation — added 2026-05-14 to diagnose the post-admit
    # latency gap. The `joined_meeting` handler was observed taking ~2.9 s
    # between "Webhook received" and "status -> recording" in prod. Logs
    # below split that into select + dispatch + commit so the next test
    # pinpoints which step is slow. Limited to status-changing events to
    # avoid log spam from heartbeats / chunk_saved.
    _t_entry = time.monotonic()

    event_type = payload.get("event_type") or payload.get("eventType") or payload.get("event")
    job_id = payload.get("job_id") or payload.get("jobId")

    if not event_type or not job_id:
        raise HTTPException(400, "Missing event_type or job_id")

    logger.info(f"Webhook received: event={event_type}, job_id={job_id}")
    _t_received = time.monotonic()

    # Short-circuit no-op events BEFORE the meeting lookup. chunk_saved
    # and heartbeat arrive at ~7-10/sec during an active recording and
    # historically forced a DB SELECT per event just to fall into the
    # "Unknown event type" warning branch. Under sustained load that
    # added enough pool pressure to choke other handlers (witnessed
    # 2026-05-29: pool exhaustion + 70s connection holds + asyncpg
    # CancelledError on new connects). Drop them at the door.
    _NOOP_EVENTS = {"chunk_saved", "heartbeat"}
    if event_type in _NOOP_EVENTS:
        return {"status": "ok", "event_type": event_type, "skipped": True}

    meeting = await _get_meeting_by_job_id(db, job_id)
    _t_meeting_loaded = time.monotonic()

    # Forward-only status guard. Events from the bot can arrive out of order
    # because the BotWorker's EventSender is fire-and-forget over HTTP and
    # multiple in-flight POSTs can race on the orchestrator forwarder.
    # Witnessed 2026-05-13 with job a6542a5c: waiting_room_entered landed
    # ~2 s AFTER joined_meeting, flipping status "recording" -> "waiting_in_lobby"
    # and stranding the extension's poller because it gates the button on
    # status="recording" exactly. Pre-recording statuses ("joining",
    # "waiting_in_lobby") MUST NOT overwrite later states. Compare against
    # a small ordered list — anything not in pre-recording is post-recording
    # and protected.
    _PRE_RECORDING = ("pending", "scheduled", "joining", "waiting_in_lobby")

    if event_type == "bot_joining":
        # Atomic guard. The earlier `if meeting.status in _PRE_RECORDING:`
        # Python-side check was non-atomic — handler A could read
        # "joining" from a snapshot, then a concurrent handler B could
        # commit "recording", then handler A would finally commit its
        # stale-derived "joining" overwrite. Observed in prod 2026-05-14
        # for waiting_room_entered. The UPDATE...WHERE status IN (...)
        # form lets Postgres evaluate the guard at commit time against
        # the latest row state — if status is already past PRE_RECORDING
        # the row is excluded and rowcount=0.
        result = await db.execute(
            update(Meeting)
            .where(
                Meeting.id == meeting.id,
                Meeting.status.in_(_PRE_RECORDING),
            )
            .values(status="joining")
        )
        await db.commit()
        if result.rowcount > 0:
            logger.info(f"Meeting {meeting.id} status -> joining")
        else:
            logger.info(
                f"Meeting {meeting.id}: ignoring late bot_joining "
                f"(status was already past PRE_RECORDING at commit time)"
            )
    elif event_type == "waiting_room_entered":
        result = await db.execute(
            update(Meeting)
            .where(
                Meeting.id == meeting.id,
                Meeting.status.in_(_PRE_RECORDING),
            )
            .values(status="waiting_in_lobby")
        )
        await db.commit()
        if result.rowcount > 0:
            logger.info(f"Meeting {meeting.id} status -> waiting_in_lobby")
        else:
            logger.info(
                f"Meeting {meeting.id}: ignoring late waiting_room_entered "
                f"(status was already past PRE_RECORDING at commit time)"
            )
    elif event_type == "joined_meeting":
        await _handle_joined_meeting(db, meeting, payload)
    elif event_type == "recording_started":
        # Bot's FFmpeg is now actually capturing audio. Stamp
        # recording_started_at so the extension's poll can tell apart
        # "bot admitted but recorder cold-starting" (joined_meeting fired,
        # status="recording", but recording_started_at IS NULL) from
        # "bot actually recording" (recording_started_at IS NOT NULL).
        # Idempotent: only sets if currently NULL so retried recording_started
        # webhooks don't overwrite an earlier timestamp.
        if meeting.recording_started_at is None:
            meeting.recording_started_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Meeting {meeting.id} recording_started_at set")
    elif event_type == "meeting_ended":
        await _handle_meeting_ended(db, meeting, payload)
    elif event_type == "recording_complete":
        await _handle_recording_complete(db, meeting, payload)
    elif event_type == "preprocess_complete":
        await _handle_preprocess_complete(db, meeting, payload)
    elif event_type == "enhance_complete":
        await _handle_enhance_complete(db, meeting, payload)
    elif event_type == "fast_p5_complete":
        await _handle_fast_p5_complete(db, meeting, payload)
    elif event_type == "diarization_complete":
        await _handle_diarization_complete(db, meeting, payload)
    elif event_type == "transcription_complete":
        await _handle_transcription_complete(db, meeting, payload)
    elif event_type == "intelligence_complete":
        await _handle_intelligence_complete(db, meeting, payload)
    elif event_type == "intelligence_failed":
        await _handle_intelligence_failed(db, meeting, payload)
    elif event_type == "crm_extraction_complete":
        await _handle_crm_extraction_complete(db, meeting, payload)
    elif event_type == "crm_extraction_failed":
        logger.warning(f"CRM extraction failed for meeting {meeting.id}")
    elif event_type == "preprocess_failed":
        await _handle_failure(db, meeting, payload, "preprocess_failed")
    elif event_type == "enhance_failed":
        await _handle_failure(db, meeting, payload, "enhance_failed")
    elif event_type == "fast_p5_failed":
        # Best-effort: log + continue. Manually enqueue Type 6 so the
        # polished pipeline isn't blocked when the fast path errors.
        logger.warning(f"Meeting {meeting.id}: fast P5 failed; continuing with diarization")
        await _handle_fast_p5_complete(db, meeting, payload)
    elif event_type == "diarization_failed":
        # Fast-email speaker-attribution (2026-05-13): T6 failure now triggers
        # a best-effort fast email + one retry instead of immediately marking
        # the meeting failed. See plan §10.1.
        await _handle_diarization_failed(db, meeting, payload)
    elif event_type == "transcription_failed":
        await _handle_failure(db, meeting, payload, "transcription_failed")
    elif event_type == "bot_error":
        await _handle_failure(db, meeting, payload, "bot_error")
    elif event_type == "bot_denied":
        await _handle_failure(db, meeting, payload, "bot_denied")
    else:
        logger.warning(f"Unknown event type: {event_type}")

    # Emit a single-line timing breakdown for status-changing events. The
    # handler dispatch time includes the inner db.commit() — if "dispatch"
    # is the big number, the slow step is the UPDATE+COMMIT (likely lock
    # contention or WAL flush). If "meeting_lookup" is big, the SELECT
    # path needs work. If "received" lag is big, deps (auth, pool checkout)
    # are slow.
    # Cascade post-commit state to the /status Redis cache. Limited to
    # events that actually mutate the three fields the cache stores
    # (status / intelligence_status / recording_started_at). Pipeline-
    # internal events like preprocess_complete that only manage Redis
    # progress hashes don't need a cascade. Terminal events route
    # through publish()'s DEL path automatically.
    _STATUS_CACHE_RELEVANT_EVENTS = {
        "bot_joining",
        "waiting_room_entered",
        "joined_meeting",
        "recording_started",
        "meeting_ended",
        "recording_complete",
        "fast_p5_complete",
        "transcription_complete",
        "intelligence_complete",
        "intelligence_failed",
        "diarization_failed",
        "transcription_failed",
        "preprocess_failed",
        "enhance_failed",
        "bot_error",
        "bot_denied",
    }
    if event_type in _STATUS_CACHE_RELEVANT_EVENTS:
        await meeting_status_cache.cascade_from_db(
            db, settings.REDIS_URL, meeting.id
        )

    if event_type in (
        "bot_joining",
        "waiting_room_entered",
        "joined_meeting",
        "recording_started",
        "meeting_ended",
        "recording_complete",
    ):
        _t_end = time.monotonic()
        logger.info(
            f"Webhook timing event={event_type} "
            f"received={(_t_received - _t_entry) * 1000:.0f}ms "
            f"meeting_lookup={(_t_meeting_loaded - _t_received) * 1000:.0f}ms "
            f"dispatch={(_t_end - _t_meeting_loaded) * 1000:.0f}ms "
            f"total={(_t_end - _t_entry) * 1000:.0f}ms"
        )

    return {"status": "ok", "event_type": event_type}
