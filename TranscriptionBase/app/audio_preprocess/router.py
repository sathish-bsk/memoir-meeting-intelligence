"""
FastAPI router for the audio preprocessing pipeline.
Mounted at /audio-preprocess in the main app.
"""
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from pydantic import BaseModel

from app.audio_preprocess.config import preprocess_config
from app.audio_preprocess.services.enhance_playback import check_enhance_available
from app.audio_preprocess.services.pipeline import run_pipeline
from app.audio_preprocess.ui import get_html_page
from app.audio_preprocess.postprocess_ui import get_postprocess_page
from app.audio_preprocess.vocabulary_ui import get_vocabulary_page

logger = logging.getLogger("audio_preprocess")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/audio-preprocess", tags=["audio-preprocess"])

# Store results per request for serving audio files
_results_store: dict = {}


def _get_vocab_dir() -> Path:
    """Get the vocabulary directory, creating it if needed."""
    d = Path(preprocess_config.VOCABULARY_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_vocab_path(name: str) -> Path:
    """Resolve a vocabulary name to its JSON file path."""
    # Sanitize: only allow alphanumeric, dash, underscore
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid vocabulary name")
    return _get_vocab_dir() / f"{safe}.json"


# ═══════════════════════════════════════════════════════════════════
# Main Processing Page + Pipeline
# ═══════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def preprocess_home():
    """Serve the audio preprocessing comparison UI."""
    return get_html_page()


@router.post("/process")
async def process_audio(
    file: UploadFile = File(...),
    processing_mode: str = Form("both"),
    language: str = Form("auto"),
    engine: str = Form("faster-whisper"),
    model_size: str = Form("medium"),
    multilingual: str = Form("false"),
    diarization: str = Form("false"),
    num_speakers: str = Form("auto"),
    alignment: str = Form("false"),
    translate_to_english: str = Form("false"),
    vocab_correction: str = Form("false"),
    vocabulary: str = Form(""),
    llm_correction: str = Form("false"),
    enable_intelligence: str = Form("false"),
    phone_mode: str = Form("false"),
    post_processing_type: str = Form("phase7"),
):
    """
    Process uploaded audio through the pipeline.
    processing_mode: 'path_a' (smart only), 'path_b' (resemble only), or 'both'.
    language: ISO 639-1 code or 'auto' for auto-detection.
    engine: 'openai-whisper' or 'faster-whisper'.
    model_size: 'tiny', 'base', 'small', 'medium', or 'large-v3'.
    multilingual: 'true' to enable fine-grained LID + dual-pass fallback.
    diarization: 'true' to enable speaker diarization (Pyannote).
    num_speakers: 'auto' or integer string ('2', '3', etc.) for expected speaker count.
    translate_to_english: 'true' to translate non-English segments to English.
    vocabulary: comma-separated vocabulary names (e.g. 'saas_starter,enterprise_starter').
    phone_mode: 'true' to enable sensitivity adjustments for phone/VoIP recordings.
    post_processing_type: 'phase7' for Phase 7 Intelligence or 'memoir' for Memoir Summary.
    """
    request_id = uuid.uuid4().hex[:12]
    temp_dir = Path(preprocess_config.TEMP_DIR) / request_id

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded file
        input_path = temp_dir / file.filename
        with open(str(input_path), "wb") as f:
            content = await file.read()
            f.write(content)

        # Validate processing_mode
        if processing_mode not in ("path_a", "path_b", "both"):
            processing_mode = "both"

        # Validate engine
        if engine not in ("openai-whisper", "faster-whisper"):
            engine = "faster-whisper"

        # Validate model_size
        if model_size not in ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"):
            model_size = "medium"

        # Convert 'auto' to None for Whisper auto-detection
        whisper_lang = None if language == "auto" else language

        # Parse multilingual flag (form sends strings)
        is_multilingual = multilingual.lower() in ("true", "1", "yes", "on")
        if is_multilingual and language != "auto":
            is_multilingual = False
            logger.warning(f"[{request_id}] Multilingual mode disabled: requires language='auto'")

        # Parse diarization flags
        is_diarize = diarization.lower() in ("true", "1", "yes", "on")
        speaker_count = 0 if num_speakers == "auto" else int(num_speakers)

        # Parse alignment flag
        is_align = alignment.lower() in ("true", "1", "yes", "on")

        # Parse translation flag
        is_translate = translate_to_english.lower() in ("true", "1", "yes", "on")

        # Parse vocabulary correction flags
        is_vocab_correction = vocab_correction.lower() in ("true", "1", "yes", "on")
        is_llm_correction = llm_correction.lower() in ("true", "1", "yes", "on")

        # Parse intelligence flag
        is_intelligence = enable_intelligence.lower() in ("true", "1", "yes", "on")

        # Parse phone mode flag
        is_phone_mode = phone_mode.lower() in ("true", "1", "yes", "on")

        # Validate post-processing type
        pp_type = post_processing_type if post_processing_type in ("phase7", "memoir") else "phase7"

        # Resolve vocabulary file paths (supports comma-separated names)
        vocab_files = []
        if is_vocab_correction and vocabulary and vocabulary.strip():
            for vname in vocabulary.split(","):
                vname = vname.strip()
                if not vname:
                    continue
                vocab_path = _resolve_vocab_path(vname)
                if vocab_path.exists():
                    vocab_files.append(str(vocab_path))
                else:
                    logger.warning(f"[{request_id}] Vocabulary '{vname}' not found at {vocab_path}")

        logger.info(f"[{request_id}] Processing: {file.filename} ({len(content)} bytes), mode={processing_mode}, lang={language}, engine={engine}, model={model_size}, multilingual={is_multilingual}, diarization={is_diarize}, num_speakers={speaker_count}, alignment={is_align}, translate={is_translate}, vocab_correction={is_vocab_correction}, vocabulary={vocabulary or 'none'}, llm_correction={is_llm_correction}, intelligence={is_intelligence}, phone_mode={is_phone_mode}, post_processing_type={pp_type}")

        # Run the pipeline with selected options
        result = await run_pipeline(input_path, temp_dir, processing_mode=processing_mode, language=whisper_lang, engine=engine, model_size=model_size, multilingual=is_multilingual, diarize=is_diarize, num_speakers=speaker_count, align=is_align, translate_to_english=is_translate, vocabulary_files=vocab_files, vocab_correction=is_vocab_correction, llm_correction=is_llm_correction, enable_intelligence=is_intelligence, phone_mode=is_phone_mode, post_processing_type=pp_type)

        # Store temp_dir for audio file serving
        _results_store[request_id] = str(temp_dir)

        response = result.to_dict()
        response["request_id"] = request_id

        return JSONResponse(content=response)

    except Exception as e:
        logger.exception(f"[{request_id}] Pipeline error: {e}")
        # Cleanup on error
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir), ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════
# Audio Serving / Fonts / Enhance Status / Cleanup
# ═══════════════════════════════════════════════════════════════════

@router.get("/audio/{request_id}/{filename}")
async def serve_audio(request_id: str, filename: str):
    """Serve processed audio files for playback in the UI."""
    temp_dir = _results_store.get(request_id)
    if not temp_dir:
        raise HTTPException(status_code=404, detail="Audio not found")

    file_path = Path(temp_dir) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(str(file_path), media_type="audio/wav", filename=filename)


_FONTS_DIR = Path(__file__).parent / "static" / "fonts"


@router.get("/fonts/{filename}")
async def serve_font(filename: str):
    """Serve bundled font files for PDF generation."""
    if filename not in ("NotoSansTamil-Regular.ttf", "NotoSansDevanagari-Regular.ttf"):
        raise HTTPException(status_code=404, detail="Font not found")
    font_path = _FONTS_DIR / filename
    if not font_path.exists():
        raise HTTPException(status_code=404, detail="Font file not found")
    return FileResponse(str(font_path), media_type="font/ttf", filename=filename)


@router.get("/enhance-status")
async def enhance_status():
    """Check if the Resemble Enhance microservice is available."""
    available = check_enhance_available(preprocess_config.RESEMBLE_ENHANCE_URL)
    return {"available": available, "url": preprocess_config.RESEMBLE_ENHANCE_URL}


@router.get("/llm-status")
async def llm_status():
    """Check if Ollama LLM service is available for Phase 5b correction."""
    from app.audio_preprocess.services.llm_corrector import check_ollama_available
    available = check_ollama_available(preprocess_config.LLM_BASE_URL)
    return {
        "available": available,
        "url": preprocess_config.LLM_BASE_URL,
        "model": preprocess_config.LLM_MODEL,
    }


@router.get("/intelligence-status")
async def intelligence_status():
    """Check if Ollama LLM service is available for Phase 7 intelligence extraction."""
    from app.audio_preprocess.services.intelligence import check_intelligence_llm_available
    status = check_intelligence_llm_available(preprocess_config.LLM_BASE_URL)
    return {
        "available": status["available"],
        "url": preprocess_config.LLM_BASE_URL,
        "model": preprocess_config.INTELLIGENCE_LLM_MODEL,
        "models": status.get("models", []),
    }


# ═══════════════════════════════════════════════════════════════════
# Voice Embedding Extraction (for manual speaker edits)
# ═══════════════════════════════════════════════════════════════════


class VoiceEmbeddingRequest(BaseModel):
    recording_path: str  # Relative to DATA_DIR, e.g. "meetings/{id}/recording.wav"
    start: float
    end: float


@router.post("/voice-embeddings/extract")
async def extract_voice_embedding(body: VoiceEmbeddingRequest):
    """Extract a single voice embedding from a recording segment.

    Used by core_api for manual speaker correction — extracts and returns
    the embedding without storing it (core_api handles DB storage).
    """
    from app.audio_preprocess.services.voice_embeddings import (
        MIN_DURATION_SECONDS,
        extract_embedding,
    )
    from app.job_processor.config import DATA_DIR

    duration = body.end - body.start
    if duration < MIN_DURATION_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"Segment too short ({duration:.1f}s < {MIN_DURATION_SECONDS}s minimum)",
        )

    # Resolve recording path relative to DATA_DIR
    data_dir = Path(DATA_DIR).resolve()
    audio_path = data_dir / body.recording_path
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail=f"Recording not found: {body.recording_path}")

    try:
        embedding = extract_embedding(str(audio_path), body.start, body.end)
    except Exception as e:
        logger.error(f"Voice embedding extraction failed: {e}")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    if embedding is None:
        raise HTTPException(status_code=422, detail="Could not extract embedding from segment")

    return {"embedding": embedding, "duration_seconds": duration}


@router.delete("/cleanup/{request_id}")
async def cleanup(request_id: str):
    """Clean up temp files for a request."""
    temp_dir = _results_store.pop(request_id, None)
    if temp_dir and Path(temp_dir).exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    return {"status": "cleaned"}


# ═══════════════════════════════════════════════════════════════════
# Post-Processing Test Page
# ═══════════════════════════════════════════════════════════════════

@router.get("/post-process", response_class=HTMLResponse)
async def postprocess_page():
    """Serve the standalone post-processing test UI."""
    return get_postprocess_page()


@router.post("/post-process/run")
async def run_postprocessing(payload: dict = Body(...)):
    """Run post-processing on pre-existing segments without the audio pipeline."""
    import asyncio
    import time
    from functools import partial

    cfg = preprocess_config
    t_start = time.time()
    loop = asyncio.get_event_loop()

    segments = payload.get("segments", [])
    if not segments:
        raise HTTPException(status_code=400, detail="No segments provided")

    for i, seg in enumerate(segments):
        if "text" not in seg:
            raise HTTPException(status_code=400, detail=f"Segment {i} missing 'text' field")

    # Auto-compute duration from segment timestamps
    total_duration = max((s.get("end", 0) for s in segments), default=0.0)
    num_speakers = len(set(s.get("speaker", "UNKNOWN") for s in segments if s.get("speaker")))
    language = payload.get("language", "en")

    result = {
        "segments": segments,
        "transcription": "",
        "total_duration": total_duration,
        "num_speakers": num_speakers,
        "vocabulary": {"enabled": False},
        "llm_correction": {"enabled": False},
        "intelligence": {"enabled": False},
    }

    vocab = None
    corrected_indices = set()
    vocab_candidates = []

    # Step 1: Vocabulary correction (supports comma-separated names)
    # When LLM is enabled: collect_only=True (word matches go to LLM for context review)
    # When LLM is not enabled: collect_only=False (abbreviation normalization only)
    if payload.get("vocab_correction") and payload.get("vocabulary"):
        from app.audio_preprocess.services.vocabulary import VocabularyStore, apply_vocabulary_corrections
        vocab_names = payload["vocabulary"]
        vocab_paths = []
        for vname in vocab_names.split(","):
            vname = vname.strip()
            if not vname:
                continue
            vp = _resolve_vocab_path(vname)
            if vp.exists():
                vocab_paths.append(str(vp))

        if vocab_paths:
            if len(vocab_paths) == 1:
                vocab = VocabularyStore.load(vocab_paths[0])
            else:
                vocab = VocabularyStore.load_and_merge(vocab_paths)

            # Check if LLM is available to decide collect_only mode
            use_llm = False
            if payload.get("llm_correction"):
                from app.audio_preprocess.services.llm_corrector import check_ollama_available
                use_llm = check_ollama_available(cfg.LLM_BASE_URL)

            t0 = time.time()
            segments, corrections, corrected_indices, vocab_candidates = apply_vocabulary_corrections(
                segments, vocab, cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                collect_only=use_llm,
            )
            result["vocabulary"] = {
                "enabled": True,
                "name": vocab.name,
                "corrections": corrections,
                "processing_time_seconds": round(time.time() - t0, 2),
            }

    # Step 2: LLM correction (reviews all word-level candidates with context)
    if payload.get("llm_correction") and vocab:
        from app.audio_preprocess.services.llm_corrector import LLMCorrector, check_ollama_available
        available = check_ollama_available(cfg.LLM_BASE_URL)
        if available:
            t0 = time.time()
            corrector = LLMCorrector(
                base_url=cfg.LLM_BASE_URL,
                model=cfg.LLM_MODEL,
                vocab_entries=vocab.to_entries_list(),
                confidence_threshold=cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                timeout=cfg.LLM_TIMEOUT,
                temperature=cfg.LLM_TEMPERATURE,
                max_batch=cfg.LLM_MAX_CANDIDATES_PER_BATCH,
            )
            segments, llm_corrections = corrector.correct_segments(
                segments, corrected_indices,
                vocab_candidates=vocab_candidates,
            )
            result["llm_correction"] = {
                "enabled": True,
                "available": True,
                "corrections": llm_corrections,
                "processing_time_seconds": round(time.time() - t0, 2),
            }
        else:
            result["llm_correction"] = {"enabled": True, "available": False, "corrections": []}

    # Step 3: Intelligence / Memoir extraction
    pp_type = payload.get("post_processing_type", "phase7")
    if pp_type not in ("phase7", "memoir"):
        pp_type = "phase7"

    if payload.get("enable_intelligence"):
        from app.audio_preprocess.services.coaching_metrics import compute_coaching_metrics
        from app.audio_preprocess.services.intelligence import run_full_intelligence, check_intelligence_llm_available

        status = check_intelligence_llm_available(cfg.LLM_BASE_URL)
        if status["available"]:
            t0 = time.time()
            coaching = compute_coaching_metrics(segments, total_duration)

            if pp_type == "memoir":
                from app.audio_preprocess.services.memoir_extractor import run_memoir_extraction
                extraction = await loop.run_in_executor(
                    None,
                    partial(
                        run_memoir_extraction,
                        segments=segments,
                        total_duration=total_duration,
                        num_speakers=num_speakers,
                        language=language,
                        coaching_metrics=coaching,
                        base_url=cfg.LLM_BASE_URL,
                        model=cfg.INTELLIGENCE_LLM_MODEL,
                        temperature=cfg.LLM_TEMPERATURE,
                        timeout=cfg.LLM_TIMEOUT,
                        max_single_pass_tokens=cfg.INTELLIGENCE_MAX_SINGLE_PASS_TOKENS,
                        num_ctx=cfg.INTELLIGENCE_NUM_CTX,
                    ),
                )
            else:
                extraction = await loop.run_in_executor(
                    None,
                    partial(
                        run_full_intelligence,
                        segments=segments,
                        total_duration=total_duration,
                        num_speakers=num_speakers,
                        language=language,
                        coaching_metrics=coaching,
                        base_url=cfg.LLM_BASE_URL,
                        model=cfg.INTELLIGENCE_LLM_MODEL,
                        temperature=cfg.LLM_TEMPERATURE,
                        timeout=cfg.LLM_TIMEOUT,
                        max_single_pass_tokens=cfg.INTELLIGENCE_MAX_SINGLE_PASS_TOKENS,
                        num_ctx=cfg.INTELLIGENCE_NUM_CTX,
                    ),
                )

            result["intelligence"] = {
                "enabled": True,
                "available": True,
                "coaching_metrics": coaching,
                "extraction": extraction or {},
                "processing_time_seconds": round(time.time() - t0, 2),
                "post_processing_type": pp_type,
            }
        else:
            result["intelligence"] = {"enabled": True, "available": False, "post_processing_type": pp_type}

    # Final
    result["segments"] = segments
    result["transcription"] = " ".join(s.get("text", "") for s in segments).strip()
    result["processing_time_total"] = round(time.time() - t_start, 2)

    return JSONResponse(content=result)


# ═══════════════════════════════════════════════════════════════════
# Vocabulary Management Page + CRUD API
# ═══════════════════════════════════════════════════════════════════

@router.get("/vocabulary", response_class=HTMLResponse)
async def vocabulary_page():
    """Serve the vocabulary management UI."""
    return get_vocabulary_page()


@router.get("/vocabulary/list")
async def vocabulary_list():
    """List all available vocabularies with metadata."""
    vocab_dir = _get_vocab_dir()
    vocabularies = []
    for f in sorted(vocab_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            vocabularies.append({
                "name": f.stem,
                "display_name": data.get("name", f.stem),
                "entry_count": len(data.get("entries", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            vocabularies.append({
                "name": f.stem,
                "display_name": f.stem,
                "entry_count": 0,
                "updated_at": "",
            })
    return JSONResponse(content={"vocabularies": vocabularies})


@router.get("/vocabulary/{name}/data")
async def vocabulary_get(name: str):
    """Get full vocabulary data by name."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore
    path = _resolve_vocab_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary '{name}' not found")
    store = VocabularyStore.load(str(path))
    return JSONResponse(content=store.to_dict())


@router.post("/vocabulary/{name}/entry")
async def vocabulary_add_entry(name: str, entry: dict = Body(...)):
    """Add a new term to a vocabulary."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore, VocabEntry
    path = _resolve_vocab_path(name)
    if not path.exists():
        # Create new vocabulary file
        store = VocabularyStore(name=name)
    else:
        store = VocabularyStore.load(str(path))
    try:
        store.add_entry(VocabEntry.from_dict(entry))
        store.save(str(path))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return JSONResponse(content={"status": "added", "term": entry.get("term", "")})


@router.put("/vocabulary/{name}/entry/{term}")
async def vocabulary_update_entry(name: str, term: str, entry: dict = Body(...)):
    """Update an existing term in a vocabulary."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore, VocabEntry
    path = _resolve_vocab_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary '{name}' not found")
    store = VocabularyStore.load(str(path))
    try:
        store.update_entry(term, VocabEntry.from_dict(entry))
        store.save(str(path))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(content={"status": "updated", "term": entry.get("term", "")})


@router.delete("/vocabulary/{name}/entry/{term}")
async def vocabulary_delete_entry(name: str, term: str):
    """Delete a term from a vocabulary."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore
    path = _resolve_vocab_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary '{name}' not found")
    store = VocabularyStore.load(str(path))
    try:
        store.delete_entry(term)
        store.save(str(path))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(content={"status": "deleted", "term": term})


@router.post("/vocabulary/upload")
async def vocabulary_upload(file: UploadFile = File(...)):
    """Upload a complete vocabulary JSON file."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    # Validate structure
    if "entries" not in data or not isinstance(data["entries"], list):
        raise HTTPException(status_code=400, detail="JSON must contain an 'entries' array")

    name = data.get("name", Path(file.filename).stem)
    safe_name = "".join(c for c in name.replace(" ", "_") if c.isalnum() or c in "-_")
    if not safe_name:
        safe_name = "custom_vocabulary"

    # Load to validate, then save
    path = _get_vocab_dir() / f"{safe_name}.json"
    store = VocabularyStore(name=name)
    from app.audio_preprocess.services.vocabulary import VocabEntry
    store.entries = [VocabEntry.from_dict(e) for e in data["entries"]]
    store._rebuild_indexes()
    store.save(str(path))

    return JSONResponse(content={
        "status": "uploaded",
        "name": safe_name,
        "display_name": name,
        "entry_count": len(store.entries),
    })


@router.get("/vocabulary/{name}/export")
async def vocabulary_export(name: str):
    """Download a vocabulary as a JSON file."""
    path = _resolve_vocab_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary '{name}' not found")
    return FileResponse(
        str(path),
        media_type="application/json",
        filename=f"{name}.json",
    )


@router.post("/vocabulary/{name}/load-starter")
async def vocabulary_load_starter(name: str, starter: str = Body(..., embed=True)):
    """Load a starter pack into a vocabulary (creates or replaces)."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore
    starter_path = _get_vocab_dir() / f"{starter}.json"
    if not starter_path.exists():
        raise HTTPException(status_code=404, detail=f"Starter pack '{starter}' not found")

    store = VocabularyStore.load(str(starter_path))
    target_path = _resolve_vocab_path(name)
    store.name = name
    store.save(str(target_path))

    return JSONResponse(content={
        "status": "loaded",
        "name": name,
        "source": starter,
        "entry_count": len(store.entries),
    })


@router.post("/vocabulary/test-match")
async def vocabulary_test_match(
    vocab_name: str = Body(...),
    word: str = Body(...),
    context: list = Body(default=[]),
):
    """Test if a word matches any entry in a vocabulary."""
    from app.audio_preprocess.services.vocabulary import VocabularyStore
    path = _resolve_vocab_path(vocab_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Vocabulary '{vocab_name}' not found")
    store = VocabularyStore.load(str(path))
    result = store.test_match(word, context if context else None)
    return JSONResponse(content=result)
