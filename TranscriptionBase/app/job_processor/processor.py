"""
Job processor — reads a transcription job from the DB, runs the pipeline,
saves results to storage, updates the DB, and calls the webhook.
"""
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

import httpx

from app.job_processor.config import BOT_RECORDINGS_DIR, DATA_DIR, STORAGE_BACKEND
from app.job_processor.db import get_job, update_job_status
from app.job_processor.progress import update_progress, clear_progress, update_meeting_progress
from videoclip_common.storage import create_storage

logger = logging.getLogger("job_processor.processor")


def _build_memoir_team_static_block(params: dict) -> str | None:
    """Tier 4.2 — assemble a deterministic team-static prefix for memoir
    extraction's Layer 2 cache.

    Returns None when there's nothing meaningful to include (so the
    extractor falls back to single-layer caching). When non-empty, returns
    a sorted, stable string suitable for use as a cache_control'd block.

    Sources used:
      - CRM contacts (sorted by lower-case email or name) — from Tier 1.4
        params propagation. Stable across meetings for the same team.
      - Calendar participant names (sorted) — from params.participant_names.

    Order MUST be deterministic. If two meetings for the same team produce
    different ordering, the cache key changes and Layer 2 misses.
    """
    parts: list[str] = []

    crm = params.get("crm_contacts") or []
    if crm:
        normalized: list[tuple[str, str]] = []  # (sort_key, formatted_line)
        for c in crm:
            if isinstance(c, dict):
                name = (c.get("name") or "").strip()
                email = (c.get("email") or "").strip().lower()
                if not name and not email:
                    continue
                key = email or name.lower()
                line = (
                    f"- {name} <{email}>" if name and email
                    else (f"- {name}" if name else f"- {email}")
                )
                normalized.append((key, line))
            elif isinstance(c, str) and c.strip():
                normalized.append((c.strip().lower(), f"- {c.strip()}"))
        if normalized:
            normalized.sort(key=lambda x: x[0])
            parts.append("Known CRM contacts (Memoir reference):\n" + "\n".join(line for _, line in normalized))

    names = params.get("participant_names") or []
    if names:
        sorted_names = sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})
        if sorted_names:
            parts.append("Calendar-listed participants:\n" + "\n".join(f"- {n}" for n in sorted_names))

    if not parts:
        return None
    return "\n\n".join(parts)

# GPU override env vars — set in .env.gpu on Vast.ai, not set on EC2 CPU fallback
_override_model = os.getenv("OVERRIDE_MODEL_SIZE")           # e.g. "medium"
_override_mode = os.getenv("OVERRIDE_PROCESSING_MODE")       # e.g. "path_b"
_override_diarize = os.getenv("OVERRIDE_DIARIZATION")        # e.g. "true"


def _cleanup_job_scratch(job_id: str) -> None:
    """F6 disk pressure mitigation — reclaim /tmp scratch after each job.

    Each _run_<type> already has its own `finally: shutil.rmtree(temp_dir)`
    that handles the success path. This outer sweep is a safety net for
    crashes BETWEEN inner finally blocks (e.g., TB SIGKILL mid-flight)
    that leave stranded /tmp/<stage>_job_<job_id>/ directories. Also
    sweeps stray <stage>_job_<job_id>_<random> dirs if any survive.

    Idempotent. Never touches /data/recordings — those are R2-mirrored
    and needed for retry quorum if the same job_id is re-claimed.
    """
    import shutil, glob
    patterns = [
        f"/tmp/preprocess_job_{job_id}",
        f"/tmp/enhance_job_{job_id}",
        f"/tmp/diarize_job_{job_id}",
        f"/tmp/asr_job_{job_id}",
        f"/tmp/fast_p5_job_{job_id}",
        f"/tmp/asr_postprocess_job_{job_id}",
        f"/tmp/intelligence_job_{job_id}",
    ]
    for p in patterns:
        for match in glob.glob(p) + glob.glob(p + "_*"):
            try:
                shutil.rmtree(match, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Job {job_id}: cleanup failed for {match}: {e}")


async def process_job(job_id: str) -> None:
    """Process a single job end-to-end (transcription or CRM extraction)."""
    job = await get_job(job_id)
    if not job:
        logger.error(f"Job {job_id} not found in DB")
        return

    if job["status"] != "in_progress":
        logger.warning(f"Job {job_id} status is {job['status']}, expected in_progress")
        return

    params = job["params"] if isinstance(job["params"], dict) else json.loads(job["params"])
    meeting_id = str(job["meeting_id"]) if job["meeting_id"] else None
    operation_type = job.get("operation_type", 1)

    try:
        if operation_type == 1:
            result = await _run_preprocess(job_id, params, meeting_id)
            event_type = "preprocess_complete"
        elif operation_type == 2:
            result = await _run_crm_extraction(job_id, params, meeting_id)
            event_type = "crm_extraction_complete"
        elif operation_type == 3:
            result = await _run_intelligence(job_id, params, meeting_id)
            event_type = "intelligence_complete"
        elif operation_type == 4:
            result = await _run_enhance(job_id, params, meeting_id)
            event_type = "enhance_complete"
        elif operation_type == 5:
            result = await _run_asr_postprocess(job_id, params, meeting_id)
            event_type = "transcription_complete"
        elif operation_type == 6:
            result = await _run_diarization(job_id, params, meeting_id)
            event_type = "diarization_complete"
        elif operation_type == 7:
            result = await _run_asr_p5_fast(job_id, params, meeting_id)
            event_type = "fast_p5_complete"
        else:
            result = await _run_transcription(job_id, params, meeting_id)
            event_type = "transcription_complete"

        await update_job_status(job_id, "completed", result=result)
        logger.info(f"Job {job_id} completed successfully (type={operation_type})")

        # Build webhook data based on operation type
        webhook_data = {
            "transcription_job_id": job_id,
            "meeting_id": meeting_id,
        }
        if operation_type == 5:
            webhook_data["word_count"] = result.get("word_count", 0)
            webhook_data["speaker_count"] = result.get("speaker_count", 0)

        await _call_webhook(params, {
            "event_type": event_type,
            "job_id": job["bot_job_id"] or job_id,
            "data": webhook_data,
        })

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        max_retries = params.get("max_retries", 3)
        current_retry = (job["retry_count"] or 0) + 1

        fail_event_types = {
            1: "preprocess_failed", 2: "crm_extraction_failed", 3: "intelligence_failed",
            4: "enhance_failed", 5: "transcription_failed", 6: "diarization_failed",
            7: "fast_p5_failed",
        }
        fail_event_type = fail_event_types.get(operation_type, "transcription_failed")

        if current_retry < max_retries:
            # Re-queue: set back to waiting so the worker picks it up again
            await update_job_status(
                job_id,
                "waiting",
                error_message=str(e),
                retry_count=current_retry,
            )
            logger.info(f"Job {job_id} re-queued (retry {current_retry}/{max_retries})")
        else:
            # Max retries exceeded
            await update_job_status(
                job_id,
                "failed",
                error_message=str(e),
                retry_count=current_retry,
            )
            logger.error(f"Job {job_id} permanently failed after {current_retry} retries")

            # Call webhook with failure
            await _call_webhook(params, {
                "event_type": fail_event_type,
                "job_id": job["bot_job_id"] or job_id,
                "data": {
                    "transcription_job_id": job_id,
                    "meeting_id": meeting_id,
                    "error": str(e),
                },
            })
    finally:
        # F6 — sweep /tmp scratch unconditionally. Runs on success, on
        # caught exception (after re-queue or permanent-fail webhook),
        # and on retry-requeue paths. Cheap (rmtree on missing paths is
        # a no-op via ignore_errors).
        _cleanup_job_scratch(job_id)


async def _run_preprocess(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run preprocessing + diarization only (operation type 1).

    Downloads audio → preprocesses → uploads preprocessed_audio.wav + preprocess_result.json
    to meetings/{id}/preprocess/ in R2.
    """
    from app.audio_preprocess.services.pipeline import run_preprocess

    input_audio_path = params.get("input_audio_path", "")
    preprocess_r2_path = f"meetings/{meeting_id or 'unknown'}/preprocess/"

    recordings_dir = Path(BOT_RECORDINGS_DIR).resolve()
    audio_file = recordings_dir / input_audio_path

    # --- Remote media download handling (same as _run_transcription) ---
    youtube_url = params.get("youtube_url")
    media_url = params.get("media_url")
    if youtube_url or media_url:
        import asyncio as _asyncio
        from app.services.ingest import create_request_temp_dir
        dl_temp_dir = create_request_temp_dir(f"dl-job-{job_id}")
        try:
            if youtube_url:
                from app.services.ingest import ingest_youtube
                downloaded_path = await ingest_youtube(youtube_url, job_id, dl_temp_dir)
            else:
                from app.services.ingest import ingest_media_url
                downloaded_path = await ingest_media_url(media_url, job_id, dl_temp_dir)
            wav_path = dl_temp_dir / "recording.wav"
            proc = await _asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(downloaded_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(wav_path),
                stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"FFmpeg conversion failed: {stderr_bytes.decode()}")
            audio_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(wav_path), str(audio_file))
            if STORAGE_BACKEND == "s3":
                upload_storage = create_storage()
                await upload_storage.save_file(input_audio_path, str(audio_file))
        finally:
            if dl_temp_dir.exists():
                shutil.rmtree(dl_temp_dir, ignore_errors=True)

    await update_meeting_progress(meeting_id, 1, 0, "Downloading audio...")
    logger.info(f"Job {job_id}: input_audio_path={input_audio_path}, local={audio_file}, exists={audio_file.exists()}, STORAGE_BACKEND={STORAGE_BACKEND}")
    if not audio_file.exists() and STORAGE_BACKEND == "s3":
        storage = create_storage()
        r2_exists = await storage.exists(input_audio_path)
        logger.info(f"Job {job_id}: R2 exists={r2_exists}")
        if r2_exists:
            audio_file.parent.mkdir(parents=True, exist_ok=True)
            await storage.download_file(input_audio_path, str(audio_file))
            logger.info(f"Job {job_id}: downloaded audio from cloud storage ({audio_file.stat().st_size} bytes)")
    if not audio_file.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_file}")

    temp_dir = Path(f"/tmp/preprocess_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        working_audio = temp_dir / audio_file.name
        shutil.copy2(str(audio_file), str(working_audio))

        await update_meeting_progress(meeting_id, 1, 10, "Analyzing audio...")
        preprocess_result = await run_preprocess(
            input_file=working_audio,
            temp_dir=temp_dir,
            phone_mode=params.get("phone_mode", True),
            job_id=job_id,
            meeting_id=meeting_id,
        )

        # Upload preprocessed audio + result JSON to R2
        preprocessed_audio = Path(preprocess_result["preprocessed_audio"])
        await update_meeting_progress(meeting_id, 1, 85, "Uploading preprocess results...")

        # Tier 1.5 — honor the source extension when writing recording.{ext}.
        # FLAC-encoded inputs (BOTWORKER_AUDIO_FORMAT=flac) must NOT be
        # misnamed as .wav: that breaks frontend playback via browser MIME
        # inference. FFmpeg-based consumers (ASR/diarization) probe via
        # libavformat regardless of extension, so this is asset-naming only.
        input_ext = Path(audio_file).suffix.lower() or ".wav"
        if input_ext not in (".wav", ".flac"):
            input_ext = ".wav"

        if STORAGE_BACKEND == "s3":
            upload_storage = create_storage()
            # Upload preprocessed audio
            r2_audio_key = f"{preprocess_r2_path}preprocessed_audio.wav"
            await upload_storage.save_file(r2_audio_key, str(preprocessed_audio))
            # Upload preprocess_result.json (without the local path)
            result_for_storage = dict(preprocess_result)
            result_for_storage.pop("preprocessed_audio", None)

            import tempfile
            result_json_path = temp_dir / "preprocess_result.json"
            result_json_path.write_text(json.dumps(result_for_storage, indent=2, default=str))
            r2_result_key = f"{preprocess_r2_path}preprocess_result.json"
            await upload_storage.save_file(r2_result_key, str(result_json_path))

            # Upload recording at meeting level using the source extension
            # computed above.
            import re
            meeting_base = f"meetings/{meeting_id}"
            recording_rel_path = f"{meeting_base}/recording{input_ext}"
            data_dir = Path(DATA_DIR).resolve()
            recording_output = data_dir / recording_rel_path
            recording_output.parent.mkdir(parents=True, exist_ok=True)
            if not recording_output.exists():
                shutil.copy2(str(audio_file), str(recording_output))
            await upload_storage.save_file(recording_rel_path, str(recording_output))

            # Clean up local files
            shutil.rmtree(str(recording_output.parent), ignore_errors=True)
            if audio_file.parent != recordings_dir:
                shutil.rmtree(str(audio_file.parent), ignore_errors=True)

            logger.info(f"Job {job_id}: uploaded preprocess results to R2 ({preprocess_r2_path})")

        await update_meeting_progress(meeting_id, 1, 100, "Preprocess complete")

        # Tier 2.1 — surface SNR + clipping signals for the conditional Type 4 skip
        # decision in core_api's _handle_preprocess_complete. High-SNR clean audio
        # bypasses Resemble Enhance; noisy/clipped audio runs Type 4 as today.
        analysis = preprocess_result.get("analysis", {}) or {}
        snr_db = float(analysis.get("snr_db", 0.0)) if analysis else 0.0
        clipping_pct = float(analysis.get("clipping_percentage", 100.0)) if analysis else 100.0
        skip_enhance = (snr_db > 20.0 and clipping_pct < 1.0)

        # recording_path's extension mirrors input_ext so the meeting record
        # (overridden in webhooks.py:811) carries the correct file extension.
        return {
            "preprocess_path": preprocess_r2_path,
            "recording_path": f"meetings/{meeting_id}/recording{input_ext}",
            # Tier 2.1 audio-quality signals (consumed by core_api webhook handler)
            "snr_db": snr_db,
            "clipping_pct": clipping_pct,
            "skip_enhance": skip_enhance,
        }

    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_enhance(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run resemble-enhance on preprocessed audio (operation type 4).

    Downloads preprocessed_audio.wav from R2 → enhances → uploads enhanced_audio.wav
    to meetings/{id}/enhance/.
    """
    from app.audio_preprocess.services.enhance_playback import apply_resemble_enhance
    from app.audio_preprocess.config import preprocess_config as cfg

    preprocess_path = params.get("preprocess_path", f"meetings/{meeting_id}/preprocess/")
    enhance_r2_path = f"meetings/{meeting_id or 'unknown'}/enhance/"

    temp_dir = Path(f"/tmp/enhance_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        await update_meeting_progress(meeting_id, 4, 10, "Downloading preprocessed audio...")

        # Download preprocessed audio from R2
        preprocessed_audio = temp_dir / "preprocessed_audio.wav"
        if STORAGE_BACKEND == "s3":
            storage = create_storage()
            r2_audio_key = f"{preprocess_path}preprocessed_audio.wav"
            await storage.download_file(r2_audio_key, str(preprocessed_audio))
            logger.info(f"Job {job_id}: downloaded preprocessed audio from R2")
        else:
            raise FileNotFoundError("Enhance requires S3/R2 storage backend")

        if not preprocessed_audio.exists():
            raise FileNotFoundError(f"Preprocessed audio not found: {preprocessed_audio}")

        # Run resemble-enhance
        await update_meeting_progress(meeting_id, 4, 20, "Enhancing audio...")
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        enhanced_path = await loop.run_in_executor(
            None, apply_resemble_enhance, preprocessed_audio, temp_dir, cfg.RESEMBLE_ENHANCE_URL
        )
        resemble_available = "fallback" not in enhanced_path.name
        logger.info(f"Job {job_id}: enhance complete (resemble_available={resemble_available})")

        # Upload enhanced audio to R2
        await update_meeting_progress(meeting_id, 4, 80, "Uploading enhanced audio...")
        if STORAGE_BACKEND == "s3":
            upload_storage = create_storage()
            r2_enhanced_key = f"{enhance_r2_path}enhanced_audio.wav"
            await upload_storage.save_file(r2_enhanced_key, str(enhanced_path))
            logger.info(f"Job {job_id}: uploaded enhanced audio to R2 ({enhance_r2_path})")

        enhance_label = "Enhance complete" if resemble_available else "Skipped (using preprocessed audio)"
        await update_meeting_progress(meeting_id, 4, 100, enhance_label)

        return {
            "enhance_path": enhance_r2_path,
            "resemble_available": resemble_available,
        }

    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_diarization(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run speaker diarization on enhanced audio (operation type 6).

    Downloads enhanced_audio.wav from R2 → runs pyannote diarization →
    uploads diarization_result.json to meetings/{id}/diarization/.
    """
    from app.audio_preprocess.services.diarize import diarize_audio
    from app.audio_preprocess.services.pipeline import _free_gpu_models
    from app.audio_preprocess.config import preprocess_config as cfg
    from functools import partial
    import time

    preprocess_path = params.get("preprocess_path", f"meetings/{meeting_id}/preprocess/")
    diarization_r2_path = f"meetings/{meeting_id or 'unknown'}/diarization/"

    temp_dir = Path(f"/tmp/diarize_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        await update_meeting_progress(meeting_id, 6, 10, "Downloading preprocessed audio...")

        # Download preprocessed audio from R2 (48kHz — pyannote handles resampling internally)
        preprocessed_audio = temp_dir / "preprocessed_audio.wav"
        if STORAGE_BACKEND == "s3":
            storage = create_storage()
            await storage.download_file(f"{preprocess_path}preprocessed_audio.wav", str(preprocessed_audio))
            logger.info(f"Job {job_id}: downloaded preprocessed audio from R2")
        else:
            raise FileNotFoundError("Diarization requires S3/R2 storage backend")

        # Run diarization
        await update_meeting_progress(meeting_id, 6, 20, "Identifying speakers...")
        logger.info(f"=== Type 6: Speaker Diarization on preprocessed audio (48kHz) ===")
        t0 = time.time()
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        num_speakers = params.get("num_speakers", 0)
        diarize_func = partial(diarize_audio, preprocessed_audio, cfg, num_speakers)
        diarization_result = await loop.run_in_executor(None, diarize_func)
        diar_time = time.time() - t0
        logger.info(f"Job {job_id}: diarization complete: {diarization_result['num_speakers']} speakers in {diar_time:.1f}s")

        # Free GPU models
        _free_gpu_models()

        # Upload diarization result to R2
        await update_meeting_progress(meeting_id, 6, 85, "Uploading diarization results...")
        if STORAGE_BACKEND == "s3":
            upload_storage = create_storage()
            result_path = temp_dir / "diarization_result.json"
            result_path.write_text(json.dumps(diarization_result, indent=2, default=str))
            await upload_storage.save_file(f"{diarization_r2_path}diarization_result.json", str(result_path))
            logger.info(f"Job {job_id}: uploaded diarization result to R2 ({diarization_r2_path})")

        await update_meeting_progress(meeting_id, 6, 100, "Diarization complete")

        return {
            "diarization_path": diarization_r2_path,
            "num_speakers": diarization_result.get("num_speakers", 0),
        }

    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_asr_postprocess(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run ASR + all post-processing (operation type 5).

    Downloads preprocess_result.json + enhanced_audio.wav from R2 →
    runs ASR → alignment → speaker assignment → voice embeddings → P5 → corrections →
    uploads final results to meetings/{id}/v1/.
    """
    from app.audio_preprocess.services.pipeline import (
        _restore_punctuation, _remap_segments, _free_gpu_models,
    )
    from app.audio_preprocess.services.aligner import align_segments
    from app.audio_preprocess.services.diarize import assign_speakers_to_segments
    from app.audio_preprocess.services.transcribe import transcribe
    from app.audio_preprocess.services.core_processing import resample_to_16k
    from app.audio_preprocess.services.vocabulary import VocabularyStore, apply_vocabulary_corrections
    from app.audio_preprocess.services.llm_corrector import LLMCorrector
    from app.audio_preprocess.services.email_detector import collect_email_candidates
    from app.audio_preprocess.services.name_corrector import build_name_index, correct_names_in_segments
    from app.audio_preprocess.config import preprocess_config as cfg
    from functools import partial
    import asyncio as _asyncio
    import time
    import re as _re

    preprocess_path = params.get("preprocess_path", f"meetings/{meeting_id}/preprocess/")
    enhance_path = params.get("enhance_path", f"meetings/{meeting_id}/enhance/")
    diarization_path = params.get("diarization_path", f"meetings/{meeting_id}/diarization/")
    output_base_path = params.get("output_base_path", f"meetings/{meeting_id or 'unknown'}/v1/")

    temp_dir = Path(f"/tmp/asr_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Load vocabulary
    vocab = None
    vocab_dir = Path(cfg.VOCABULARY_DIR)
    vocab_file = vocab_dir / "saas_starter.json"
    if vocab_file.exists():
        try:
            vocab = VocabularyStore.load(str(vocab_file))
            logger.info(f"Job {job_id}: loaded vocabulary with {len(vocab.entries)} entries")
        except Exception as e:
            logger.warning(f"Job {job_id}: vocabulary load failed: {e}")

    # Tier 1.4 — CRM contacts: prefer params (injected by core_api in
    # _handle_preprocess_complete and propagated through the chain). Fall
    # back to network fetch ONLY if the upstream fetch never ran (signaled
    # by crm_contacts_fetched=False or absent). An empty list with
    # crm_contacts_fetched=True means "team has 0 CRM contacts" — that's a
    # valid state and we should NOT re-fetch (which would hit the external
    # CRM API a second time).
    crm_contacts_raw = list(params.get("crm_contacts") or [])
    crm_contacts_fetched = bool(params.get("crm_contacts_fetched", False))
    crm_names = []
    crm_emails = []
    if crm_contacts_fetched:
        for _c in crm_contacts_raw:
            if isinstance(_c, dict):
                if _c.get("name"):
                    crm_names.append(_c["name"])
                if _c.get("email"):
                    crm_emails.append(_c["email"])
            elif isinstance(_c, str) and _c:
                crm_names.append(_c)
        logger.info(
            f"Job {job_id}: using {len(crm_names)} CRM contacts from params "
            f"(fetched={crm_contacts_fetched}, no network fetch)"
        )
    else:
        # Fallback: HTTP fetch (matches pre-Tier-1.4 behavior). Triggered
        # only on legacy in-flight jobs deployed before crm_contacts_fetched
        # was added, OR when _handle_preprocess_complete's call to
        # list_crm_contacts itself raised (network/auth error). Successful
        # 0-contact responses no longer trigger this path.
        try:
            import httpx as _httpx
            webhook_url = params.get("webhook_url", "")
            service_key = params.get("webhook_headers", {})
            if isinstance(service_key, str):
                import json as _j
                service_key = _j.loads(service_key)
            service_key = service_key.get("X-Service-Key", "")
            team_id_str = params.get("team_id", "")
            if webhook_url and service_key and team_id_str:
                from urllib.parse import urlparse
                parsed = urlparse(webhook_url)
                core_api_base = f"{parsed.scheme}://{parsed.netloc}"
                async with _httpx.AsyncClient(timeout=15) as _client:
                    _resp = await _client.get(
                        f"{core_api_base}/api/internal/crm/contacts",
                        params={"team_id": team_id_str},
                        headers={"X-Service-Key": service_key},
                    )
                    if _resp.status_code == 200:
                        crm_contacts_raw = _resp.json().get("contacts", [])
                        for _c in crm_contacts_raw:
                            if isinstance(_c, dict):
                                if _c.get("name"):
                                    crm_names.append(_c["name"])
                                if _c.get("email"):
                                    crm_emails.append(_c["email"])
                            elif isinstance(_c, str) and _c:
                                crm_names.append(_c)
                        if crm_names:
                            logger.info(f"Job {job_id}: fetched {len(crm_names)} CRM contacts via HTTP fallback, {len(crm_emails)} emails")
        except Exception as e:
            logger.warning(f"Job {job_id}: CRM contact HTTP fetch failed (non-fatal): {e}")

    try:
        await update_meeting_progress(meeting_id, 5, 0, "Downloading files...")
        loop = _asyncio.get_event_loop()

        # Tier 2.1: when enhance_skipped (Type 4 was skipped on clean audio),
        # enhance_path aliases preprocess_path. Type 5's ASR + alignment input
        # comes from preprocessed_audio.wav instead of enhanced_audio.wav.
        enhance_skipped = bool(params.get("enhance_skipped", False))

        # Download preprocess_result.json, audio (enhanced or preprocessed),
        # and diarization_result.json from R2
        preprocess_result_file = temp_dir / "preprocess_result.json"
        enhanced_audio = temp_dir / "enhanced_audio.wav"
        diarization_file = temp_dir / "diarization_result.json"

        if STORAGE_BACKEND == "s3":
            storage = create_storage()
            preprocessed_audio = temp_dir / "preprocessed_audio.wav"

            # Optional preprocessed_audio download for voice embeddings (48 kHz
            # higher quality than 16 kHz enhanced). Skipped when enhance_skipped
            # because the ASR audio IS preprocessed in that case — voice
            # embeddings can use the same file.
            async def _maybe_download_preprocessed():
                try:
                    await storage.download_file(
                        f"{preprocess_path}preprocessed_audio.wav",
                        str(preprocessed_audio),
                    )
                    return True
                except Exception:
                    return False

            if enhance_skipped:
                # ASR audio IS preprocessed_audio.wav; we only need 3 downloads.
                # Reuse the same local file for both ASR and voice embeddings.
                logger.info(f"Job {job_id}: Tier 2.1 enhance_skipped — reading preprocessed_audio for ASR")
                results = await _asyncio.gather(
                    storage.download_file(f"{preprocess_path}preprocess_result.json", str(preprocess_result_file)),
                    storage.download_file(f"{preprocess_path}preprocessed_audio.wav", str(enhanced_audio)),
                    storage.download_file(f"{diarization_path}diarization_result.json", str(diarization_file)),
                )
                # Voice embeddings reuse the same file
                preprocessed_audio = enhanced_audio
            else:
                # Standard path: enhanced audio for ASR, optional preprocessed for embeddings
                results = await _asyncio.gather(
                    storage.download_file(f"{preprocess_path}preprocess_result.json", str(preprocess_result_file)),
                    storage.download_file(f"{enhance_path}enhanced_audio.wav", str(enhanced_audio)),
                    storage.download_file(f"{diarization_path}diarization_result.json", str(diarization_file)),
                    _maybe_download_preprocessed(),
                )
                if results[3] is False:
                    preprocessed_audio = None
            logger.info(f"Job {job_id}: downloaded preprocess + audio + diarization from R2 (parallel)")
        else:
            raise FileNotFoundError("ASR postprocess requires S3/R2 storage backend")

        preprocess_data = json.loads(preprocess_result_file.read_text())
        diarization_result = json.loads(diarization_file.read_text())
        vad_boundaries = preprocess_data.get("vad_boundaries", [])
        preprocess_config_data = preprocess_data.get("config", {})
        phone_mode = preprocess_config_data.get("phone_mode", True)

        # Resample enhanced audio to 16kHz for Whisper ASR (Whisper requires 16kHz)
        enhanced_16k = await resample_to_16k(enhanced_audio, temp_dir)

        # Resolve ASR params
        effective_engine = params.get("engine") or cfg.ASR_ENGINE
        effective_model_size = _override_model or params.get("model_size") or cfg.ASR_MODEL_SIZE
        language = params.get("language")
        align = True

        # Build context prompt
        context_prompt = None
        try:
            prompt_parts = []
            _meeting_title = params.get("meeting_title")
            if _meeting_title:
                prompt_parts.append(f"Meeting: {_meeting_title}.")
            _context_names = params.get("participant_names", [])
            if _context_names:
                prompt_parts.append(f"Participants: {', '.join(_context_names[:10])}.")
            if prompt_parts:
                prompt_parts.append("Proper punctuation and capitalization. Use exact participant names when transcribing speech.")
                prompt_parts.append("Email addresses use @ and dot notation, e.g. john@company.io, support@acme.com.")
                context_prompt = " ".join(prompt_parts)
        except Exception:
            pass

        # ── ASR — reuse Type 7 output if present, else run Whisper inline ──
        # Type 7 (fast P5) writes raw_asr_segments.json to R2 BEFORE Type 5
        # runs. If we find that file, we skip Whisper entirely (saves ~60-90s
        # on a 30-min recording). If Type 7 was skipped or failed, we fall
        # back to running Whisper here (preserves today's behavior).
        raw_asr_key = f"{output_base_path}raw_asr_segments.json"
        type7_present = False
        try:
            type7_present = await storage.exists(raw_asr_key)
        except Exception as e:
            logger.warning(f"Job {job_id}: existence check for raw_asr failed: {e}")

        import copy
        if type7_present:
            await update_meeting_progress(meeting_id, 5, 10, "Loading Type 7 transcript...")
            logger.info(f"Job {job_id}: reusing raw_asr_segments.json from Type 7 (no Whisper rerun)")
            try:
                segments = await storage.read_json(raw_asr_key)
                transcript_text = " ".join(
                    s.get("text", "") for s in segments if isinstance(s, dict)
                ).strip()
                # Type 5 needs to write transcript_segments.json (canonical),
                # not raw_asr_segments.json (Type 7 owns that file).
                raw_asr_segments = copy.deepcopy(segments)
            except Exception as e:
                logger.warning(
                    f"Job {job_id}: failed to read Type 7 raw ASR ({e}); falling back to inline Whisper"
                )
                type7_present = False

        if not type7_present:
            await update_meeting_progress(meeting_id, 5, 10, "Transcribing audio...")
            logger.info(f"=== Type 5: ASR on enhanced audio (engine={effective_engine}, model={effective_model_size}) ===")
            t0 = time.time()
            transcribe_func = partial(
                transcribe, enhanced_16k, effective_model_size, cfg.ASR_DEVICE,
                language, effective_engine, cfg.ASR_COMPUTE_TYPE,
                False, cfg.LID_CONFIDENCE_THRESHOLD, cfg.LID_MIN_SPEECH_DURATION_MS,
                False, align,
                hotwords="", phone_mode=phone_mode,
                initial_prompt=context_prompt,
            )
            asr_result = await loop.run_in_executor(None, transcribe_func)
            asr_time = time.time() - t0
            logger.info(f"Job {job_id}: ASR complete in {asr_time:.1f}s")

            transcript_text = asr_result["text"]
            segments = asr_result["segments"]

            # Save raw ASR output before any post-processing (only when we
            # actually ran Whisper inline; otherwise Type 7 already saved it)
            raw_asr_segments = copy.deepcopy(segments)

        # DEBUG: log segments with "tight" after ASR
        _debug_tight = [s for s in segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER ASR: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  ASR tight: start={_ds['start']:.3f} end={_ds['end']:.3f} text=\"{_ds['text'][:100]}\"")

        # Free ASR model before punctuation + alignment
        _free_gpu_models()

        # Punctuation restoration (oliverguhr — replaced by Qwen refinement after LLM correction)
        # await update_meeting_progress(meeting_id, 5, 30, "Restoring punctuation...")
        # _restore_punctuation(segments)

        # Forced alignment
        await update_meeting_progress(meeting_id, 5, 35, "Aligning words...")
        logger.info("=== Type 5: Forced Alignment ===")
        t0_align = time.time()
        align_func = partial(
            align_segments, enhanced_16k, segments,
            cfg.ALIGNMENT_DEVICE, cfg.ALIGNMENT_BATCH_SIZE,
        )
        aligned_segments = await loop.run_in_executor(None, align_func)
        align_time = time.time() - t0_align
        logger.info(f"Job {job_id}: alignment complete in {align_time:.1f}s")

        # DEBUG: log segments with "tight" after alignment — ALL words + diarization turns
        _debug_tight = [s for s in aligned_segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER ALIGNMENT: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  Aligned tight seg: start={_ds['start']:.3f} end={_ds['end']:.3f}")
            logger.info(f"  text: \"{_ds['text']}\"")
            for _w in _ds.get("words", []):
                logger.info(f"    word=\"{_w.get('word','')}\" start={_w.get('start',0):.3f} end={_w.get('end',0):.3f} mid={(_w.get('start',0)+_w.get('end',0))/2:.3f}")
            # Show diarization turns for this segment
            if diarization_result:
                _diar_segs = diarization_result.get("speakers", [])
                logger.info(f"  Diarization turns overlapping [{_ds['start']:.1f}-{_ds['end']:.1f}]:")
                for _dd in _diar_segs:
                    _os = max(_ds['start'], _dd['start'])
                    _oe = min(_ds['end'], _dd['end'])
                    if _os < _oe:
                        logger.info(f"    {_dd['speaker']}: [{_dd['start']:.2f} - {_dd['end']:.2f}] (overlap: {_os:.2f}-{_oe:.2f})")

        # Speaker assignment
        await update_meeting_progress(meeting_id, 5, 45, "Assigning speakers...")
        if diarization_result:
            segments = assign_speakers_to_segments(
                diarization_result, aligned_segments,
                audio_path=enhanced_16k, config=cfg,
            )
        else:
            segments = aligned_segments

        # DEBUG: log segments with "tight" after speaker assignment
        _debug_tight = [s for s in segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER SPEAKER ASSIGN: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  Speaker tight: spk={_ds.get('speaker','?')} start={_ds['start']:.3f} end={_ds['end']:.3f} text=\"{_ds['text'][:100]}\"")

        # Remap timestamps to original audio time
        _remap_segments(segments, vad_boundaries)

        # DEBUG: log after remap
        _debug_tight = [s for s in segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER REMAP: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  Remap tight: spk={_ds.get('speaker','?')} start={_ds['start']:.3f} end={_ds['end']:.3f} orig={_ds.get('original_start',0):.3f}-{_ds.get('original_end',0):.3f}")

        # Remove duplicate words within segments (Whisper chunk boundary artifact)
        # The forced aligner can merge overlapping Whisper segments into one,
        # producing duplicate words with overlapping timestamps in the same segment.
        for seg in segments:
            words = seg.get("words", [])
            if len(words) < 2:
                continue
            seen = []
            kept = []
            for w in words:
                w_text = w.get("word", "").strip().lower().rstrip(".,!?;:")
                w_start = w.get("start", 0)
                w_end = w.get("end", 0)
                is_dup = False
                for s_text, s_start, s_end in seen:
                    if w_text == s_text and w_start < s_end and w_end > s_start:
                        is_dup = True
                        break
                if not is_dup:
                    kept.append(w)
                seen.append((w_text, w_start, w_end))
            if len(kept) < len(words):
                dropped = len(words) - len(kept)
                seg["words"] = kept
                seg["text"] = " ".join(w.get("word", "") for w in kept).strip()
                logger.info(f"Job {job_id}: dropped {dropped} duplicate words in segment [{seg.get('start', 0):.1f}s]")

        # DEBUG: log after word dedup
        _debug_tight = [s for s in segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER WORD DEDUP: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  Dedup tight: spk={_ds.get('speaker','?')} start={_ds['start']:.3f} end={_ds['end']:.3f}")

        # Free faster-whisper + aligner GPU memory BEFORE voice embedding
        # extraction. Type 5 reuses Type 7's raw_asr_segments.json (no Whisper
        # rerun) + alignment ran earlier in this stage and is complete — both
        # models can be unloaded here. Without this, ECAPA-TDNN voice embedding
        # extraction OOMs under VRAM pressure from vLLM (~11 GB) + TB main env
        # (~4 GB with Whisper+aligner+pyannote3.1 resident), and the
        # SPEAKER_XX → display-name mapping silently fails. The non-fatal
        # warning then leaves the polished transcript with raw labels.
        # Reversible on 24 GB+ GPU — see plan §15.
        logger.info(f"Job {job_id}: freeing GPU models before voice embedding extraction (frees ~3 GB for ECAPA)")
        _free_gpu_models()

        # --- Voice embeddings (moved before sliding window) ---
        await update_meeting_progress(meeting_id, 5, 50, "Extracting voice embeddings...")
        embeddings = {}
        try:
            from app.audio_preprocess.services.voice_embeddings import extract_all_embeddings
            embedding_audio = str(preprocessed_audio) if preprocessed_audio and preprocessed_audio.exists() else str(enhanced_16k)
            embeddings = extract_all_embeddings(embedding_audio, segments)
            if embeddings:
                logger.info(f"Job {job_id}: extracted voice embeddings for {len(embeddings)} speakers")
        except Exception as e:
            logger.warning(f"Job {job_id}: voice embedding extraction failed (non-fatal): {e}")

        # Sliding window speaker verification using extracted embeddings (comment out to disable)
        try:
            from app.audio_preprocess.services.diarize import verify_speakers_sliding_window
            segments = verify_speakers_sliding_window(segments, str(enhanced_16k), cfg, embeddings)
        except Exception as e:
            logger.warning(f"Job {job_id}: sliding window verification failed (non-fatal): {e}")

        # DEBUG: log after speaker verification
        _debug_tight = [s for s in segments if "tight" in s.get("text", "").lower()]
        logger.info(f"Job {job_id}: DEBUG AFTER SPEAKER VERIFY: {len(_debug_tight)} seg(s) with 'tight'")
        for _ds in _debug_tight:
            logger.info(f"  Verify tight: spk={_ds.get('speaker','?')} start={_ds['start']:.3f} end={_ds['end']:.3f} text=\"{_ds['text'][:100]}\"")
            for _w in _ds.get("words", []):
                logger.info(f"    word=\"{_w.get('word','')}\" start={_w.get('start',0):.3f} end={_w.get('end',0):.3f}")

        # Free GPU models before LLM
        _free_gpu_models()

        # --- Vocabulary correction (Phase 5a) --- collect candidates for LLM
        vocab_candidates = []
        corrected_indices = set()
        if vocab:
            await update_meeting_progress(meeting_id, 5, 48, "Applying vocabulary corrections...")
            try:
                segments, correction_log, corrected_indices, vocab_candidates = apply_vocabulary_corrections(
                    segments, vocab, cfg.VOCABULARY_CONFIDENCE_THRESHOLD, collect_only=True,
                )
                if correction_log:
                    logger.info(f"Job {job_id}: {len(correction_log)} vocabulary corrections (abbreviations)")
                if vocab_candidates:
                    logger.info(f"Job {job_id}: {len(vocab_candidates)} vocab candidates for LLM review")
            except Exception as e:
                logger.warning(f"Job {job_id}: vocabulary correction failed (non-fatal): {e}")

        # --- Email reconstruction (applied directly, not via LLM) ---
        try:
            from app.audio_preprocess.services.email_detector import collect_email_candidates
            email_candidates = collect_email_candidates(segments, crm_emails)
            if email_candidates:
                applied_emails = 0
                for ec in email_candidates:
                    seg = segments[ec["segment_index"]]
                    reconstructed = ec["phonetic_candidates"][0] if ec["phonetic_candidates"] else None
                    if not reconstructed:
                        continue
                    words = seg.get("words", [])
                    wi = ec["word_index"]
                    span = ec.get("span_length", 1)
                    if words and wi < len(words):
                        words[wi]["corrected_from"] = words[wi].get("word", "")
                        words[wi]["word"] = reconstructed
                        for j in range(1, span):
                            if wi + j < len(words):
                                words[wi + j]["word"] = ""
                        seg["text"] = " ".join(w.get("word", "") for w in words if w.get("word", "").strip())
                        applied_emails += 1
                    else:
                        text_words = seg.get("text", "").split()
                        if wi < len(text_words):
                            text_words[wi] = reconstructed
                            for j in range(1, span):
                                if wi + j < len(text_words):
                                    text_words[wi + j] = ""
                            seg["text"] = " ".join(w for w in text_words if w)
                            applied_emails += 1
                logger.info(f"Job {job_id}: {applied_emails}/{len(email_candidates)} emails reconstructed")
        except Exception as e:
            logger.warning(f"Job {job_id}: email detection failed (non-fatal): {e}")

        # --- Word-level confidence flagging for numeric tokens ---
        _NUM_PATTERN = _re.compile(r'^\$?[\d,]+\.?\d*$')
        for seg in segments:
            for word in seg.get("words", []):
                w = word.get("word", "").strip(".,!?")
                if _NUM_PATTERN.match(w) and word.get("log_prob", 0) < -0.5:
                    word["low_confidence"] = True

        # Detect language
        language_detected = "en"
        if segments:
            for seg in segments:
                if isinstance(seg, dict) and seg.get("detected_language"):
                    language_detected = seg["detected_language"]
                    break

        speaker_count = 0
        if segments:
            speaker_ids = {s.get("speaker") for s in segments if isinstance(s, dict) and s.get("speaker")}
            speaker_count = len(speaker_ids)
        word_count = len(transcript_text.split()) if transcript_text else 0

        # --- Speaker name resolution (overlap-based) ---
        await update_meeting_progress(meeting_id, 5, 50, "Resolving speakers...")
        speaker_name_map = {}
        try:
            if STORAGE_BACKEND == "s3":
                se_storage = create_storage()
                # Try to find speaker_events.json from the original recording path
                input_audio_path = params.get("input_audio_path", "")
                audio_dir_name = input_audio_path.rsplit("/", 1)[0] if "/" in input_audio_path else ""
                se_key = f"{audio_dir_name}/speaker_events.json" if audio_dir_name else None
                if se_key and await se_storage.exists(se_key):
                    se_path = temp_dir / "speaker_events.json"
                    await se_storage.download_file(se_key, str(se_path))
                    # v1/v2 schema-aware loader (returns events + participants)
                    from app.audio_preprocess.services.speaker_overlap import (
                        load_speaker_events_file,
                        compute_speaker_name_map,
                    )
                    se_events, _se_participants = load_speaker_events_file(se_path)
                    if se_events and segments:
                        speaker_name_map = compute_speaker_name_map(segments, se_events)
        except Exception as e:
            logger.warning(f"Job {job_id}: speaker_name_map failed (non-fatal): {e}")

        # Voice embeddings already extracted above (before sliding window)

        # --- Voice name lookup from DB ---
        voice_name_map = {}
        team_id = params.get("team_id")
        if team_id and embeddings:
            try:
                from app.audio_preprocess.services.voice_embeddings import lookup_voice_names
                voice_name_map = await lookup_voice_names(team_id, embeddings)
                if voice_name_map:
                    logger.info(f"Job {job_id}: voice lookup matched {len(voice_name_map)} speakers: {voice_name_map}")
            except Exception as e:
                logger.warning(f"Job {job_id}: voice name lookup failed (non-fatal): {e}")

        # --- Fuzzy name correction against known contacts ---
        all_known_names = list(set(
            params.get("participant_names", []) +
            crm_names +
            list(voice_name_map.values()) +
            list(speaker_name_map.values())
        ))
        if all_known_names:
            try:
                name_idx = build_name_index(all_known_names)
                segments, name_corrections = correct_names_in_segments(segments, name_idx)
                if name_corrections:
                    logger.info(f"Job {job_id}: {len(name_corrections)} name corrections")
            except Exception as e:
                logger.warning(f"Job {job_id}: name correction failed (non-fatal): {e}")

        # --- LLM correction (Phase 5b) --- vocab + email candidates in one call
        try:
            import httpx as _httpx
            _lr = _httpx.get(f"{cfg.LLM_BASE_URL}/api/tags", timeout=5.0)
            llm_available = _lr.status_code == 200
        except Exception:
            llm_available = False

        # Tier 4.0.b — accumulate per-call prompt_log entries from both
        # correctors so core_api can persist them to ai_prompt_log. After
        # the correctors run, we drain their internal logs and append to
        # this list, then forward via the Type 5 result dict.
        type5_prompt_log: list = []

        # Tier 5.2 — when MERGE_PUNCT_CORRECTION=true, run a single merged
        # LLM pass that handles BOTH vocabulary correction and punctuation
        # refinement in one shot. Saves ~80-160s on 1-hour calls (Ollama).
        # Default off → existing two-pass flow runs verbatim.
        merge_punct = os.getenv("MERGE_PUNCT_CORRECTION", "false").lower() == "true"

        if merge_punct and llm_available and vocab:
            await update_meeting_progress(meeting_id, 5, 62, "Running merged correction + punctuation...")
            try:
                corrector = LLMCorrector(
                    base_url=cfg.LLM_BASE_URL, model=cfg.LLM_MODEL,
                    vocab_entries=[e.to_dict() for e in vocab.entries],
                    confidence_threshold=cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                )
                segments, llm_corrections = corrector.correct_and_refine(
                    segments, corrected_indices, vocab_candidates=vocab_candidates,
                )
                if llm_corrections:
                    applied = [c for c in llm_corrections if c.get("correction_type") not in ("llm_keep", "llm_timeout", "llm_invalid", "llm_rejected")]
                    logger.info(f"Job {job_id}: merged path reviewed {len(llm_corrections)} candidates, applied {len(applied)} corrections")
                type5_prompt_log.extend(corrector.get_prompt_log())
            except Exception as e:
                logger.warning(f"Job {job_id}: merged correction+punct failed (non-fatal): {e}")
        else:
            if llm_available and vocab and (vocab_candidates or corrected_indices):
                await update_meeting_progress(meeting_id, 5, 62, "Running LLM correction...")
                try:
                    corrector = LLMCorrector(
                        base_url=cfg.LLM_BASE_URL, model=cfg.LLM_MODEL,
                        vocab_entries=[e.to_dict() for e in vocab.entries],
                        confidence_threshold=cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                    )
                    segments, llm_corrections = corrector.correct_segments(
                        segments, corrected_indices, vocab_candidates=vocab_candidates,
                    )
                    if llm_corrections:
                        applied = [c for c in llm_corrections if c.get("correction_type") not in ("llm_keep", "llm_timeout", "llm_invalid", "llm_rejected")]
                        logger.info(f"Job {job_id}: LLM reviewed {len(llm_corrections)} candidates, applied {len(applied)} corrections")
                    # Drain per-call cost data
                    type5_prompt_log.extend(corrector.get_prompt_log())
                except Exception as e:
                    logger.warning(f"Job {job_id}: LLM correction failed (non-fatal): {e}")

            # Punctuation & capitalization refinement via Qwen
            await update_meeting_progress(meeting_id, 5, 78, "Refining punctuation...")
            try:
                from app.audio_preprocess.services.punctuation_refiner import PunctuationRefiner
                refiner = PunctuationRefiner(
                    base_url=cfg.LLM_BASE_URL,
                    model=cfg.LLM_MODEL,
                    batch_size=getattr(cfg, 'PUNCTUATION_REFINE_BATCH_SIZE', 5),
                )
                segments, refine_count = refiner.refine_segments(segments)
                logger.info(f"Job {job_id}: refined punctuation in {refine_count} segments")
                type5_prompt_log.extend(refiner.get_prompt_log())
            except Exception as e:
                logger.warning(f"Job {job_id}: punctuation refinement failed (non-fatal): {e}")

        # Rebuild transcript text from corrected segments
        transcript_text = " ".join(s.get("text", "") for s in segments).strip()
        word_count = len(transcript_text.split()) if transcript_text else 0

        # Replace SPEAKER_XX with resolved names for P5
        resolved_segments = []
        for seg in segments:
            new_seg = dict(seg)
            label = new_seg.get("speaker", "")
            if label in voice_name_map:
                new_seg["speaker"] = voice_name_map[label]
            elif label in speaker_name_map:
                new_seg["speaker"] = speaker_name_map[label]
            resolved_segments.append(new_seg)

        # --- P5 Summary ---
        # POLISHED_P5_ENABLED=true  → generate polished P5 from corrected+diarized transcript
        # POLISHED_P5_ENABLED=false → reuse fast P5 (Type 7) as summary.json
        await update_meeting_progress(meeting_id, 5, 70, "Generating summary...")
        summary_result = None

        if cfg.POLISHED_P5_ENABLED:
            try:
                if cfg.INTELLIGENCE_PROVIDER in ("claude", "gemini", "groq", "ollama", "vllm"):
                    from app.job_processor.intelligence_pipeline import (
                        run_summary_only, prewarm_claude_for_stage, SUMMARY_SYSTEM,
                    )

                    # Tier 4.2 — pre-warm Anthropic cache with the polished P5
                    # system prompt before the real call hits. No-op for
                    # Ollama/Gemini/Groq. Fire-and-forget; failures don't block.
                    try:
                        await prewarm_claude_for_stage(SUMMARY_SYSTEM, None)
                    except Exception as _pw_err:
                        logger.debug(f"Job {job_id}: P5 prewarm skipped: {_pw_err}")

                    pipeline_participants = []
                    if voice_name_map:
                        pipeline_participants = [{"name": name} for name in voice_name_map.values()]
                    elif speaker_name_map:
                        pipeline_participants = [{"name": name} for name in speaker_name_map.values()]

                    summary_result = await run_summary_only(
                        segments=resolved_segments,
                        participants=pipeline_participants,
                        call_date=None,
                        rep_name=None,
                    )
                    if summary_result:
                        logger.info(f"Job {job_id}: polished P5 summary generated")
                        # Tier 4.0.b — capture per-call cost data for the polished
                        # P5 LLM call before we strip it from summary_result.
                        type5_prompt_log.extend(summary_result.get("prompt_log") or [])
            except Exception as e:
                logger.warning(f"Job {job_id}: polished P5 failed (non-fatal): {e}")
        else:
            # Polished P5 disabled — reuse fast P5 from Type 7 so summary.json
            # is still produced for downstream consumers (email preview/send,
            # memoir P5 fallback at read_endpoints.py:780, speaker rename).
            logger.info(f"Job {job_id}: POLISHED_P5_ENABLED=false, reusing fast P5 from Type 7")
            try:
                if STORAGE_BACKEND == "s3":
                    fast_storage = create_storage()
                    fast_key = f"{output_base_path}summary_fast.json"
                    if await fast_storage.exists(fast_key):
                        summary_result = await fast_storage.read_json(fast_key)
                        logger.info(f"Job {job_id}: reused summary_fast.json as summary.json")
                    else:
                        logger.warning(
                            f"Job {job_id}: summary_fast.json not found at {fast_key}; "
                            f"summary.json will not be produced"
                        )
            except Exception as e:
                logger.warning(f"Job {job_id}: fast P5 reuse failed (non-fatal): {e}")

        # --- Save all results ---
        await update_meeting_progress(meeting_id, 5, 85, "Saving results...")
        data_dir = Path(DATA_DIR).resolve()
        output_dir = data_dir / output_base_path
        output_dir.mkdir(parents=True, exist_ok=True)

        # Transcript text
        (output_dir / "transcript.txt").write_text(transcript_text)
        # Raw ASR output — only write when Type 5 ran Whisper inline. When
        # Type 7 ran (the normal path), it already wrote raw_asr_segments.json
        # to R2; Type 5 must NOT overwrite (single-writer-per-file rule).
        if not type7_present:
            (output_dir / "raw_asr_segments.json").write_text(json.dumps(raw_asr_segments, indent=2))
        # Segments (canonical polished file — Type 5 owns this)
        (output_dir / "transcript_segments.json").write_text(json.dumps(segments, indent=2))
        # Embeddings
        if embeddings:
            (output_dir / "embeddings.json").write_text(json.dumps(embeddings, indent=2))
        # Summary
        if summary_result:
            (output_dir / "summary.json").write_text(json.dumps(summary_result, indent=2, default=str))
        # Analysis (coaching metrics)
        analysis_data = {}
        try:
            from app.audio_preprocess.services.coaching_metrics import compute_coaching_metrics
            audio_duration = preprocess_data.get("audio_duration_seconds", 0)
            coaching = compute_coaching_metrics(segments, audio_duration)
            if coaching:
                analysis_data["coaching_metrics"] = coaching
                logger.info(f"Job {job_id}: coaching metrics computed")
        except Exception as e:
            logger.warning(f"Job {job_id}: coaching metrics failed (non-fatal): {e}")
        (output_dir / "analysis.json").write_text(json.dumps(analysis_data, indent=2, default=str))
        # Speaker name map
        if speaker_name_map:
            (output_dir / "speaker_name_map.json").write_text(json.dumps(speaker_name_map, indent=2))
        # Copy speaker_events if available
        se_local = temp_dir / "speaker_events.json"
        if se_local.exists():
            shutil.copy2(str(se_local), str(output_dir / "speaker_events.json"))

        # Upload to R2 (parallel — Tier 1.2)
        await update_meeting_progress(meeting_id, 5, 90, "Uploading results...")
        if STORAGE_BACKEND == "s3":
            upload_storage = create_storage()
            upload_coros = [
                upload_storage.save_file(f"{output_base_path}{filename}", str(output_dir / filename))
                for filename in [
                    "transcript.txt", "transcript_segments.json", "raw_asr_segments.json", "analysis.json",
                    "summary.json", "embeddings.json", "speaker_events.json", "speaker_name_map.json",
                ]
                if (output_dir / filename).exists()
            ]
            if upload_coros:
                await _asyncio.gather(*upload_coros)

            # Clean up local output files
            shutil.rmtree(str(output_dir), ignore_errors=True)

            # Copy preprocessed + enhanced audio to permanent v1/ path for playback
            try:
                for src, dst in [
                    (f"{preprocess_path}preprocessed_audio.wav", f"{output_base_path}preprocessed_audio.wav"),
                    (f"{enhance_path}enhanced_audio.wav", f"{output_base_path}enhanced_audio.wav"),
                ]:
                    try:
                        if await upload_storage.exists(src):
                            await upload_storage.copy(src, dst)
                            logger.info(f"Job {job_id}: copied {src} → {dst}")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Job {job_id}: audio copy failed (non-fatal): {e}")

            # Clean up intermediate R2 paths (preprocess/, enhance/, diarization/)
            try:
                for prefix in [preprocess_path, enhance_path, diarization_path]:
                    for key in [f"{prefix}preprocessed_audio.wav", f"{prefix}preprocess_result.json", f"{prefix}enhanced_audio.wav", f"{prefix}diarization_result.json"]:
                        try:
                            await upload_storage.delete(key)
                        except Exception:
                            pass
                logger.info(f"Job {job_id}: cleaned up intermediate R2 paths")
            except Exception as e:
                logger.warning(f"Job {job_id}: intermediate R2 cleanup failed (non-fatal): {e}")

            logger.info(f"Job {job_id}: uploaded final results to R2 ({output_base_path})")

        await update_meeting_progress(meeting_id, 5, 100, "Transcription complete")

        # Tier 1.5 — Type 5 no longer returns recording_path. The source
        # extension (.wav vs .flac) is known only to Type 1, which already
        # persisted meeting.recording_path via _handle_preprocess_complete.
        # _handle_transcription_complete falls through to that value.
        result_dict = {
            "word_count": word_count,
            "speaker_count": speaker_count,
            "language": language_detected,
            # transcript_path is the plain-text join used for the search
            # vector + full-text endpoint. Must NOT equal segments_path —
            # if it does, the post-edit full-text save clobbers the JSON
            # segments file. See edit_transcription guard in core_api.
            "transcript_path": f"{output_base_path}transcript.txt",
            "segments_path": f"{output_base_path}transcript_segments.json",
            "analysis_path": f"{output_base_path}analysis.json",
            # Tier 4.0.b/c — per-call cost data for ai_prompt_log persistence
            # in core_api's _handle_transcription_complete. Includes entries
            # from LLMCorrector, PunctuationRefiner, and the polished P5 call.
            "prompt_log": type5_prompt_log,
        }
        if summary_result:
            result_dict["summary_path"] = f"{output_base_path}summary.json"
        return result_dict

    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_asr_p5_fast(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run fast ASR + fast P5 + fast email send (operation type 7).

    This is the speed-optimization path: cuts time-to-summary-email from
    ~18 min to ~5 min. Runs between Type 4 (enhance) and Type 6 (diarization)
    so it has access to enhanced audio but does NOT have diarization output.

    Sequence:
      1. Download enhanced_audio.wav + preprocess_result.json from R2
      2. Resample to 16 kHz; run Whisper ASR
      3. Save raw_asr_segments.json (Type 5 will read this — never re-run Whisper)
      4. Save transcript_segments_fast.json (UI fallback)
      5. Free GPU
      6. Run P5 narrative summary (Ollama, same as Type 5)
      7. If summary returned, save summary_fast.json + POST to email-fast-summary
      8. Return — Type 6 (diarization) follows

    Failure handling: any step can fail; we still try to save the raw transcript
    so Type 5 can read it. Email failures don't block the polished pipeline —
    the gate stays NULL and polished path retries.

    Speaker labels (2026-05-13): Diarization (T6) now runs BEFORE T7 in the
    fast-email speaker-attribution pipeline. T7 downloads T6's
    diarization_result.json + (optionally) the bot's speaker_events.json, runs
    `assign_speakers_to_segments` to merge pyannote turns into Whisper
    segments, and runs `compute_speaker_name_map` to resolve SPEAKER_XX → real
    names from the bot panel events. The result is written into
    transcript_segments_fast.json and fed to the fast P5 prompt. Voice
    embedding + sliding-window verify (the slower, more accurate refinements)
    still run in Type 5 — fast and polished emails may legitimately disagree
    when ECAPA voice-verification corrects pyannote's first pass.

    If diarization_path is missing or unreadable (e.g., T6 failed), the
    segments stay without a `speaker` key — `format_transcript_for_llm` falls
    back to "UNKNOWN" labels (pre-2026-05-13 behavior) and the email still
    goes out best-effort.
    """
    from app.audio_preprocess.services.transcribe import transcribe
    from app.audio_preprocess.services.core_processing import resample_to_16k
    from app.audio_preprocess.services.pipeline import _free_gpu_models
    from app.audio_preprocess.services.vocabulary import VocabularyStore, apply_vocabulary_corrections
    from app.audio_preprocess.config import preprocess_config as cfg
    from functools import partial
    from urllib.parse import urlparse
    import asyncio as _asyncio
    import copy as _copy
    import time

    preprocess_path = params.get("preprocess_path", f"meetings/{meeting_id}/preprocess/")
    enhance_path = params.get("enhance_path", f"meetings/{meeting_id}/enhance/")
    output_base_path = params.get(
        "output_base_path", f"meetings/{meeting_id or 'unknown'}/v1/"
    )

    # Tier 2.2 — load vocabulary so we can vocab-correct the fast P5 input
    # WITHOUT polluting raw_asr_segments.json (which the /raw-asr endpoint
    # contracts as truly-raw Whisper output, before post-processing).
    vocab = None
    vocab_dir = Path(cfg.VOCABULARY_DIR)
    vocab_file = vocab_dir / "saas_starter.json"
    if vocab_file.exists():
        try:
            vocab = VocabularyStore.load(str(vocab_file))
            logger.info(f"Job {job_id}: Type 7 loaded vocabulary with {len(vocab.entries)} entries")
        except Exception as e:
            logger.warning(f"Job {job_id}: Type 7 vocabulary load failed (non-fatal): {e}")

    temp_dir = Path(f"/tmp/fast_p5_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        await update_meeting_progress(meeting_id, 7, 0, "Downloading audio...")
        loop = _asyncio.get_event_loop()

        # ── Download audio + preprocess metadata from R2 ──
        # Tier 2.1: when enhance_skipped, enhance_path aliases preprocess_path
        # and the audio file we want is preprocessed_audio.wav (no Resemble).
        # Otherwise we want enhanced_audio.wav from the enhance directory.
        enhance_skipped = bool(params.get("enhance_skipped", False))
        preprocess_result_file = temp_dir / "preprocess_result.json"

        if enhance_skipped:
            audio_local = temp_dir / "preprocessed_audio.wav"
            audio_remote_key = f"{preprocess_path}preprocessed_audio.wav"
            logger.info(f"Job {job_id}: Tier 2.1 enhance_skipped — using preprocessed audio for fast P5")
        else:
            audio_local = temp_dir / "enhanced_audio.wav"
            audio_remote_key = f"{enhance_path}enhanced_audio.wav"

        if STORAGE_BACKEND != "s3":
            raise FileNotFoundError("Fast P5 requires S3/R2 storage backend")

        storage = create_storage()
        await _asyncio.gather(
            storage.download_file(
                f"{preprocess_path}preprocess_result.json", str(preprocess_result_file)
            ),
            storage.download_file(audio_remote_key, str(audio_local)),
        )
        # Variable name kept as `enhanced_audio` for downstream code compatibility,
        # even when the file is actually preprocessed (Tier 2.1 skip path).
        enhanced_audio = audio_local
        logger.info(f"Job {job_id}: downloaded {'preprocessed' if enhance_skipped else 'enhanced'} audio + preprocess metadata from R2")

        preprocess_data = json.loads(preprocess_result_file.read_text())
        preprocess_config_data = preprocess_data.get("config", {})
        phone_mode = preprocess_config_data.get("phone_mode", True)

        # Resample to 16 kHz for Whisper
        enhanced_16k = await resample_to_16k(enhanced_audio, temp_dir)

        # ── ASR ──
        await update_meeting_progress(meeting_id, 7, 20, "Transcribing audio...")
        effective_engine = params.get("engine") or cfg.ASR_ENGINE
        effective_model_size = (
            _override_model or params.get("model_size") or cfg.ASR_MODEL_SIZE
        )
        language = params.get("language")

        # Build context prompt with calendar participants (if any)
        context_prompt = None
        try:
            prompt_parts = []
            _meeting_title = params.get("meeting_title")
            if _meeting_title:
                prompt_parts.append(f"Meeting: {_meeting_title}.")
            _context_names = params.get("participant_names", []) or []
            if _context_names:
                prompt_parts.append(
                    f"Participants: {', '.join(_context_names[:10])}."
                )
            if prompt_parts:
                prompt_parts.append(
                    "Proper punctuation and capitalization. "
                    "Use exact participant names when transcribing speech."
                )
                prompt_parts.append(
                    "Email addresses use @ and dot notation, e.g. john@company.io, support@acme.com."
                )
                context_prompt = " ".join(prompt_parts)
        except Exception:
            pass

        t0 = time.time()
        transcribe_func = partial(
            transcribe, enhanced_16k, effective_model_size, cfg.ASR_DEVICE,
            language, effective_engine, cfg.ASR_COMPUTE_TYPE,
            False, cfg.LID_CONFIDENCE_THRESHOLD, cfg.LID_MIN_SPEECH_DURATION_MS,
            False, True,
            hotwords="", phone_mode=phone_mode,
            initial_prompt=context_prompt,
        )
        asr_result = await loop.run_in_executor(None, transcribe_func)
        logger.info(f"Job {job_id}: fast ASR complete in {time.time()-t0:.1f}s")

        segments = asr_result["segments"]
        transcript_text = asr_result.get("text", "")

        # Free GPU before P5 LLM
        _free_gpu_models()

        # Tier 2.2 — vocab correction in Type 7. Operates on a deep copy so
        # raw_asr_segments.json stays truly raw (preserves /raw-asr debug
        # contract). The vocab-corrected copy goes into the fast P5 prompt
        # AND transcript_segments_fast.json. Type 5's vocab block (line 707)
        # remains and applies vocab again to its own raw input — acceptable
        # ~1 s redundancy in exchange for the clean semantic separation.
        fast_segments = _copy.deepcopy(segments)
        if vocab is not None:
            try:
                fast_segments, _correction_log, _corrected_indices, _candidates = (
                    apply_vocabulary_corrections(
                        fast_segments,
                        vocab,
                        cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                        collect_only=False,
                    )
                )
                if _correction_log:
                    logger.info(
                        f"Job {job_id}: Type 7 vocab corrected {len(_correction_log)} word(s)"
                    )
            except Exception as e:
                logger.warning(
                    f"Job {job_id}: Type 7 vocab correction failed (non-fatal): {e}"
                )
                fast_segments = _copy.deepcopy(segments)  # roll back to raw

        # ── Speaker attribution (2026-05-13) ──
        # Pipeline order: T6 (diarization) now runs BEFORE T7. Read T6's
        # diarization_result.json from R2 and merge pyannote turns into
        # fast_segments so each segment has a `speaker` field. If the bot
        # produced a speaker_events.json (Meet/Teams/Zoom active-speaker panel),
        # use it to map SPEAKER_XX labels to real names. This lifts the fast
        # email from "UNKNOWN" labels to real names.
        # Voice-embedding refinement (ECAPA) still runs in Type 5; fast email
        # uses pyannote's first-pass labels (occasionally corrected later by T5).
        diarization_path = params.get("diarization_path")
        diarization_result: dict | None = None
        attribution_method = "unknown"  # for telemetry log + closed-vocab gate
        n_pyannote_speakers = 0
        if diarization_path:
            try:
                diar_local = temp_dir / "diarization_result.json"
                await storage.download_file(
                    f"{diarization_path}diarization_result.json", str(diar_local)
                )
                diarization_result = json.loads(diar_local.read_text())
            except Exception as e:
                logger.warning(
                    f"Job {job_id}: T7 failed to load diarization_result.json: {e!r} "
                    f"— fast email will use UNKNOWN labels"
                )
                diarization_result = None
        # Track which sources contributed (set independently; not mutually
        # exclusive). attribution_method is the HIGHEST-priority source that
        # contributed at least one name. Priority matches T5's policy:
        #     voice_db (Tier 1) > events_json (Tier 2) > closed_vocab > pyannote_only
        used_events_json = False
        used_voice_db = False

        if diarization_result:
            try:
                from app.audio_preprocess.services.diarize import (
                    assign_speakers_to_segments,
                )
                # Signature: (diarization_result, whisper_segments, audio_path=None, config=None)
                fast_segments = assign_speakers_to_segments(
                    diarization_result, fast_segments
                )
                attribution_method = "pyannote_only"
                diar_segs = diarization_result.get("speakers", []) or []
                n_pyannote_speakers = len({s.get("speaker") for s in diar_segs})

                # ── Build events_json name map (bot panel timing overlap) ──
                input_audio_path = params.get("input_audio_path", "") or ""
                speaker_events = None
                if input_audio_path:
                    parent_dir = input_audio_path.rsplit("/", 1)[0] if "/" in input_audio_path else ""
                    events_remote_key = (
                        f"{parent_dir}/speaker_events.json" if parent_dir
                        else "speaker_events.json"
                    )
                    try:
                        if await storage.exists(events_remote_key):
                            events_local = temp_dir / "speaker_events.json"
                            await storage.download_file(events_remote_key, str(events_local))
                            from app.audio_preprocess.services.speaker_overlap import (
                                load_speaker_events_file,
                            )
                            speaker_events, _v2_participants = load_speaker_events_file(
                                events_local
                            )
                    except Exception as _e:
                        logger.warning(
                            f"Job {job_id}: T7 read speaker_events.json failed (non-fatal): {_e!r}"
                        )

                events_name_map: dict[str, str] = {}
                if speaker_events:
                    try:
                        from app.audio_preprocess.services.speaker_overlap import (
                            compute_speaker_name_map,
                        )
                        # Signature: (segments, speaker_events) — segments here
                        # means the diarization segments (speaker/start/end),
                        # NOT the Whisper segments now decorated with labels.
                        events_name_map = compute_speaker_name_map(
                            diar_segs, speaker_events
                        ) or {}
                        if events_name_map:
                            logger.info(
                                f"Job {job_id}: T7 events_json map → {len(events_name_map)} "
                                f"speaker label(s)"
                            )
                    except Exception as _e:
                        logger.warning(
                            f"Job {job_id}: T7 compute_speaker_name_map failed (non-fatal): {_e!r}"
                        )

                # ── Build voice-print DB name map (ECAPA cosine match) ──
                # Always runs (regardless of events_json result) — matches T5's
                # Tier 1 / Tier 2 policy. voice_db wins for any label present
                # in both maps because voice-fingerprint match against a stored
                # profile is a stronger signal than panel-timing overlap.
                voice_name_map: dict[str, str] = {}
                team_id = params.get("team_id") or ""
                if team_id:
                    try:
                        from app.audio_preprocess.services.voice_embeddings import (
                            extract_all_embeddings,
                            lookup_voice_names,
                        )
                        # ECAPA needs 16 kHz mono — enhanced_16k already exists
                        # from the ASR step above.
                        embeddings_data = await loop.run_in_executor(
                            None,
                            lambda: extract_all_embeddings(
                                str(enhanced_16k), fast_segments
                            ),
                        )
                        if embeddings_data:
                            voice_name_map = await lookup_voice_names(
                                team_id, embeddings_data
                            ) or {}
                            if voice_name_map:
                                logger.info(
                                    f"Job {job_id}: T7 voice_db map → "
                                    f"{len(voice_name_map)} speaker label(s)"
                                )
                    except Exception as _e:
                        logger.warning(
                            f"Job {job_id}: T7 voice-print DB lookup failed "
                            f"(non-fatal): {_e!r}"
                        )

                # ── Merge with voice_db winning over events_json per label ──
                merged_map: dict[str, str] = {}
                all_labels = set(voice_name_map.keys()) | set(events_name_map.keys())
                for label in all_labels:
                    if label in voice_name_map:
                        merged_map[label] = voice_name_map[label]
                    elif label in events_name_map:
                        merged_map[label] = events_name_map[label]

                if merged_map:
                    for seg in fast_segments:
                        label = seg.get("speaker")
                        if label in merged_map:
                            seg["speaker"] = merged_map[label]
                    used_voice_db = bool(voice_name_map)
                    used_events_json = bool(events_name_map)
                    # Highest-priority source that contributed at least one name
                    if voice_name_map:
                        attribution_method = "voice_db"
                    else:
                        attribution_method = "events_json"
                    logger.info(
                        f"Job {job_id}: T7 merged speaker map → "
                        f"{len(merged_map)} label(s) "
                        f"(voice_db={len(voice_name_map)}, events_json={len(events_name_map)})"
                    )

                # ── Closed-vocab fallback flag ──
                # If neither events_json nor voice_db yielded ANY name but
                # participant_names is populated, signal the P5 prompt to ask
                # the LLM to disambiguate from transcript context.
                if (
                    attribution_method == "pyannote_only"
                    and (params.get("participant_names") or [])
                ):
                    attribution_method = "closed_vocab"
            except Exception as e:
                logger.warning(
                    f"Job {job_id}: T7 speaker attribution block failed (non-fatal): {e!r}"
                )

        # Structured telemetry log — grep `T7 speaker attribution method=` to
        # count how each meeting's fast email was labelled. See plan §10.4.
        logger.info(
            f"Job {job_id}: T7 speaker attribution method={attribution_method} "
            f"n_pyannote={n_pyannote_speakers} "
            f"n_participants={len(params.get('participant_names', []) or [])} "
            f"used_events_json={used_events_json} "
            f"used_voice_db={used_voice_db} "
            f"used_events_json_legacy={attribution_method == 'events_json'} "
            f"used_closed_vocab={attribution_method == 'closed_vocab'}"
        )

        # ── Save fast transcript artifacts (R2) ──
        await update_meeting_progress(meeting_id, 7, 60, "Saving fast transcript...")
        data_dir = Path(DATA_DIR).resolve()
        output_dir = data_dir / output_base_path
        output_dir.mkdir(parents=True, exist_ok=True)

        # raw_asr_segments.json — TRULY raw (no vocab). Type 5 reads this and
        # applies its own vocab + LLM correction. /raw-asr endpoint also reads it.
        (output_dir / "raw_asr_segments.json").write_text(
            json.dumps(segments, indent=2)
        )
        # transcript_segments_fast.json — vocab-corrected. UI fallback during
        # the T+7min → T+20min window.
        (output_dir / "transcript_segments_fast.json").write_text(
            json.dumps(fast_segments, indent=2)
        )

        upload_storage = create_storage()
        await _asyncio.gather(
            upload_storage.save_file(
                f"{output_base_path}raw_asr_segments.json",
                str(output_dir / "raw_asr_segments.json"),
            ),
            upload_storage.save_file(
                f"{output_base_path}transcript_segments_fast.json",
                str(output_dir / "transcript_segments_fast.json"),
            ),
        )
        logger.info(f"Job {job_id}: uploaded fast transcript files to R2")

        # ── Fast P5 narrative ──
        await update_meeting_progress(meeting_id, 7, 75, "Generating fast summary...")
        summary_result = None
        try:
            if cfg.INTELLIGENCE_PROVIDER in ("claude", "gemini", "groq", "ollama", "vllm"):
                from app.job_processor.intelligence_pipeline import (
                    run_summary_only, prewarm_claude_for_stage, SUMMARY_SYSTEM,
                )

                # Tier 4.2 — pre-warm Anthropic cache. For Type 7 this is the
                # very first LLM call of the meeting, so prewarming here lets
                # the polished Type 5 P5 (~T+15min later, well within 1h TTL)
                # read from cache instead of writing again.
                try:
                    await prewarm_claude_for_stage(SUMMARY_SYSTEM, None)
                except Exception as _pw_err:
                    logger.debug(f"Job {job_id}: fast P5 prewarm skipped: {_pw_err}")

                # Calendar-provided participant_names act as the only label
                # source. Without diarization there are no SPEAKER_XX → name
                # mappings. The P5 prompt is told the participant set; the LLM
                # may use names attributively but won't have per-turn ownership.
                pipeline_participants = []
                _names = params.get("participant_names", []) or []
                if _names:
                    pipeline_participants = [{"name": n} for n in _names]

                summary_result = await run_summary_only(
                    segments=fast_segments,
                    participants=pipeline_participants,
                    call_date=None,
                    rep_name=None,
                    # Fast-email speaker-attribution (2026-05-13): when the
                    # in-line speaker map could not resolve names via
                    # speaker_events.json, ask the LLM to disambiguate from
                    # transcript content using the participant list as a
                    # closed vocabulary. See plan §10.2.
                    closed_vocab_mode=(attribution_method == "closed_vocab"),
                    participant_names_for_closed_vocab=(params.get("participant_names") or []),
                    pyannote_speaker_count=n_pyannote_speakers,
                )
                if summary_result:
                    logger.info(f"Job {job_id}: fast P5 summary generated")
                else:
                    logger.info(
                        f"Job {job_id}: P5 returned None (transcript too short); "
                        "skipping summary_fast.json + email"
                    )
        except Exception as e:
            logger.warning(f"Job {job_id}: fast P5 summary failed (non-fatal): {e}")

        # ── If we got a summary: save + trigger fast email ──
        if summary_result:
            (output_dir / "summary_fast.json").write_text(
                json.dumps(summary_result, indent=2, default=str)
            )
            await upload_storage.save_file(
                f"{output_base_path}summary_fast.json",
                str(output_dir / "summary_fast.json"),
            )
            logger.info(f"Job {job_id}: uploaded summary_fast.json to R2")

            # POST to /api/internal/meetings/{id}/email-fast-summary
            try:
                await update_meeting_progress(meeting_id, 7, 90, "Sending fast email...")
                webhook_url = params.get("webhook_url", "")
                webhook_headers = params.get("webhook_headers", {})
                if isinstance(webhook_headers, str):
                    webhook_headers = json.loads(webhook_headers)
                service_key = (
                    webhook_headers.get("X-Service-Key", "")
                    if isinstance(webhook_headers, dict)
                    else ""
                )
                if webhook_url and service_key and meeting_id:
                    parsed = urlparse(webhook_url)
                    core_api_base = f"{parsed.scheme}://{parsed.netloc}"
                    fast_url = (
                        f"{core_api_base}/api/internal/meetings/"
                        f"{meeting_id}/email-fast-summary"
                    )
                    async with httpx.AsyncClient(timeout=30.0) as _client:
                        _resp = await _client.post(
                            fast_url, headers={"X-Service-Key": service_key}
                        )
                        if _resp.status_code >= 400:
                            logger.warning(
                                f"Job {job_id}: fast email POST returned "
                                f"{_resp.status_code}: {_resp.text}"
                            )
                        else:
                            logger.info(
                                f"Job {job_id}: fast email triggered: {_resp.json()}"
                            )
            except Exception as e:
                logger.warning(
                    f"Job {job_id}: fast email POST failed (non-fatal): {e}"
                )

        # Clean up local output dir (R2 has the files)
        shutil.rmtree(str(output_dir), ignore_errors=True)

        await update_meeting_progress(meeting_id, 7, 100, "Fast P5 complete")

        # Tier 4.0.c — emit prompt_log for the fast P5 LLM call so core_api
        # can persist it to ai_prompt_log under prompt_name=P5_summary_fast.
        type7_prompt_log: list = []
        if summary_result and isinstance(summary_result, dict):
            for entry in (summary_result.get("prompt_log") or []):
                # Distinguish fast-path entries from polished-path P5 entries
                # in the dashboard.
                e2 = dict(entry)
                e2["prompt_name"] = "P5_summary_fast"
                type7_prompt_log.append(e2)

        word_count = len(transcript_text.split()) if transcript_text else 0
        return {
            "word_count": word_count,
            "speaker_count": 0,
            "language": asr_result.get("language") or "en",
            "segments_path": f"{output_base_path}transcript_segments_fast.json",
            "raw_asr_path": f"{output_base_path}raw_asr_segments.json",
            "summary_path": (
                f"{output_base_path}summary_fast.json" if summary_result else None
            ),
            # Tier 4.0.c — per-call cost data for ai_prompt_log persistence
            # in core_api's _handle_fast_p5_complete.
            "prompt_log": type7_prompt_log,
        }

    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_transcription(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run the full transcription pipeline (legacy, backward compat for old jobs)."""
    from app.audio_preprocess.services.pipeline import run_pipeline

    input_audio_path = params.get("input_audio_path", "")
    output_base_path = params.get("output_base_path", f"meetings/{meeting_id or 'unknown'}/")

    # Resolve input audio file from BotWorker recordings directory
    recordings_dir = Path(BOT_RECORDINGS_DIR).resolve()
    audio_file = recordings_dir / input_audio_path

    # --- Remote media download handling ---
    youtube_url = params.get("youtube_url")
    media_url = params.get("media_url")
    if youtube_url or media_url:
        import asyncio as _asyncio
        from app.services.ingest import create_request_temp_dir

        dl_temp_dir = create_request_temp_dir(f"dl-job-{job_id}")
        try:
            if youtube_url:
                from app.services.ingest import ingest_youtube
                downloaded_path = await ingest_youtube(youtube_url, job_id, dl_temp_dir)
            else:
                from app.services.ingest import ingest_media_url
                downloaded_path = await ingest_media_url(media_url, job_id, dl_temp_dir)

            # Convert to 16kHz mono WAV
            wav_path = dl_temp_dir / "recording.wav"
            proc = await _asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(downloaded_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(wav_path),
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"FFmpeg conversion failed: {stderr_bytes.decode()}")

            # Place WAV at the expected location so the rest of the pipeline works
            audio_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(wav_path), str(audio_file))

            # Upload to cloud storage if configured
            if STORAGE_BACKEND == "s3":
                upload_storage = create_storage()
                await upload_storage.save_file(input_audio_path, str(audio_file))
                logger.info(f"Job {job_id}: uploaded remote audio to cloud storage")

            source = "YouTube" if youtube_url else "URL"
            logger.info(f"Job {job_id}: {source} audio downloaded and converted: {audio_file}")
        finally:
            if dl_temp_dir.exists():
                shutil.rmtree(dl_temp_dir, ignore_errors=True)
    # --- End remote media download handling ---

    await update_progress(job_id, 5, "Downloading audio...")
    if not audio_file.exists() and STORAGE_BACKEND == "s3":
        # Download from cloud storage (R2/S3) when not available locally
        storage = create_storage()
        if await storage.exists(input_audio_path):
            audio_file.parent.mkdir(parents=True, exist_ok=True)
            await storage.download_file(input_audio_path, str(audio_file))
            logger.info(f"Job {job_id}: downloaded audio from cloud storage: {input_audio_path}")
    if not audio_file.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_file}")

    # Create a temp directory for pipeline processing
    temp_dir = Path(f"/tmp/transcription_job_{job_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Copy audio to temp dir
        working_audio = temp_dir / audio_file.name
        shutil.copy2(str(audio_file), str(working_audio))

        # Resolve vocabulary files if vocab_correction is enabled
        vocab_files = None
        if params.get("vocab_correction"):
            from app.audio_preprocess.config import preprocess_config
            vocab_dir = Path(preprocess_config.VOCABULARY_DIR)
            vocab_names = params.get("vocabulary", "")
            if vocab_names:
                vocab_files = [vocab_dir / f"{name.strip()}.json" for name in vocab_names.split(",")]
            else:
                # Load all available vocabulary files
                vocab_files = sorted(vocab_dir.glob("*.json"))

        # Build contextual initial_prompt for better ASR accuracy
        context_prompt = None
        try:
            prompt_parts = []
            _meeting_title = params.get("meeting_title")
            if _meeting_title:
                prompt_parts.append(f"Meeting: {_meeting_title}.")

            # Try to load participant names from speaker_events.json.
            # Primary source: params.participant_names (populated by
            # tab_audio.py /complete from the request body, and by the
            # bot webhook chain from recording_complete). Fallback: read
            # the v2 file's participants array — covers retranscription
            # of older meetings where the params dict didn't carry names.
            _context_names = params.get("participant_names", [])
            if not _context_names:
                _se_path = audio_file.parent / "speaker_events.json"
                if not _se_path.exists() and STORAGE_BACKEND == "s3":
                    _audio_dir = audio_file.parent.name
                    _se_key = f"{_audio_dir}/speaker_events.json" if _audio_dir else None
                    if _se_key:
                        try:
                            _se_storage = create_storage()
                            if await _se_storage.exists(_se_key):
                                _se_path.parent.mkdir(parents=True, exist_ok=True)
                                await _se_storage.download_file(_se_key, str(_se_path))
                        except Exception:
                            pass
                if _se_path.exists():
                    from app.audio_preprocess.services.speaker_overlap import (
                        load_speaker_events_file,
                    )
                    _events, _v2_participants = load_speaker_events_file(_se_path)
                    _seen = set()
                    # Prefer the v2 participants array (full roster including
                    # attendees who never spoke). Falls back to extracting
                    # names from event records when participants is empty
                    # (v1 files OR v2 with no DOM-scraped roster).
                    for p in _v2_participants:
                        if isinstance(p, dict):
                            name = p.get("display_name") or p.get("name")
                        else:
                            name = str(p) if p else None
                        if name and name not in _seen:
                            _seen.add(name)
                            _context_names.append(name)
                    for evt in _events:
                        if not isinstance(evt, dict):
                            continue
                        name = evt.get("name") or evt.get("speaker")
                        if name and name not in _seen:
                            _seen.add(name)
                            _context_names.append(name)

            if _context_names:
                prompt_parts.append(f"Participants: {', '.join(_context_names[:10])}.")

            if prompt_parts:
                prompt_parts.append("Proper punctuation and capitalization. Use exact participant names when transcribing speech.")
                context_prompt = " ".join(prompt_parts)
        except Exception as e:
            logger.debug(f"Job {job_id}: failed to build context prompt: {e}")

        # Run the transcription pipeline
        pipeline_result = await run_pipeline(
            input_file=working_audio,
            temp_dir=temp_dir,
            processing_mode=_override_mode or params.get("processing_mode", "path_b"),
            language=params.get("language"),
            engine=params.get("engine", "faster-whisper"),
            model_size=_override_model or params.get("model_size", "medium"),
            phone_mode=params.get("phone_mode", True),
            multilingual=params.get("multilingual", False),
            diarize=(_override_diarize.lower() == "true") if _override_diarize else params.get("diarization", False),
            align=True,
            vocab_correction=params.get("vocab_correction", False),
            vocabulary_files=vocab_files,
            llm_correction=params.get("llm_correction", False),
            enable_intelligence=params.get("enable_intelligence", False),
            post_processing_type=params.get("post_processing_type", "phase7"),
            initial_prompt=context_prompt,
            job_id=job_id,
        )

        # Extract transcript text and segments from PipelineResult attributes
        transcript_text = ""
        segments = []
        speaker_count = 0
        language_detected = "en"

        mode = params.get("processing_mode", "path_b")
        if mode in ("path_b", "both") and pipeline_result.path_b_transcription:
            transcript_text = pipeline_result.path_b_transcription
            segments = pipeline_result.path_b_segments
        elif pipeline_result.path_a_transcription:
            transcript_text = pipeline_result.path_a_transcription
            segments = pipeline_result.path_a_segments

        # Detect language from segments
        if segments:
            for seg in segments:
                if isinstance(seg, dict) and seg.get("detected_language"):
                    language_detected = seg["detected_language"]
                    break

        if segments:
            speaker_ids = {s.get("speaker") for s in segments if isinstance(s, dict) and s.get("speaker")}
            speaker_count = len(speaker_ids)

        word_count = len(transcript_text.split()) if transcript_text else 0

        # Save results to main storage (DATA_DIR)
        data_dir = Path(DATA_DIR).resolve()
        output_dir = data_dir / output_base_path
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save transcript text
        transcript_path = output_dir / "transcript.txt"
        transcript_path.write_text(transcript_text)

        # Save segments
        segments_path = output_dir / "transcript_segments.json"
        segments_path.write_text(json.dumps(segments, indent=2))

        # Save full pipeline result (large file — stored in storage, not DB)
        full_result_path = output_dir / "pipeline_result.json"
        full_result_data = {}
        if hasattr(pipeline_result, "to_dict"):
            full_result_data = pipeline_result.to_dict()
        else:
            # Fallback: serialize what we can
            full_result_data = {
                "transcript": transcript_text,
                "segments": segments,
                "language": language_detected,
            }
        full_result_path.write_text(json.dumps(full_result_data, indent=2, default=str))

        # Save analysis.json with coaching metrics + intelligence/memoir results
        analysis_data = {}
        if hasattr(pipeline_result, "coaching_metrics") and pipeline_result.coaching_metrics:
            analysis_data["coaching_metrics"] = pipeline_result.coaching_metrics
        if hasattr(pipeline_result, "intelligence_result") and pipeline_result.intelligence_result:
            analysis_data["intelligence"] = pipeline_result.intelligence_result
        if hasattr(pipeline_result, "intelligence_time"):
            analysis_data["intelligence_time"] = pipeline_result.intelligence_time
        if hasattr(pipeline_result, "post_processing_type"):
            analysis_data["post_processing_type"] = pipeline_result.post_processing_type

        analysis_path = output_dir / "analysis.json"
        analysis_path.write_text(json.dumps(analysis_data, indent=2, default=str))

        await update_progress(job_id, 70, "Resolving speakers...")
        # Compute speaker_name_map BEFORE intelligence pipeline so LLM gets real names
        speaker_name_map = {}
        try:
            recording_parent = audio_file.parent
            se_path = recording_parent / "speaker_events.json"
            # Download speaker_events from cloud storage if not local
            # Orchestrator uploads as {job_id}/speaker_events.json (same prefix as recording)
            if not se_path.exists() and STORAGE_BACKEND == "s3":
                # Derive storage key from audio path: {job_id}/merged.wav -> {job_id}/speaker_events.json
                audio_dir_name = audio_file.parent.name  # job_id directory
                se_storage_key = f"{audio_dir_name}/speaker_events.json" if audio_dir_name else None
                if se_storage_key:
                    try:
                        se_storage = create_storage()
                        if await se_storage.exists(se_storage_key):
                            se_path.parent.mkdir(parents=True, exist_ok=True)
                            await se_storage.download_file(se_storage_key, str(se_path))
                            logger.info(f"Job {job_id}: downloaded speaker_events.json from cloud storage")
                    except Exception as e:
                        logger.warning(f"Job {job_id}: failed to download speaker_events from storage: {e}")
            if se_path.exists():
                from app.audio_preprocess.services.speaker_overlap import (
                    load_speaker_events_file,
                    compute_speaker_name_map,
                )
                se_events, _v2_participants = load_speaker_events_file(se_path)
                if se_events and segments:
                    speaker_name_map = compute_speaker_name_map(segments, se_events)
        except Exception as e:
            logger.warning(f"Job {job_id}: early speaker_name_map failed (non-fatal): {e}")

        # Extract voice embeddings BEFORE P5 so we can look up names
        await update_progress(job_id, 75, "Extracting voice embeddings...")
        embeddings_path = output_dir / "embeddings.json"
        try:
            from app.audio_preprocess.services.voice_embeddings import (
                extract_all_embeddings,
                save_embeddings,
            )
            if segments:
                embeddings = extract_all_embeddings(str(working_audio), segments)
                if embeddings:
                    save_embeddings(embeddings, str(embeddings_path))
                    logger.info(f"Job {job_id}: extracted voice embeddings for {len(embeddings)} speakers")
        except Exception as e:
            logger.warning(f"Job {job_id}: voice embedding extraction failed (non-fatal): {e}")

        # Look up voice embedding names from DB (READ-ONLY, best-effort)
        voice_name_map = {}
        team_id = params.get("team_id")
        if team_id and embeddings_path.exists():
            try:
                from app.audio_preprocess.services.voice_embeddings import lookup_voice_names
                emb_data = json.loads(embeddings_path.read_text())
                voice_name_map = await lookup_voice_names(team_id, emb_data)
                if voice_name_map:
                    logger.info(f"Job {job_id}: voice lookup matched {len(voice_name_map)} speakers: {voice_name_map}")
            except Exception as e:
                logger.warning(f"Job {job_id}: voice name lookup failed (non-fatal): {e}")

        # Replace SPEAKER_XX labels with resolved names for P5 summary
        # Priority: voice embedding name > overlap name > SPEAKER_XX
        resolved_segments = []
        for seg in segments:
            new_seg = dict(seg)
            label = new_seg.get("speaker", "")
            if label in voice_name_map:
                new_seg["speaker"] = voice_name_map[label]
            elif label in speaker_name_map:
                new_seg["speaker"] = speaker_name_map[label]
            resolved_segments.append(new_seg)

        # Run P5 summary only for fast-path email delivery (best-effort)
        await update_progress(job_id, 80, "Generating summary...")
        summary_result = None
        try:
            from app.audio_preprocess.config import preprocess_config
            if preprocess_config.INTELLIGENCE_PROVIDER in ("claude", "gemini", "groq", "ollama", "vllm"):
                from app.job_processor.intelligence_pipeline import run_summary_only

                # Build participant list from voice names, overlap names, or speaker_events
                pipeline_participants = []
                if voice_name_map:
                    pipeline_participants = [{"name": name} for name in voice_name_map.values()]
                elif speaker_name_map:
                    pipeline_participants = [{"name": name} for name in speaker_name_map.values()]
                else:
                    try:
                        recording_parent_intel = audio_file.parent
                        se_path_intel = recording_parent_intel / "speaker_events.json"
                        if se_path_intel.exists():
                            se_data_intel = json.loads(se_path_intel.read_text())
                            seen_names = set()
                            for evt in (se_data_intel if isinstance(se_data_intel, list) else []):
                                name = evt.get("name") or evt.get("speaker")
                                if name and name not in seen_names:
                                    seen_names.add(name)
                                    pipeline_participants.append({"name": name})
                    except Exception:
                        pass

                summary_result = await run_summary_only(
                    segments=resolved_segments,
                    participants=pipeline_participants,
                    call_date=None,
                    rep_name=None,
                )

                if summary_result:
                    summary_path = output_dir / "summary.json"
                    summary_path.write_text(json.dumps(summary_result, indent=2, default=str))
                    logger.info(f"Job {job_id}: summary.json saved")
        except Exception as e:
            logger.warning(f"Job {job_id}: P5 summary generation failed (non-fatal): {e}")

        # (Voice embeddings already extracted above, before P5 summary)

        # Copy speaker_events.json from recordings dir and save pre-computed speaker name map (best-effort)
        try:
            recording_parent_copy = audio_file.parent
            speaker_events_src = recording_parent_copy / "speaker_events.json"
            if speaker_events_src.exists():
                speaker_events_dst = output_dir / "speaker_events.json"
                shutil.copy2(str(speaker_events_src), str(speaker_events_dst))
                logger.info(f"Job {job_id}: copied speaker_events.json to output")

                # Save the speaker_name_map computed earlier (before intelligence pipeline)
                if speaker_name_map:
                    name_map_path = output_dir / "speaker_name_map.json"
                    name_map_path.write_text(json.dumps(speaker_name_map, indent=2))
                    logger.info(
                        f"Job {job_id}: speaker name map saved "
                        f"({len(speaker_name_map)} labels mapped)"
                    )
        except Exception as e:
            logger.warning(f"Job {job_id}: speaker events processing failed (non-fatal): {e}")

        # Copy recording to meeting-level path (not inside version subfolder).
        # output_base_path may be "meetings/{id}/v2/" — save recording one level up
        # at "meetings/{id}/recording.wav".
        meeting_base = output_base_path.rstrip("/")
        # Strip version subfolder if present (e.g. "meetings/{id}/v2" -> "meetings/{id}")
        import re
        if re.search(r"/v\d+$", meeting_base):
            meeting_base = meeting_base.rsplit("/", 1)[0]
        recording_rel_path = f"{meeting_base}/recording.wav"
        recording_output = data_dir / recording_rel_path
        recording_output.parent.mkdir(parents=True, exist_ok=True)
        if not recording_output.exists():
            shutil.copy2(str(audio_file), str(recording_output))

        # Upload all results to cloud storage (R2/S3) if configured
        await update_progress(job_id, 95, "Uploading results...")
        if STORAGE_BACKEND == "s3":
            try:
                upload_storage = create_storage()
                for filename in [
                    "transcript.txt", "transcript_segments.json", "analysis.json",
                    "pipeline_result.json", "summary.json",
                    "embeddings.json", "speaker_events.json", "speaker_name_map.json",
                ]:
                    local_file = output_dir / filename
                    if local_file.exists():
                        await upload_storage.save_file(f"{output_base_path}{filename}", str(local_file))

                # Upload recording.wav
                if recording_output.exists():
                    await upload_storage.save_file(recording_rel_path, str(recording_output))

                logger.info(f"Job {job_id}: uploaded results to cloud storage")

                # Clean up ALL local files after successful R2 upload
                # 1. Output dir (DATA_DIR/meetings/{id}/v1/) — results now in R2
                shutil.rmtree(str(output_dir), ignore_errors=True)
                # 2. Recording source dir (BOT_RECORDINGS_DIR/{jobId}/ or uploads/{id}/)
                if audio_file.parent != recordings_dir:
                    shutil.rmtree(str(audio_file.parent), ignore_errors=True)
                # 3. Meeting-level recording.wav (DATA_DIR/meetings/{id}/recording.wav)
                if recording_output.exists():
                    recording_output.unlink(missing_ok=True)
                # 4. Clean empty parent dirs
                try:
                    recording_output.parent.rmdir()  # Only removes if empty
                except OSError:
                    pass
                logger.info(f"Job {job_id}: cleaned up local files after R2 upload")
            except Exception as e:
                logger.error(f"Job {job_id}: failed to upload results to cloud storage: {e}")
                raise

        await update_progress(job_id, 100, "Complete")
        await clear_progress(job_id)

        result_dict = {
            "word_count": word_count,
            "speaker_count": speaker_count,
            "language": language_detected,
            "transcript_path": f"{output_base_path}transcript.txt",
            "segments_path": f"{output_base_path}transcript_segments.json",
            "recording_path": recording_rel_path,
            "full_result_path": f"{output_base_path}pipeline_result.json",
            "analysis_path": f"{output_base_path}analysis.json",
        }
        if summary_result:
            result_dict["summary_path"] = f"{output_base_path}summary.json"
        return result_dict

    finally:
        # Cleanup temp dir
        shutil.rmtree(str(temp_dir), ignore_errors=True)


async def _run_crm_extraction(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run CRM extraction (P2 prompt) by reading segments from storage."""
    from app.job_processor.intelligence_pipeline import (
        get_provider,
        format_transcript_for_llm,
        extract_metadata,
        run_crm_extraction,
    )

    await update_meeting_progress(meeting_id, 2, 5, "Loading transcript...")

    # 1. Read segments from shared storage
    segments_path = params.get("segments_path", "")
    seg_file = Path(DATA_DIR).resolve() / segments_path
    if not seg_file.exists() and STORAGE_BACKEND == "s3":
        seg_storage = create_storage()
        if await seg_storage.exists(segments_path):
            seg_file.parent.mkdir(parents=True, exist_ok=True)
            await seg_storage.download_file(segments_path, str(seg_file))
            logger.info(f"Job {job_id}: downloaded segments from cloud storage: {segments_path}")
    if not seg_file.exists():
        raise FileNotFoundError(f"Segments file not found: {seg_file}")
    segments = json.loads(seg_file.read_text())

    if not segments or not isinstance(segments, list):
        raise ValueError("Transcript segments are empty")

    await update_meeting_progress(meeting_id, 2, 15, "Preparing transcript for CRM extraction...")

    # 2. Apply speaker_map (so LLM gets real names)
    speaker_map = params.get("speaker_map", {})
    for seg in segments:
        label = seg.get("speaker", "")
        if label in speaker_map:
            mapped = speaker_map[label]
            if isinstance(mapped, dict):
                seg["speaker"] = mapped.get("name", label)
            elif isinstance(mapped, str):
                seg["speaker"] = mapped

    await update_meeting_progress(meeting_id, 2, 25, "Formatting transcript for LLM...")

    # 3. Run P2 prompt
    provider = get_provider()
    if provider is None:
        raise RuntimeError("Intelligence provider not configured")

    await update_meeting_progress(meeting_id, 2, 35, "Initializing CRM extraction model...")

    formatted = format_transcript_for_llm(segments)
    metadata = extract_metadata(segments)

    if metadata.get("word_count", 0) < 50:
        logger.info(f"Job {job_id}: transcript too short for CRM extraction ({metadata.get('word_count', 0)} words), skipping")
        await update_meeting_progress(meeting_id, 2, 100, "Skipped — transcript too short")
        return {"crm_extraction": None, "skipped": True, "reason": "transcript_too_short"}

    await update_meeting_progress(meeting_id, 2, 45, "Extracting CRM fields from call data...")

    crm_data = await run_crm_extraction(
        provider,
        formatted,
        metadata,
        params.get("prospect_name"),
        params.get("prospect_company"),
        params.get("call_date"),
        params.get("rep_name"),
    )

    if crm_data is None:
        raise RuntimeError("CRM extraction returned no result")

    await update_meeting_progress(meeting_id, 2, 85, "Processing CRM results...")

    # Capture P2 prompt stats for cost logging in core_api
    last_stats = provider.get_last_call_stats() or {}
    prompt_log_entry = {
        "prompt_number": 2,
        "prompt_name": "P2_crm_extraction",
        "provider": last_stats.get("provider", "unknown"),
        "model": last_stats.get("model", "unknown"),
        "input_tokens": last_stats.get("input_tokens", 0),
        "output_tokens": last_stats.get("output_tokens", 0),
        "cache_read_tokens": last_stats.get("cache_read_tokens", 0),
        "cost_usd": last_stats.get("cost_usd", 0.0),
        "duration_ms": last_stats.get("duration_ms", 0),
        "parse_success": last_stats.get("parse_success", True),
        "validation_issues": None,
    }

    await update_meeting_progress(meeting_id, 2, 100, "CRM sync complete")
    logger.info(f"Job {job_id}: CRM extraction completed for meeting {meeting_id}")
    return {"crm_extraction": crm_data, "prompt_log": [prompt_log_entry]}


async def _run_intelligence(job_id: str, params: dict, meeting_id: str | None) -> dict:
    """Run intelligence pipeline (P1-P4, P6) by reading segments from storage."""
    from app.job_processor.intelligence_pipeline import (
        run_full_pipeline, prewarm_claude_for_stage, SUMMARY_SYSTEM,
    )

    await update_meeting_progress(meeting_id, 3, 0, "Loading transcript...")

    # Tier 4.2 — pre-warm Anthropic cache. The intelligence pipeline runs
    # multiple LLM calls (P1-P4, P6) with distinct system prompts; pre-warming
    # the SUMMARY_SYSTEM (which is the largest static prompt) primes one
    # cache entry. The other prompts will write their own cache on first use.
    # No-op for Ollama/Gemini/Groq.
    try:
        await prewarm_claude_for_stage(SUMMARY_SYSTEM, None)
    except Exception as _pw_err:
        logger.debug(f"Job {job_id}: Type 3 prewarm skipped: {_pw_err}")

    segments_path = params.get("segments_path", "")
    seg_file = Path(DATA_DIR).resolve() / segments_path
    if not seg_file.exists() and STORAGE_BACKEND == "s3":
        seg_storage = create_storage()
        if await seg_storage.exists(segments_path):
            seg_file.parent.mkdir(parents=True, exist_ok=True)
            await seg_storage.download_file(segments_path, str(seg_file))
            logger.info(f"Job {job_id}: downloaded segments from cloud storage: {segments_path}")
    if not seg_file.exists():
        raise FileNotFoundError(f"Segments file not found: {seg_file}")
    segments = json.loads(seg_file.read_text())

    if not segments or not isinstance(segments, list):
        raise ValueError("Transcript segments are empty")

    speaker_map = params.get("speaker_map", {})
    for seg in segments:
        label = seg.get("speaker", "")
        if label in speaker_map:
            mapped = speaker_map[label]
            if isinstance(mapped, dict):
                seg["speaker"] = mapped.get("name", label)
            elif isinstance(mapped, str):
                seg["speaker"] = mapped

    pipeline_participants = []
    for name in params.get("participant_names", []):
        pipeline_participants.append({"name": name})

    pipeline_team_id = params.get("team_id")

    await update_meeting_progress(meeting_id, 3, 5, "Loading AI models...")

    intelligence_result = await run_full_pipeline(
        segments=segments,
        participants=pipeline_participants,
        call_date=params.get("call_date"),
        team_id=pipeline_team_id,
        meeting_id=meeting_id,
        skip_summary=True,
        job_id=job_id,
    )

    if not intelligence_result:
        raise RuntimeError("Intelligence pipeline returned no result")

    await update_meeting_progress(meeting_id, 3, 75, "Intelligence analysis complete")

    output_base_path = params.get("output_base_path", f"meetings/{meeting_id}/v1/")
    output_dir = Path(DATA_DIR).resolve() / output_base_path
    output_dir.mkdir(parents=True, exist_ok=True)

    intel_path = output_dir / "intelligence.json"
    intel_path.write_text(json.dumps(intelligence_result, indent=2, default=str))
    logger.info(
        f"Job {job_id}: intelligence.json saved "
        f"(type={intelligence_result.get('call_type')}, "
        f"provider={intelligence_result.get('provider', 'unknown')})"
    )

    # Run Phase 10 Memoir extraction on speaker-mapped segments
    await update_meeting_progress(meeting_id, 3, 78, "Generating meeting summary...")
    try:
        from app.audio_preprocess.services.memoir_extractor import (
            run_memoir_extraction, MEMOIR_SYSTEM_PROMPT,
        )
        from app.audio_preprocess.config import preprocess_config as cfg
        from app.job_processor.intelligence_pipeline import prewarm_claude_for_stage

        # Tier 4.2 — pre-warm Anthropic cache for the memoir system prompt
        # before running. Ollama/Gemini/Groq is no-op.
        try:
            await prewarm_claude_for_stage(MEMOIR_SYSTEM_PROMPT, None)
        except Exception as _pw_err:
            logger.debug(f"Job {job_id}: memoir prewarm skipped: {_pw_err}")

        total_duration = max((seg.get("end", 0) for seg in segments), default=0)
        num_speakers = len(set(seg.get("speaker", "") for seg in segments if seg.get("speaker")))

        # Tier 4.2 — build a deterministic team-static prefix for 3-layer
        # Claude caching. Combines CRM contacts (already in params via
        # Tier 1.4) and known participants. Deterministic order is critical
        # for cache key stability — different ordering = different hash.
        # Falls through to None on empty inputs (single-layer caching).
        team_static_block = _build_memoir_team_static_block(params)

        memoir_result = run_memoir_extraction(
            segments=segments,
            total_duration=total_duration,
            num_speakers=num_speakers,
            language="en",
            base_url=cfg.LLM_BASE_URL,
            model=cfg.INTELLIGENCE_LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            timeout=cfg.LLM_TIMEOUT,
            max_single_pass_tokens=cfg.INTELLIGENCE_MAX_SINGLE_PASS_TOKENS,
            num_ctx=cfg.INTELLIGENCE_NUM_CTX,
            team_static_block=team_static_block,
        )

        if memoir_result:
            # Tier 4.0.b — extract memoir extractor's prompt_log BEFORE
            # writing intelligence.json (drop the underscore-prefixed key
            # so the on-disk file stays clean of internal cost data).
            memoir_prompt_log = memoir_result.pop("_prompt_log", []) or []
            intelligence_result["memoir"] = memoir_result
            intel_path.write_text(json.dumps(intelligence_result, indent=2, default=str))
            logger.info(f"Job {job_id}: Memoir extraction completed and added to intelligence.json")
            # Append to the intelligence prompt_log list so Type 3's
            # pipeline_cost.json carries memoir entries through to
            # core_api's _handle_intelligence_complete + store_all_costs.
            existing_log = intelligence_result.get("prompt_log") or []
            intelligence_result["prompt_log"] = list(existing_log) + memoir_prompt_log
    except Exception as e:
        logger.warning(f"Job {job_id}: Memoir extraction failed (non-fatal): {e}")

    import os as _os
    gpu_type = _os.environ.get("TRANSCRIPTION_GPU_TYPE", "CPU")
    pipeline_cost = {
        "intelligence_provider": intelligence_result.get("provider", "unknown"),
        "intelligence_processing_seconds": intelligence_result.get("total_seconds", 0),
        "prompt_log": intelligence_result.get("prompt_log", []),
        "asr_gpu_type": gpu_type,
    }
    cost_path = output_dir / "pipeline_cost.json"
    cost_path.write_text(json.dumps(pipeline_cost, indent=2))

    await update_meeting_progress(meeting_id, 3, 90, "Uploading results...")

    if STORAGE_BACKEND == "s3":
        upload_storage = create_storage()
        for filename in ["intelligence.json", "pipeline_cost.json"]:
            local = output_dir / filename
            if local.exists():
                await upload_storage.save_file(f"{output_base_path}{filename}", str(local))
        for filename in ["intelligence.json", "pipeline_cost.json"]:
            local = output_dir / filename
            if local.exists():
                local.unlink()

    await update_meeting_progress(meeting_id, 3, 100, "AI analysis complete")

    logger.info(f"Job {job_id}: intelligence pipeline completed for meeting {meeting_id}")
    return {
        "intelligence_path": f"{output_base_path}intelligence.json",
        "pipeline_cost_path": f"{output_base_path}pipeline_cost.json",
    }


async def _call_webhook(params: dict, payload: dict) -> None:
    """Call the webhook URL with the given payload."""
    webhook_url = params.get("webhook_url")
    if not webhook_url:
        logger.warning("No webhook_url in params, skipping webhook call")
        return

    webhook_headers = params.get("webhook_headers", {})
    if isinstance(webhook_headers, str):
        import json as _json
        webhook_headers = _json.loads(webhook_headers)
    headers = {"Content-Type": "application/json"}
    headers.update(webhook_headers)
    logger.info(f"Webhook headers: {list(headers.keys())}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(webhook_url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"Webhook returned {resp.status_code}: {resp.text[:200]}"
                )
            else:
                logger.info(f"Webhook called successfully: {webhook_url}")
    except Exception as e:
        logger.error(f"Webhook call failed: {e}")
