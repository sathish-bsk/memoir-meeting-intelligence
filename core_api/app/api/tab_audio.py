"""
Tab Audio Upload Session API — chunked upload for browser tab recording.
"""

import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from videoclip_common.storage import create_storage
from app.models.meeting import Meeting
from app.models.speaker import MeetingParticipant
from app.models.tab_audio_session import TabAudioUploadSession, TabAudioUploadChunk
from app.models.team import TeamMember
from app.schemas.meeting import MeetingResponse
from app.services import meeting_status_cache
from app.clients.auth_client import batch_get_users
from videoclip_common.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["tab-audio"])

MAX_CHUNK_SIZE_BYTES = 50 * 1024 * 1024   # 50MB per chunk
MAX_FULL_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500MB for fallback
MAX_SESSION_AGE_HOURS = 6  # Reject chunks to sessions older than 6h


class ParticipantIn(BaseModel):
    display_name: str


class CompleteSessionRequest(BaseModel):
    speaker_events: list = []
    # Optional roster of meeting participants captured client-side.
    # Empty list is the safe default for callers that don't scrape
    # participants (Path A web recorder without extension; legacy
    # extension popup pre-v1.5.0). When present, each entry's
    # display_name is upserted into meeting_participants with
    # mapping_source matching the meeting's recording origin, and
    # the list is also bundled into v2 speaker_events.json + into
    # transcription_jobs.params.participant_names so TB closed-vocab
    # attribution can use it.
    participants: list[ParticipantIn] = []


async def _verify_team_membership_local(
    db: AsyncSession, team_id: uuid.UUID, user_id: str
) -> TeamMember:
    from sqlalchemy import or_
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == uuid.UUID(user_id),
            or_(TeamMember.invite_status == "accepted", TeamMember.invite_status.is_(None)),
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this team")
    return member


async def _get_meeting_and_verify(
    db: AsyncSession, meeting_id: uuid.UUID, user_id: str
) -> Meeting:
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    await _verify_team_membership_local(db, meeting.team_id, user_id)
    return meeting


async def _get_session(
    db: AsyncSession, meeting_id: uuid.UUID, session_id: uuid.UUID
) -> TabAudioUploadSession:
    result = await db.execute(
        select(TabAudioUploadSession).where(
            TabAudioUploadSession.id == session_id,
            TabAudioUploadSession.meeting_id == meeting_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Upload session not found")
    return session


def _get_chunks_dir(meeting_id: uuid.UUID) -> Path:
    return Path(settings.BOT_RECORDINGS_DIR) / "uploads" / str(meeting_id) / "chunks"


def _get_upload_dir(meeting_id: uuid.UUID) -> Path:
    return Path(settings.BOT_RECORDINGS_DIR) / "uploads" / str(meeting_id)


async def _ffmpeg_concat_chunks(chunk_files: list[Path], output_wav: Path) -> None:
    """Concatenate WebM chunks using FFmpeg concat demuxer -> 16kHz mono WAV."""
    # Write concat list file (use output stem to avoid conflicts in dual-track)
    concat_list_path = output_wav.parent / f"chunks_{output_wav.stem}.txt"
    with open(concat_list_path, "w") as f:
        for chunk_file in chunk_files:
            f.write(f"file '{chunk_file.resolve()}'\n")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(output_wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg concat failed: {stderr.decode()}")
        raise HTTPException(500, "Failed to process audio chunks")


async def _ffmpeg_convert_single(input_file: Path, output_wav: Path) -> None:
    """Convert a single WebM file -> 16kHz mono WAV."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(input_file),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(output_wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg convert failed: {stderr.decode()}")
        raise HTTPException(500, "Failed to process audio file")


async def _binary_concat_and_convert(chunk_files: list[Path], output_wav: Path) -> None:
    """Binary-concatenate WebM chunk files from a continuous MediaRecorder stream,
    then convert the combined WebM to 16kHz mono WAV.

    This is used for mic chunks where the recorder runs continuously (never restarted),
    so all chunks are from the same WebM stream and can be concatenated as raw bytes.
    """
    combined_webm = output_wav.parent / f"{output_wav.stem}_combined.webm"
    with open(combined_webm, "wb") as out:
        for cf in chunk_files:
            out.write(cf.read_bytes())

    await _ffmpeg_convert_single(combined_webm, output_wav)


async def _ffmpeg_noise_reduce(input_wav: Path, output_wav: Path) -> None:
    """Apply noise reduction + cleanup to mic WAV.

    Chain:
      1. highpass 80Hz — remove low rumble/hum while preserving speech body
      2. afftdn nr=12 nf=-35 tn=1 — moderate denoise with valid ffmpeg 7 params
      3. dynaudnorm — smooth leveling so spoken voice remains audible
    """
    af_chain = (
        "highpass=f=80,"
        "afftdn=nr=12:nf=-35:tn=1,"
        "dynaudnorm=f=250:g=7"
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(input_wav),
        "-af", af_chain,
        "-ar", "16000", "-ac", "1",
        str(output_wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(f"FFmpeg noise reduction failed (using raw mic); af='{af_chain}': {stderr.decode()}")
        shutil.copy2(str(input_wav), str(output_wav))


async def _ffmpeg_mix_tracks(tab_wav: Path, mic_wav: Path, output_wav: Path) -> None:
    """Mix tab + mic WAV files into a single 16kHz mono WAV.

    mic_wav is already noise-reduced and loudnorm'd by _ffmpeg_noise_reduce.
    Uses amix with duration=longest so the output covers the full tab duration
    even if mic is slightly shorter.
    """
    filter_complex = (
        "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:weights=1 1[out]"
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(tab_wav), "-i", str(mic_wav),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "16000", "-ac", "1",
        str(output_wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg mix failed: {stderr.decode()}")
        raise HTTPException(500, "Failed to mix audio tracks")


def _build_v2_speaker_events_doc(
    speaker_events: list,
    participants: list[dict],
    source: str,
) -> dict:
    """Wrap raw events + participants into the v2 schema envelope.

    v2 is the schema produced by all writers (bot, extension tab-audio,
    web recorder) post-migration 040. TB readers handle both v1 (flat
    array) and v2 via _load_speaker_events shim in processor.py.
    """
    return {
        "schema_version": 2,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "events": speaker_events or [],
        "participants": participants or [],
    }


async def _upsert_participants(
    db: AsyncSession,
    meeting: Meeting,
    participants: list[ParticipantIn],
) -> list[str]:
    """Idempotently insert one MeetingParticipant row per display_name.

    Uses Postgres ON CONFLICT DO NOTHING against the new partial unique
    index uq_meeting_participants_predial_name from migration 040.
    Returns the cleaned list of display names actually persisted (used
    to populate transcription_jobs.params.participant_names).
    """
    if not participants:
        return []

    # mapping_source string for the new rows — discriminates origin in
    # audit logs and lets speaker_mapping._SOURCE_WEIGHT future-proof
    # per-source weighting. Three distinct values:
    #   web_recorder        → web_capture       (in-browser /record page)
    #   extension_tab_audio → extension_capture (extension popup)
    #   anything else       → bot_capture       (defensive; bot path
    #                                             normally inserts these
    #                                             via webhooks._handle_recording_complete,
    #                                             not via this helper)
    if meeting.source == "web_recorder":
        source_label = "web_capture"
    elif meeting.source == "extension_tab_audio":
        source_label = "extension_capture"
    else:
        source_label = "bot_capture"

    deduped: list[str] = []
    seen_lower: set[str] = set()
    for p in participants:
        name = (p.display_name or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        deduped.append(name)

    for name in deduped:
        # ON CONFLICT explicitly targeting the partial unique index from
        # migration 040 — without naming the target, Postgres won't infer
        # a partial index as the arbiter and the statement raises.
        # UUID generated Python-side to avoid requiring the pgcrypto
        # extension at runtime (the column has no DB default).
        await db.execute(
            sa_text(
                """
                INSERT INTO meeting_participants
                    (id, meeting_id, platform_display_name, mapping_source,
                     mapping_confidence, talk_time_seconds, segment_count,
                     word_count, created_at, updated_at)
                VALUES
                    (:id, :mid, :name, :src, 0, 0, 0, 0, NOW(), NOW())
                ON CONFLICT (meeting_id, (LOWER(platform_display_name)), mapping_source)
                WHERE diarization_label IS NULL
                DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "mid": str(meeting.id),
                "name": name,
                "src": source_label,
            },
        )

    return deduped


async def _finalize_meeting(
    db: AsyncSession,
    meeting: Meeting,
    session: TabAudioUploadSession,
    speaker_events: list,
    output_wav: Path,
    upload_dir: Path,
    participants: list[ParticipantIn] | None = None,
) -> None:
    """Common finalization: persist participants, write v2 speaker_events.json,
    upload to R2, create transcription_job, update meeting.

    Called by both the chunked /complete path and the /fallback-complete path.
    Both pass the same participants list shape — symmetric contract.
    """
    participants = participants or []

    # 1. Upsert participant rows (idempotent via partial unique index)
    persisted_names = await _upsert_participants(db, meeting, participants)

    # 2. Write v2 speaker_events.json (events + bundled participants)
    if meeting.source == "web_recorder":
        v2_source = "web_capture"
    elif meeting.source == "extension_tab_audio":
        v2_source = "extension_capture"
    else:
        v2_source = "bot_capture"
    v2_doc = _build_v2_speaker_events_doc(
        speaker_events=speaker_events,
        participants=[{"display_name": n} for n in persisted_names],
        source=v2_source,
    )
    speaker_events_path = upload_dir / "speaker_events.json"
    with open(speaker_events_path, "w") as f:
        json.dump(v2_doc, f)

    # 3. Upload final WAV + speaker_events to cloud storage (R2/S3) if configured
    if settings.STORAGE_BACKEND == "s3":
        try:
            storage = create_storage()
            recording_key = f"uploads/{meeting.id}/recording.wav"
            await storage.save_file(recording_key, str(output_wav))

            se_key = f"uploads/{meeting.id}/speaker_events.json"
            await storage.save_json(se_key, v2_doc)

            # For dual-track: also upload individual tracks for debugging
            tab_wav = upload_dir / "tab.wav"
            mic_raw_wav = upload_dir / "mic_raw.wav"
            if tab_wav.exists():
                await storage.save_file(f"uploads/{meeting.id}/tab.wav", str(tab_wav))
            if mic_raw_wav.exists():
                await storage.save_file(f"uploads/{meeting.id}/mic_raw.wav", str(mic_raw_wav))

            logger.info(f"Uploaded recording + speaker_events to cloud storage for meeting {meeting.id}")
        except Exception as e:
            logger.error(f"Failed to upload to cloud storage for meeting {meeting.id}: {e}")
            raise HTTPException(500, "Failed to upload recording to cloud storage")

    # 4. Update meeting status
    now = datetime.now(timezone.utc)
    meeting.status = "waiting_for_transcription"
    meeting.recording_path = f"uploads/{meeting.id}/recording.wav"
    meeting.original_recording_path = f"uploads/{meeting.id}/recording.wav"
    meeting.bot_job_id = f"tab-audio-{session.id}"
    meeting.ended_at = now
    if meeting.started_at:
        delta = now - meeting.started_at
        meeting.duration_seconds = int(delta.total_seconds())

    # 5. Resolve language for transcription params
    # meeting.language is NULL → multilingual auto-detect; otherwise BCP-47
    # like "en" / "es" / "ta". Mirrors §7.1.5 plan decision.
    lang = meeting.language
    is_multilingual = lang in (None, "", "auto")

    # 6. Insert transcription job. Three params changes from the original
    # hardcoded shape:
    #   - multilingual: derived from meeting.language (was always True)
    #   - language: forwarded when non-multilingual (was missing entirely)
    #   - recording_source: meeting.source (was hardcoded "extension_tab_audio";
    #     wrong for web_recorder + would silently miscategorize in analytics)
    #   - participant_names: populated from /complete payload — this is what
    #     wires v2 participants into TB closed-vocab attribution (§6.5).
    params = {
        "input_audio_path": f"uploads/{meeting.id}/recording.wav",
        "output_base_path": f"meetings/{meeting.id}/v1/",
        "webhook_url": settings.TRANSCRIPTION_WEBHOOK_URL,
        "webhook_headers": {"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
        "engine": "faster-whisper",
        "model_size": "medium",
        "processing_mode": "path_b",
        "phone_mode": True,
        "multilingual": is_multilingual,
        "language": None if is_multilingual else lang,
        "diarization": True,
        "vocab_correction": True,
        "llm_correction": True,
        "enable_intelligence": True,
        "post_processing_type": "memoir",
        "recording_source": meeting.source,
        "participant_names": persisted_names,
        "max_retries": 3,
        "team_id": str(meeting.team_id),
    }
    await db.execute(
        sa_text(
            "INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params) "
            "VALUES (:mid, :bjid, 'waiting', :params)"
        ),
        {
            "mid": str(meeting.id),
            "bjid": f"tab-audio-{session.id}",
            "params": json.dumps(params),
        },
    )

    # Mark session completed
    session.status = "completed"
    session.completed_at = now


# --- Endpoints ----------------------------------------------------------------


@router.post("/{meeting_id}/tab-audio/sessions", status_code=201)
async def create_session(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an upload session for a tab-audio or web-recorder recording."""
    meeting = await _get_meeting_and_verify(db, meeting_id, user.user_id)

    # Both extension popup tab-audio and the in-browser web recorder use
    # this session/chunk/complete pipeline. Gate accepts either source.
    _ALLOWED_SOURCES = ("extension_tab_audio", "web_recorder")
    if meeting.source not in _ALLOWED_SOURCES:
        raise HTTPException(
            400,
            f"Meeting source '{meeting.source}' is not eligible for tab-audio session "
            f"(expected one of {_ALLOWED_SOURCES})",
        )
    if meeting.status != "recording":
        raise HTTPException(400, f"Meeting is not in recording state (current: {meeting.status})")

    # Check for existing active session (idempotent — return it if exists)
    existing_result = await db.execute(
        select(TabAudioUploadSession).where(TabAudioUploadSession.meeting_id == meeting_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing and existing.status == "active":
        return {"session_id": str(existing.id), "status": existing.status}
    elif existing and existing.status == "completing":
        # Previous finalize attempt got stuck — reset so this attempt can proceed
        logger.warning(f"Resetting stale 'completing' session {existing.id} for meeting {meeting_id}")
        existing.status = "active"
        await db.commit()
        return {"session_id": str(existing.id), "status": "active"}
    elif existing:
        raise HTTPException(409, f"Session already exists in state: {existing.status}")

    session = TabAudioUploadSession(
        meeting_id=meeting_id,
        status="active",
        total_chunks=0,
        total_bytes=0,
    )
    db.add(session)
    await db.flush()

    # Create chunks directory
    chunks_dir = _get_chunks_dir(meeting_id)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    await db.commit()
    logger.info(f"Tab audio session {session.id} created for meeting {meeting_id}")
    return {"session_id": str(session.id), "status": "active"}


@router.put("/{meeting_id}/tab-audio/sessions/{session_id}/chunks/{seq}", status_code=200)
async def upload_chunk(
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    seq: int,
    file: UploadFile = File(...),
    sha256: Optional[str] = Form(None),
    start_ms: Optional[int] = Form(None),
    duration_ms: Optional[int] = Form(None),
    size: Optional[int] = Form(None),
    track: Optional[str] = Form("mixed"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a single audio chunk (idempotent — safe to retry).

    track: 'tab' (remote audio), 'mic' (local microphone), or 'mixed' (legacy single-stream).
    """
    await _get_meeting_and_verify(db, meeting_id, user.user_id)
    session = await _get_session(db, meeting_id, session_id)

    if session.status != "active":
        raise HTTPException(409, f"Session is not active (current: {session.status})")

    # Validate track value
    track_val = track or "mixed"
    if track_val not in ("tab", "mic", "mixed"):
        raise HTTPException(400, f"Invalid track value: {track_val}")

    # Age check (created_at is already tz-aware from TIMESTAMPTZ column)
    created_at_utc = session.created_at if session.created_at.tzinfo else session.created_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - created_at_utc).total_seconds() / 3600
    if age_hours > MAX_SESSION_AGE_HOURS:
        raise HTTPException(410, "Upload session has expired")

    # Check for existing chunk with same (session_id, seq, track) — idempotent
    existing_chunk_result = await db.execute(
        select(TabAudioUploadChunk).where(
            TabAudioUploadChunk.session_id == session_id,
            TabAudioUploadChunk.seq == seq,
            TabAudioUploadChunk.track == track_val,
        )
    )
    if existing_chunk_result.scalar_one_or_none():
        return {"status": "ok", "chunk_seq": seq, "track": track_val, "duplicate": True}

    # Read and size-check chunk
    content = await file.read()
    if len(content) > MAX_CHUNK_SIZE_BYTES:
        raise HTTPException(413, f"Chunk exceeds maximum size of {MAX_CHUNK_SIZE_BYTES // (1024*1024)}MB")

    # Compute SHA-256 for storage; warn if client provided one that doesn't match
    actual_sha = hashlib.sha256(content).hexdigest()
    if sha256 and actual_sha != sha256:
        logger.warning(f"SHA-256 mismatch for chunk {seq}/{track_val} of session {session_id}: expected {sha256}, got {actual_sha} — accepting anyway")

    # Save chunk file in track-specific subdirectory
    chunks_dir = _get_chunks_dir(meeting_id)
    if track_val != "mixed":
        track_dir = chunks_dir / track_val
    else:
        track_dir = chunks_dir
    track_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = track_dir / f"chunk_{seq:04d}.webm"
    with open(chunk_path, "wb") as f_out:
        f_out.write(content)

    actual_size = len(content)
    if track_val != "mixed":
        rel_path = f"uploads/{meeting_id}/chunks/{track_val}/chunk_{seq:04d}.webm"
    else:
        rel_path = f"uploads/{meeting_id}/chunks/chunk_{seq:04d}.webm"

    # Insert chunk record
    chunk = TabAudioUploadChunk(
        session_id=session_id,
        seq=seq,
        track=track_val,
        sha256=actual_sha,
        size_bytes=actual_size,
        start_ms=start_ms,
        duration_ms=duration_ms,
        file_path=rel_path,
    )
    db.add(chunk)

    # Update session totals
    session.total_chunks += 1
    session.total_bytes += actual_size

    await db.commit()
    logger.debug(f"Chunk {seq}/{track_val} uploaded for session {session_id} ({actual_size} bytes)")
    return {"status": "ok", "chunk_seq": seq, "track": track_val}


@router.post("/{meeting_id}/tab-audio/sessions/{session_id}/complete")
async def complete_session(
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    body: CompleteSessionRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Finalize session: concat chunks -> WAV -> create transcription job.

    Supports dual-track (tab + mic) and legacy single-track (mixed) recordings.
    Dual-track: concat each track separately, noise-reduce mic, then amix.
    """
    meeting = await _get_meeting_and_verify(db, meeting_id, user.user_id)
    session = await _get_session(db, meeting_id, session_id)

    if session.status not in ("active", "completing"):
        raise HTTPException(409, f"Session is not completable (current: {session.status})")

    speaker_events = body.speaker_events
    participants = body.participants

    # Get chunks ordered by seq (check BEFORE setting completing)
    chunks_result = await db.execute(
        select(TabAudioUploadChunk)
        .where(TabAudioUploadChunk.session_id == session_id)
        .order_by(TabAudioUploadChunk.seq)
    )
    chunks = chunks_result.scalars().all()

    if not chunks:
        raise HTTPException(400, "No chunks uploaded — the recording may be too short or the final chunk upload failed")

    # Mark session as completing
    session.status = "completing"
    await db.commit()

    recordings_dir = Path(settings.BOT_RECORDINGS_DIR)
    upload_dir = recordings_dir / "uploads" / str(meeting_id)
    output_wav = upload_dir / "recording.wav"

    # Group chunks by track
    tracks: dict[str, list] = {}
    for chunk in chunks:
        tracks.setdefault(chunk.track, []).append(chunk)

    # Validate track combination consistency.
    has_mixed = "mixed" in tracks
    has_separate = "tab" in tracks or "mic" in tracks
    if has_mixed and has_separate:
        session.status = "active"
        await db.commit()
        raise HTTPException(400, "Invalid upload: mixed track cannot be combined with tab/mic tracks in the same session")

    # Validate all chunk files exist
    for chunk in chunks:
        chunk_path = recordings_dir / chunk.file_path
        if not chunk_path.exists():
            logger.error(f"Chunk file missing: {chunk.file_path}")
            session.status = "active"
            await db.commit()
            raise HTTPException(500, f"Chunk file missing: {chunk.file_path}")

    try:
        if "tab" in tracks and "mic" in tracks:
            # Dual-track pipeline: concat each, noise-reduce mic, then mix
            tab_files = [recordings_dir / c.file_path for c in tracks["tab"]]
            mic_files = [recordings_dir / c.file_path for c in tracks["mic"]]

            tab_wav = upload_dir / "tab.wav"
            mic_raw_wav = upload_dir / "mic_raw.wav"

            await _ffmpeg_concat_chunks(tab_files, tab_wav)
            # Mic chunks are from a continuous MediaRecorder (never restarted),
            # so they're NOT standalone WebMs — binary concat then convert.
            await _binary_concat_and_convert(mic_files, mic_raw_wav)
            # Mic preprocessing disabled for now; mix raw mic stream directly.
            await _ffmpeg_mix_tracks(tab_wav, mic_raw_wav, output_wav)

            logger.info(f"Dual-track merge complete: {len(tab_files)} tab + {len(mic_files)} mic chunks")
        elif "tab" in tracks:
            # Tab-only (no mic) — same as legacy
            tab_files = [recordings_dir / c.file_path for c in tracks["tab"]]
            await _ffmpeg_concat_chunks(tab_files, output_wav)
        else:
            # Mixed upload mode or legacy single-track.
            # Chunks may be from a continuous recorder (requestData flushes),
            # so they're not standalone WebMs — binary concat then convert.
            chunk_files = [recordings_dir / c.file_path for c in chunks]
            await _binary_concat_and_convert(chunk_files, output_wav)
    except HTTPException:
        session.status = "active"
        await db.commit()
        raise

    # Finalize meeting + create transcription job
    await _finalize_meeting(
        db, meeting, session, speaker_events, output_wav, upload_dir,
        participants=participants,
    )
    await db.commit()

    # Cascade — _finalize_meeting sets status="waiting_for_transcription"
    # outside the webhook path; without this the cache lags at "recording"
    # until the next pipeline webhook lands.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    logger.info(f"Tab audio session {session_id} completed, transcription queued for meeting {meeting_id}")

    user_map = await batch_get_users([user.user_id])
    auth_user = user_map.get(user.user_id)
    creator_name = auth_user.get("name") if auth_user else None

    return MeetingResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=creator_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        source=meeting.source,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        has_recording=True,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
    )


@router.post("/{meeting_id}/tab-audio/sessions/{session_id}/abort")
async def abort_session(
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Abort an upload session and optionally clean up chunk files."""
    meeting = await _get_meeting_and_verify(db, meeting_id, user.user_id)
    session = await _get_session(db, meeting_id, session_id)

    session.status = "aborted"

    # Best-effort chunk file cleanup
    chunks_dir = _get_chunks_dir(meeting_id)
    try:
        if chunks_dir.exists():
            shutil.rmtree(str(chunks_dir))
    except Exception as e:
        logger.warning(f"Failed to clean up chunks dir: {e}")

    # Reset meeting status so user can retry
    if meeting.status == "recording":
        meeting.status = "recording"  # stays recording — allow retry session

    await db.commit()
    return {"status": "aborted"}


@router.post("/{meeting_id}/web-recorder-cancel", status_code=200)
async def web_recorder_cancel(
    meeting_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-fail a web_recorder meeting whose capture was cancelled.

    Called by the /record page when:
      - User clicks Cancel in the browser tab picker
      - getDisplayMedia() throws permission_denied / not_allowed
      - beforeunload fires while in requesting_capture state
      - Extension bridge reports an error mid-capture (Path B)

    Always soft-fails (per the chosen cancel policy in plan §7.1.6.5):
      - meeting.status = "failed"
      - meeting.failure_reason = "user_cancelled"
      - meeting.ended_at = NOW()
      - any associated upload session → status = "aborted"

    Idempotent. Repeated calls return the same payload without changing
    state once the meeting is already failed.
    """
    meeting = await _get_meeting_and_verify(db, meeting_id, user.user_id)

    if meeting.source != "web_recorder":
        raise HTTPException(400, "web-recorder-cancel only applies to source=web_recorder")

    # Idempotent: if already failed, return current state without re-writing
    if meeting.status == "failed":
        return {"status": "already_failed", "meeting_id": str(meeting.id)}

    # Refuse to clobber a recording that already finished. If /complete
    # has succeeded and the meeting has moved past "recording" (it's
    # waiting_for_transcription, processing, fast_email_only, or
    # completed), a late cancel call from a tab-close beforeunload race
    # must NOT overwrite valid state or wipe the chunk dir. Reject the
    # cancel; the recording stays valid.
    _ACTIVE_STATES = ("recording",)
    if meeting.status not in _ACTIVE_STATES:
        logger.info(
            f"web-recorder-cancel: meeting {meeting_id} in status={meeting.status!r}, "
            f"declining to cancel (already finalized or transitioning)"
        )
        return {"status": "not_cancellable", "meeting_id": str(meeting.id), "current_status": meeting.status}

    # Find associated session(s) — typically at most one per meeting, but
    # the model allows multiple historically. Abort all that aren't already
    # finalized so chunk cleanup gets a chance.
    sessions_result = await db.execute(
        select(TabAudioUploadSession).where(
            TabAudioUploadSession.meeting_id == meeting_id,
        )
    )
    sessions = sessions_result.scalars().all()

    for session in sessions:
        if session.status in ("active", "completing"):
            session.status = "aborted"

    # Best-effort chunk file cleanup on disk
    chunks_dir = _get_chunks_dir(meeting_id)
    try:
        if chunks_dir.exists():
            shutil.rmtree(str(chunks_dir))
    except Exception as e:
        logger.warning(f"web-recorder-cancel: failed to clean chunks dir for {meeting_id}: {e}")

    # Mark meeting failed
    now = datetime.now(timezone.utc)
    meeting.status = "failed"
    meeting.failure_reason = "user_cancelled"
    meeting.ended_at = now

    await db.commit()

    # Cascade — terminal status; publish() will DEL the cache key for
    # us. Without this, the prior cached "recording" stays until the
    # 24 h TTL fires.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    logger.info(
        f"web-recorder-cancel: meeting {meeting_id} marked failed (user_cancelled), "
        f"{len(sessions)} session(s) aborted"
    )
    return {
        "status": "cancelled",
        "meeting_id": str(meeting.id),
        "sessions_aborted": len(sessions),
    }


@router.post("/{meeting_id}/tab-audio/sessions/{session_id}/fallback-complete")
async def fallback_complete_session(
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    file: Optional[UploadFile] = File(None),
    tab_file: Optional[UploadFile] = File(None),
    mic_file: Optional[UploadFile] = File(None),
    speaker_events: str = Form("[]"),
    participants: str = Form("[]"),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full-file fallback: receive complete WebM file(s), delete all previous chunks, process.

    Supports two modes:
    - Legacy: single `file` field (backward compat)
    - Dual-track: `tab_file` + optional `mic_file`

    Symmetric contract with the chunked `/complete` path: accepts both
    `speaker_events` and `participants` as JSON-encoded Form fields so
    web_recorder + extension popup callers don't silently lose roster
    data when the chunked upload degrades to the fallback path.
    """
    meeting = await _get_meeting_and_verify(db, meeting_id, user.user_id)
    session = await _get_session(db, meeting_id, session_id)

    if session.status not in ("active", "completing"):
        raise HTTPException(409, f"Session is not in an uploadable state (current: {session.status})")

    # Determine which files we got
    has_legacy = file is not None and file.filename
    has_dual = tab_file is not None and tab_file.filename

    if not has_legacy and not has_dual:
        raise HTTPException(400, "No audio file provided — send either 'file' or 'tab_file'")

    try:
        events = json.loads(speaker_events)
    except json.JSONDecodeError:
        events = []

    # Parse participants Form field — symmetric with /complete request body.
    # Tolerate malformed JSON the same way (default to empty list).
    try:
        raw_parts = json.loads(participants) or []
    except json.JSONDecodeError:
        raw_parts = []
    parsed_participants: list[ParticipantIn] = []
    for entry in raw_parts:
        if isinstance(entry, dict) and entry.get("display_name"):
            parsed_participants.append(ParticipantIn(display_name=entry["display_name"]))
        elif isinstance(entry, str):
            # Tolerate the simpler ["Alice", "Bob"] shape
            parsed_participants.append(ParticipantIn(display_name=entry))

    # Mark as completing
    session.status = "completing"
    await db.commit()

    # Delete all previously uploaded chunk DB rows
    await db.execute(
        delete(TabAudioUploadChunk).where(TabAudioUploadChunk.session_id == session_id)
    )
    session.total_chunks = 0
    session.total_bytes = 0

    # Remove chunk files from disk
    chunks_dir = _get_chunks_dir(meeting_id)
    try:
        if chunks_dir.exists():
            shutil.rmtree(str(chunks_dir))
    except Exception as e:
        logger.warning(f"Failed to remove chunk files: {e}")

    upload_dir = _get_upload_dir(meeting_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_wav = upload_dir / "recording.wav"

    if has_dual:
        # Dual-track fallback: tab_file + optional mic_file
        tab_content = await tab_file.read()
        if len(tab_content) > MAX_FULL_FILE_SIZE_BYTES:
            raise HTTPException(413, f"Tab file exceeds maximum size of {MAX_FULL_FILE_SIZE_BYTES // (1024*1024)}MB")

        tab_webm = upload_dir / "recording_tab.webm"
        tab_wav = upload_dir / "tab.wav"
        with open(tab_webm, "wb") as f_out:
            f_out.write(tab_content)
        await _ffmpeg_convert_single(tab_webm, tab_wav)

        has_mic = mic_file is not None and mic_file.filename
        if has_mic:
            mic_content = await mic_file.read()
            if len(mic_content) > MAX_FULL_FILE_SIZE_BYTES:
                raise HTTPException(413, f"Mic file exceeds maximum size of {MAX_FULL_FILE_SIZE_BYTES // (1024*1024)}MB")

            mic_webm = upload_dir / "recording_mic.webm"
            mic_raw_wav = upload_dir / "mic_raw.wav"
            with open(mic_webm, "wb") as f_out:
                f_out.write(mic_content)
            await _ffmpeg_convert_single(mic_webm, mic_raw_wav)
            # Mic preprocessing disabled for now; mix raw mic stream directly.
            await _ffmpeg_mix_tracks(tab_wav, mic_raw_wav, output_wav)
            logger.info(f"Dual-track fallback merge for session {session_id}")
        else:
            # Tab-only fallback
            shutil.copy2(str(tab_wav), str(output_wav))
    else:
        # Legacy single-file fallback
        content = await file.read()
        if len(content) > MAX_FULL_FILE_SIZE_BYTES:
            raise HTTPException(413, f"File exceeds maximum size of {MAX_FULL_FILE_SIZE_BYTES // (1024*1024)}MB")

        full_file_path = upload_dir / "recording_full.webm"
        with open(full_file_path, "wb") as f_out:
            f_out.write(content)
        await _ffmpeg_convert_single(full_file_path, output_wav)

    # Finalize
    await _finalize_meeting(
        db, meeting, session, events, output_wav, upload_dir,
        participants=parsed_participants,
    )
    await db.commit()

    # Cascade — same as the chunked /complete path; fallback-complete
    # also flips status to "waiting_for_transcription" without a webhook.
    await meeting_status_cache.cascade_from_db(db, settings.REDIS_URL, meeting_id)

    logger.info(f"Tab audio fallback-complete for session {session_id}, meeting {meeting_id}")

    user_map = await batch_get_users([user.user_id])
    auth_user = user_map.get(user.user_id)
    creator_name = auth_user.get("name") if auth_user else None

    return MeetingResponse(
        id=meeting.id,
        team_id=meeting.team_id,
        created_by=meeting.created_by,
        created_by_name=creator_name,
        title=meeting.title,
        meeting_url=meeting.meeting_url,
        status=meeting.status,
        source=meeting.source,
        started_at=meeting.started_at,
        ended_at=meeting.ended_at,
        duration_seconds=meeting.duration_seconds,
        has_recording=True,
        scheduled_start_at=meeting.scheduled_start_at,
        created_at=meeting.created_at,
    )


@router.get("/{meeting_id}/tab-audio/sessions/{session_id}")
async def get_session(
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get session state + chunk manifest (for crash recovery)."""
    await _get_meeting_and_verify(db, meeting_id, user.user_id)
    session = await _get_session(db, meeting_id, session_id)

    chunks_result = await db.execute(
        select(TabAudioUploadChunk.seq, TabAudioUploadChunk.track)
        .where(TabAudioUploadChunk.session_id == session_id)
        .order_by(TabAudioUploadChunk.seq)
    )
    rows = chunks_result.all()
    uploaded_seqs = list({row[0] for row in rows})
    uploaded_seqs.sort()
    tracks_present = list({row[1] for row in rows})

    return {
        "session_id": str(session.id),
        "status": session.status,
        "total_chunks": session.total_chunks,
        "total_bytes": session.total_bytes,
        "uploaded_seqs": uploaded_seqs,
        "tracks": tracks_present,
        "created_at": session.created_at.isoformat(),
    }
