"""
Pipeline orchestrator — runs the full A/B comparison flow.

1. Analyze raw audio
2. Core processing (always-on)
   2a. Resample 16kHz mono
   2b. Highpass 80Hz (+ phone mode spectral)
   2c. VAD silence trimming
   2d. Loudnorm
3. Conditional processing (if needed)
3.4. Resemble Enhance (if Path B selected)
3.5. Speaker Diarization (if enabled — runs on pre-VAD audio, timestamps remapped)
4. Path A: Direct ASR on processed audio
5. Path B: ASR on enhanced audio
6. Intelligence Extraction (if enabled)
"""
import asyncio
import logging
import shutil
import threading
import time
import uuid
from functools import partial
from pathlib import Path

from app.audio_preprocess.config import preprocess_config
from app.job_processor.progress import update_progress, update_meeting_progress
from app.audio_preprocess.services.analyzer import analyze_audio, AudioAnalysis, estimate_snr
from app.audio_preprocess.services.core_processing import (
    apply_highpass,
    resample_and_highpass,
    trim_silence_vad,
    apply_loudnorm_dual_pass,
    resample_to_16k,
)
from app.audio_preprocess.services.conditional import (
    denoise_with_oa,
    apply_bandwidth_extension,
    apply_declipping,
    apply_treble_boost,
    apply_dynamic_range_compression,
)
from app.audio_preprocess.services.aligner import align_segments
from app.audio_preprocess.services.diarize import diarize_audio, assign_speakers_to_segments
from app.audio_preprocess.services.enhance_playback import apply_resemble_enhance
from app.audio_preprocess.services.transcribe import transcribe

import numpy as np
import soundfile as sf

logger = logging.getLogger("audio_preprocess")

# Punctuation restoration model (oliverguhr/fullstop-punctuation-multilang-large)
_punct_model = None
_punct_lock = threading.Lock()


def _get_punct_model():
    """Load punctuation restoration model as a singleton (thread-safe).

    Forces CPU to avoid consuming GPU VRAM needed by alignment/diarization.
    The model is lightweight (~120MB) and fast enough on CPU (~1-2s for a full meeting).
    """
    global _punct_model
    if _punct_model is not None:
        return _punct_model
    with _punct_lock:
        if _punct_model is not None:
            return _punct_model
        import torch as _torch
        from deepmultilingualpunctuation import PunctuationModel
        punct_device = 0 if _torch.cuda.is_available() else -1
        device_name = "GPU" if punct_device == 0 else "CPU"
        logger.info(f"Loading punctuation restoration model ({device_name})...")
        model = PunctuationModel.__new__(PunctuationModel)
        from transformers import pipeline as hf_pipeline
        try:
            model.pipe = hf_pipeline(
                "ner",
                "oliverguhr/fullstop-punctuation-multilang-large",
                aggregation_strategy="none",
                device=punct_device,
            )
        except TypeError:
            model.pipe = hf_pipeline(
                "ner",
                "oliverguhr/fullstop-punctuation-multilang-large",
                grouped_entities=False,
                device=punct_device,
            )
        _punct_model = model
        logger.info("Punctuation restoration model loaded (CPU)")
        return _punct_model


def _restore_punctuation(segments: list) -> int:
    """Restore punctuation in segment text using a dedicated model.

    Runs AFTER ASR, BEFORE alignment and speaker assignment. Restores
    missing periods, commas, and question marks so that sentence-boundary-based
    speaker realignment can work correctly.

    Args:
        segments: List of ASR segment dicts (modified in place).

    Returns:
        Number of segments where punctuation was restored.
    """
    model = _get_punct_model()
    restored = 0
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        try:
            new_text = model.restore_punctuation(text)
            if new_text != text:
                seg["text"] = new_text
                restored += 1
        except Exception as e:
            logger.warning(f"Punctuation restoration failed for segment: {e}")
    logger.info(f"Punctuation restored for {restored}/{len(segments)} segments")
    return restored


# DISABLED: pcs_en truecasing model — caused punctuation doubling, segment merging,
# <unk> token leakage, and number corruption. The model expects unpunctuated lowercase
# input but Whisper outputs already-punctuated text. Reverted to oliverguhr above.
#
# _truecase_model = None
# _truecase_lock = threading.Lock()
#
# def _get_truecase_model():
#     from punctuators.models import PunctCapSegModelONNX
#     ...
#
# def _restore_punctuation(segments):
#     model = _get_truecase_model()
#     texts = [text.lower() for ...]  # <-- this destroys Whisper's punctuation
#     ...


_FREE_GPU_DECISION: bool | None = None  # cached after first resolution


def _should_free_gpu_models() -> bool:
    """Return True if _free_gpu_models() should actually run.

    Decision (cached for the process):
      1. If env var MEMOIR_FREE_GPU_MODELS is set to "true"/"false" → honor it.
      2. If set to "auto" → autodetect: free only when total VRAM on device 0
         is below MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB (default 20).
      3. If missing → default to True (safe — matches current 16 GB
         production behavior; only big-GPU boxes need explicit "false").

    vast-start.sh sets MEMOIR_FREE_GPU_MODELS=true|false explicitly based on
    the GPU it boots on, so production normally hits step 1.
    """
    global _FREE_GPU_DECISION
    if _FREE_GPU_DECISION is not None:
        return _FREE_GPU_DECISION

    import os
    setting = os.getenv("MEMOIR_FREE_GPU_MODELS", "true").strip().lower()
    if setting == "true":
        _FREE_GPU_DECISION = True
    elif setting == "false":
        _FREE_GPU_DECISION = False
    elif setting == "auto":
        try:
            import torch
            if torch.cuda.is_available():
                total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
                threshold = float(os.getenv("MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB", "20"))
                _FREE_GPU_DECISION = total_gb < threshold
                logger.info(
                    f"_free_gpu_models autodetect: total VRAM {total_gb:.1f} GB, "
                    f"threshold {threshold} GB, decision={_FREE_GPU_DECISION}"
                )
            else:
                _FREE_GPU_DECISION = True
        except Exception:
            _FREE_GPU_DECISION = True  # safe default
    else:
        _FREE_GPU_DECISION = True  # unknown value → safe default

    return _FREE_GPU_DECISION


def _free_gpu_models():
    """Free heavy GPU model singletons to reclaim VRAM for Ollama.

    Gated by MEMOIR_FREE_GPU_MODELS env (set by vast-start.sh based on the
    GPU's total VRAM). On a 24 GB+ GPU all models fit alongside vLLM/Ollama
    so freeing them between stages just adds a 5-10s reload penalty for no
    memory benefit — vast-start.sh exports MEMOIR_FREE_GPU_MODELS=false and
    this function returns immediately.

    See `_should_free_gpu_models()` for the resolution logic.
    """
    if not _should_free_gpu_models():
        return

    import torch
    import gc

    # Free diarization pipeline
    from app.audio_preprocess.services import diarize
    if diarize._diarization_pipeline is not None:
        diarize._diarization_pipeline = None
        logger.info("Freed diarization pipeline from VRAM")
    if diarize._embedding_inference is not None:
        diarize._embedding_inference = None
        logger.info("Freed speaker embedding model from VRAM")

    # Free ASR model
    from app.audio_preprocess.services import transcribe as _transcribe_mod
    if getattr(_transcribe_mod, '_fw_model', None) is not None:
        _transcribe_mod._fw_model = None
        _transcribe_mod._fw_model_size = None
        logger.info("Freed faster-whisper model from VRAM")

    # Free aligner ONNX session
    from app.audio_preprocess.services import aligner
    if aligner._onnx_session is not None:
        aligner._onnx_session = None
        aligner._tokenizer = None
        logger.info("Freed ONNX aligner from VRAM")

    # Free SpeechBrain classifier
    from app.audio_preprocess.services import voice_embeddings as _ve_mod
    if _ve_mod._classifier is not None:
        _ve_mod._classifier = None
        logger.info("Freed SpeechBrain classifier from VRAM")

    # Free punctuation model
    global _punct_model
    if _punct_model is not None:
        _punct_model = None
        logger.info("Freed punctuation model from memory")

    gc.collect()
    torch.cuda.empty_cache()
    allocated = torch.cuda.memory_allocated() / 1024**3
    logger.info(f"GPU memory after cleanup: {allocated:.1f} GB allocated")


class PipelineResult:
    def __init__(self):
        self.input_analysis: dict = {}
        self.steps: list = []  # list of {step, status, reason}
        self.processing_mode: str = "both"

        # Path A
        self.path_a_audio: str = ""
        self.path_a_snr: float = 0.0
        self.path_a_duration: float = 0.0
        self.path_a_transcription: str = ""
        self.path_a_char_count: int = 0
        self.path_a_time_seconds: float = 0.0
        self.path_a_segments: list = []

        # Path B
        self.path_b_audio: str = ""
        self.path_b_snr: float = 0.0
        self.path_b_duration: float = 0.0
        self.path_b_transcription: str = ""
        self.path_b_char_count: int = 0
        self.path_b_time_seconds: float = 0.0
        self.path_b_segments: list = []

        self.resemble_available: bool = False

        # Diarization
        self.diarization_enabled: bool = False
        self.diarization_result: dict = {}
        self.diarization_time_seconds: float = 0.0

        # Word-level alignment
        self.alignment_enabled: bool = False
        self.alignment_time_seconds: float = 0.0

        # VAD boundaries for timestamp remapping
        self.vad_boundaries: list = []

        # Vocabulary correction (Phase 5a + 6a)
        self.vocabulary_enabled: bool = False
        self.vocabulary_name: str = ""
        self.vocabulary_corrections_a: list = []
        self.vocabulary_corrections_b: list = []
        self.vocabulary_correction_time: float = 0.0

        # LLM context correction (Phase 5b)
        self.llm_correction_enabled: bool = False
        self.llm_available: bool = False
        self.llm_corrections_a: list = []
        self.llm_corrections_b: list = []
        self.llm_correction_time: float = 0.0
        self.llm_corrector_calls: int = 0  # number of batches sent to Ollama

        # Cost tracking (populated after pipeline runs, written to pipeline_cost.json)
        self.asr_processing_seconds: float = 0.0   # just ASR, not full path time
        self.audio_duration_seconds: float = 0.0   # input audio length in seconds

        # Intelligence extraction (Phase 7) / Memoir (Phase 10)
        self.intelligence_enabled: bool = False
        self.intelligence_available: bool = False
        self.coaching_metrics: dict = {}
        self.intelligence_result: dict = {}
        self.intelligence_time: float = 0.0
        self.post_processing_type: str = "phase7"  # "phase7" or "memoir"

    def to_dict(self) -> dict:
        return {
            "input_analysis": self.input_analysis,
            "steps": self.steps,
            "processing_mode": self.processing_mode,
            "path_a": {
                "audio_file": self.path_a_audio,
                "output_snr_db": self.path_a_snr,
                "duration_seconds": self.path_a_duration,
                "transcription": self.path_a_transcription,
                "char_count": self.path_a_char_count,
                "processing_time_seconds": round(self.path_a_time_seconds, 2),
                "segments": self.path_a_segments,
            },
            "path_b": {
                "audio_file": self.path_b_audio,
                "output_snr_db": self.path_b_snr,
                "duration_seconds": self.path_b_duration,
                "transcription": self.path_b_transcription,
                "char_count": self.path_b_char_count,
                "processing_time_seconds": round(self.path_b_time_seconds, 2),
                "segments": self.path_b_segments,
                "resemble_available": self.resemble_available,
            },
            "diarization": {
                "enabled": self.diarization_enabled,
                "num_speakers": self.diarization_result.get("num_speakers", 0),
                "method": self.diarization_result.get("method", ""),
                "speakers": self.diarization_result.get("speakers", []),
                "processing_time_seconds": round(self.diarization_time_seconds, 2),
                "verification": self.diarization_result.get("verification"),
            },
            "alignment": {
                "enabled": self.alignment_enabled,
                "processing_time_seconds": round(self.alignment_time_seconds, 2),
            },
            "vad_boundaries": self.vad_boundaries,
            "vocabulary": {
                "enabled": self.vocabulary_enabled,
                "name": self.vocabulary_name,
                "corrections_a": self.vocabulary_corrections_a,
                "corrections_b": self.vocabulary_corrections_b,
                "processing_time_seconds": round(self.vocabulary_correction_time, 2),
            },
            "llm_correction": {
                "enabled": self.llm_correction_enabled,
                "available": self.llm_available,
                "corrections_a": self.llm_corrections_a,
                "corrections_b": self.llm_corrections_b,
                "processing_time_seconds": round(self.llm_correction_time, 2),
            },
            "intelligence": {
                "enabled": self.intelligence_enabled,
                "available": self.intelligence_available,
                "coaching_metrics": self.coaching_metrics,
                "extraction": self.intelligence_result,
                "processing_time_seconds": round(self.intelligence_time, 2),
                "post_processing_type": self.post_processing_type,
            },
        }


def _remap_to_original_time(trimmed_time: float, vad_boundaries: list) -> float:
    """Convert a timestamp from VAD-trimmed audio back to original audio time.

    Walks through VAD speech segments accumulating trimmed durations.
    Finds which segment the timestamp falls in and computes the offset.
    """
    cumulative = 0.0
    for seg in vad_boundaries:
        seg_duration = seg["end"] - seg["start"]
        if trimmed_time <= cumulative + seg_duration:
            offset = trimmed_time - cumulative
            return seg["start"] + offset
        cumulative += seg_duration
    # Past end — clamp to last boundary
    return vad_boundaries[-1]["end"] if vad_boundaries else trimmed_time


def _remap_segments(segments: list, vad_boundaries: list) -> None:
    """Add original_start/original_end to each segment and its words.

    If vad_boundaries is empty (no trimming happened), original = processed (1:1).
    """
    for seg in segments:
        if vad_boundaries:
            seg["original_start"] = round(_remap_to_original_time(seg["start"], vad_boundaries), 3)
            seg["original_end"] = round(_remap_to_original_time(seg["end"], vad_boundaries), 3)
        else:
            seg["original_start"] = seg["start"]
            seg["original_end"] = seg["end"]
        for w in seg.get("words", []):
            if vad_boundaries:
                w["original_start"] = round(_remap_to_original_time(w["start"], vad_boundaries), 3)
                w["original_end"] = round(_remap_to_original_time(w["end"], vad_boundaries), 3)
            else:
                w["original_start"] = w["start"]
                w["original_end"] = w["end"]
        for w in seg.get("translation_words", []):
            if vad_boundaries:
                w["original_start"] = round(_remap_to_original_time(w["start"], vad_boundaries), 3)
                w["original_end"] = round(_remap_to_original_time(w["end"], vad_boundaries), 3)
            else:
                w["original_start"] = w["start"]
                w["original_end"] = w["end"]


async def run_preprocess(
    input_file: Path,
    temp_dir: Path,
    phone_mode: bool = False,
    job_id: str = None,
    meeting_id: str = None,
) -> dict:
    """Run preprocessing only (operation type 1).

    Returns a serializable dict with:
    - preprocessed_audio: path to preprocessed WAV in temp_dir
    - vad_boundaries: list of {start, end} speech segments
    - analysis: AudioAnalysis dict
    - steps: list of pipeline step dicts
    - audio_duration_seconds: original audio duration
    - config: preprocessing config values needed by later types
    """
    cfg = preprocess_config
    steps = []
    loop = asyncio.get_event_loop()

    # ── 0. Pre-convert unsupported formats to WAV ──
    SOUNDFILE_FORMATS = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}
    if input_file.suffix.lower() not in SOUNDFILE_FORMATS:
        logger.info("Pre-converting %s to WAV (unsupported by soundfile)", input_file.suffix)
        pre_wav = temp_dir / "pre_analysis.wav"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(input_file), "-c:a", "pcm_s16le", str(pre_wav),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        if proc.returncode == 0 and pre_wav.exists():
            input_file = pre_wav

    # ── 1. Analyze raw input ──
    if meeting_id:
        await update_meeting_progress(meeting_id, 1, 10, "Analyzing audio...")
    logger.info("=== Preprocess Step 1: Analyzing raw audio ===")
    analysis = analyze_audio(
        input_file,
        snr_threshold=cfg.SNR_THRESHOLD_DB,
        clipping_threshold=cfg.CLIPPING_THRESHOLD,
        low_sr_hz=cfg.LOW_SAMPLE_RATE_HZ,
    )

    if phone_mode:
        steps.append({
            "step": "Phone/VoIP Mode",
            "status": "applied",
            "reason": f"VAD threshold {cfg.PHONE_MODE_VAD_THRESHOLD}, treble +{cfg.PHONE_MODE_TREBLE_GAIN}dB, DRC applied.",
        })

    # ── 2. Core processing ──
    if meeting_id:
        await update_meeting_progress(meeting_id, 1, 20, "Preprocessing audio...")
    logger.info("=== Preprocess Step 2: Core processing ===")
    current_path = input_file

    # 2a+2b. Resample to 48kHz mono + Highpass
    current_path = await resample_and_highpass(current_path, temp_dir, target_sr=48000)
    steps.append({"step": "Resample to 48kHz mono + Highpass 80Hz", "status": "applied", "reason": f"Original {analysis.sample_rate}Hz {analysis.channels}ch."})

    # Phone mode spectral
    if phone_mode:
        current_path = await apply_treble_boost(current_path, temp_dir, gain=cfg.PHONE_MODE_TREBLE_GAIN, frequency=cfg.PHONE_MODE_TREBLE_FREQ)
        steps.append({"step": "Treble boost (phone mode)", "status": "applied", "reason": f"+{cfg.PHONE_MODE_TREBLE_GAIN}dB at {cfg.PHONE_MODE_TREBLE_FREQ}Hz."})
        current_path = await apply_dynamic_range_compression(current_path, temp_dir)
        steps.append({"step": "DRC (phone mode)", "status": "applied", "reason": "Gentle compression with +8dB makeup gain."})

    # 2c. VAD
    if meeting_id:
        await update_meeting_progress(meeting_id, 1, 30, "Detecting speech...")
    pre_vad_audio, pre_vad_sr = sf.read(str(current_path), dtype="float32")
    pre_vad_dur = len(pre_vad_audio) / pre_vad_sr
    audio_duration_seconds = pre_vad_dur

    current_path, vad_boundaries = await loop.run_in_executor(
        None, partial(trim_silence_vad, current_path, temp_dir, phone_mode=phone_mode)
    )

    post_vad_audio, post_vad_sr = sf.read(str(current_path), dtype="float32")
    post_vad_dur = len(post_vad_audio) / post_vad_sr
    steps.append({"step": "Silero VAD", "status": "applied", "reason": f"Removed {pre_vad_dur - post_vad_dur:.1f}s non-speech ({pre_vad_dur:.1f}s → {post_vad_dur:.1f}s)."})

    # 2d. Loudnorm
    if meeting_id:
        await update_meeting_progress(meeting_id, 1, 40, "Normalizing audio...")
    current_path = await apply_loudnorm_dual_pass(current_path, temp_dir, cfg.TARGET_LUFS, cfg.TARGET_TP, cfg.TARGET_LRA)
    steps.append({"step": f"Dual-pass loudnorm ({cfg.TARGET_LUFS} LUFS)", "status": "applied", "reason": "EBU R128 normalization."})

    # ── 3. Conditional processing ──
    logger.info("=== Preprocess Step 3: Conditional processing ===")
    if analysis.needs_bandwidth_extension:
        current_path = await loop.run_in_executor(None, apply_bandwidth_extension, current_path, temp_dir)
        steps.append({"step": "Bandwidth extension", "status": "applied", "reason": f"Input SR {analysis.sample_rate}Hz."})
    if analysis.needs_declipping:
        current_path = await loop.run_in_executor(None, apply_declipping, current_path, temp_dir)
        steps.append({"step": "Declipping", "status": "applied", "reason": f"Clipping {analysis.clipping_percentage}%."})
    if analysis.needs_denoising:
        current_path = await loop.run_in_executor(None, denoise_with_oa, current_path, temp_dir, cfg.OA_ALPHA)
        steps.append({"step": "DeepFilterNet3 + OA", "status": "applied", "reason": f"SNR {analysis.snr_db}dB."})

    processed_path = current_path

    if meeting_id:
        await update_meeting_progress(meeting_id, 1, 80, "Preprocess complete")

    return {
        "preprocessed_audio": str(processed_path),
        "vad_boundaries": vad_boundaries,
        "analysis": analysis.to_dict(),
        "steps": steps,
        "audio_duration_seconds": audio_duration_seconds,
        "config": {
            "phone_mode": phone_mode,
        },
    }


async def run_pipeline(input_file: Path, temp_dir: Path, processing_mode: str = "both", language: str = None, engine: str = None, model_size: str = None, multilingual: bool = False, diarize: bool = False, num_speakers: int = 0, align: bool = False, translate_to_english: bool = False, vocabulary_files: list = None, vocab_correction: bool = False, llm_correction: bool = False, enable_intelligence: bool = False, phone_mode: bool = False, post_processing_type: str = "phase7", initial_prompt: str | None = None, job_id: str = None) -> PipelineResult:
    result = PipelineResult()
    cfg = preprocess_config

    # Resolve engine and model from UI selection or config defaults
    effective_engine = engine or cfg.ASR_ENGINE
    effective_model_size = model_size or cfg.ASR_MODEL_SIZE

    # Load vocabulary if vocab_correction is enabled and files are specified
    vocab = None
    if vocab_correction and vocabulary_files:
        from app.audio_preprocess.services.vocabulary import VocabularyStore
        try:
            if len(vocabulary_files) == 1:
                vocab = VocabularyStore.load(vocabulary_files[0])
            else:
                vocab = VocabularyStore.load_and_merge(vocabulary_files)
            result.vocabulary_enabled = True
            result.vocabulary_name = vocab.name
            logger.info(f"Vocabulary loaded: '{vocab.name}' ({len(vocab.entries)} entries, post-ASR correction only)")
        except Exception as e:
            logger.warning(f"Failed to load vocabulary: {e}")

    # Check LLM availability if requested
    if llm_correction and vocab:
        from app.audio_preprocess.services.llm_corrector import check_ollama_available
        result.llm_correction_enabled = True
        result.llm_available = check_ollama_available(cfg.LLM_BASE_URL)
        if not result.llm_available:
            logger.warning("LLM correction requested but Ollama is not available")

    # Check intelligence LLM availability if requested
    if enable_intelligence:
        from app.audio_preprocess.services.intelligence import check_intelligence_llm_available
        result.intelligence_enabled = True
        status = check_intelligence_llm_available(cfg.LLM_BASE_URL)
        result.intelligence_available = status["available"]
        if not result.intelligence_available:
            logger.warning("Intelligence extraction requested but Ollama is not available")

    # ── 0. Pre-convert unsupported formats to WAV ──
    # soundfile (libsndfile) cannot read AAC, MP4, etc. Convert to WAV first.
    SOUNDFILE_FORMATS = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}
    if input_file.suffix.lower() not in SOUNDFILE_FORMATS:
        logger.info("Pre-converting %s to WAV (unsupported by soundfile)", input_file.suffix)
        pre_wav = temp_dir / "pre_analysis.wav"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(input_file), "-c:a", "pcm_s16le", str(pre_wav),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        if proc.returncode == 0 and pre_wav.exists():
            input_file = pre_wav
        else:
            logger.warning("Pre-conversion failed, attempting analysis on original file")

    # ── 1. Analyze raw input ──
    if job_id: await update_progress(job_id, 10, "Analyzing audio...")
    logger.info("=== Step 1: Analyzing raw audio ===")
    analysis = analyze_audio(
        input_file,
        snr_threshold=cfg.SNR_THRESHOLD_DB,
        clipping_threshold=cfg.CLIPPING_THRESHOLD,
        low_sr_hz=cfg.LOW_SAMPLE_RATE_HZ,
    )
    result.input_analysis = analysis.to_dict()

    # Phone/VoIP mode step entry
    if phone_mode:
        result.steps.append({
            "step": "Phone/VoIP Mode",
            "status": "applied",
            "reason": (
                "Sensitivity adjustments for muffled/quiet speakers: "
                f"VAD threshold lowered to {cfg.PHONE_MODE_VAD_THRESHOLD} (from 0.5), "
                f"min speech duration {cfg.PHONE_MODE_MIN_SPEECH_MS}ms (from 250ms), "
                f"speech padding {cfg.PHONE_MODE_SPEECH_PAD_MS}ms (from 200ms), "
                "Whisper no_speech_threshold disabled. "
                f"Spectral: treble boost +{cfg.PHONE_MODE_TREBLE_GAIN}dB at {cfg.PHONE_MODE_TREBLE_FREQ}Hz, "
                "dynamic range compression (brings quiet speaker up ~8-10dB)."
            ),
        })

    # ── 2. Core processing (always-on) ──
    if job_id: await update_progress(job_id, 12, "Preprocessing audio...")
    logger.info("=== Step 2: Core processing ===")
    current_path = input_file
    loop = asyncio.get_event_loop()

    # 2a+2b. Resample to 16kHz mono + Highpass 80Hz (single FFmpeg pass)
    current_path = await resample_and_highpass(current_path, temp_dir)
    result.steps.append({
        "step": "Resample to 16kHz mono + Highpass 80Hz",
        "status": "applied",
        "reason": (
            f"Combined in a single FFmpeg pass. Whisper requires 16kHz mono input. "
            f"Original was {analysis.sample_rate}Hz, {analysis.channels}ch. "
            "Highpass removes frequencies below 80Hz (rumble, AC hum, handling noise). "
            "No lowpass applied — high frequencies are preserved for consonant recognition (/s/, /f/, /th/)."
        ),
    })

    # 2b.1 + 2b.2: Phone mode spectral enhancement (Tier 2) — before VAD
    if phone_mode:
        # Treble boost — restore consonant frequencies stripped by VoIP codecs
        current_path = await apply_treble_boost(
            current_path, temp_dir,
            gain=cfg.PHONE_MODE_TREBLE_GAIN,
            frequency=cfg.PHONE_MODE_TREBLE_FREQ,
        )
        result.steps.append({
            "step": "Treble boost (phone mode)",
            "status": "applied",
            "reason": (
                f"+{cfg.PHONE_MODE_TREBLE_GAIN}dB shelf at {cfg.PHONE_MODE_TREBLE_FREQ}Hz. "
                "VoIP codecs strip frequencies above 3-4kHz, removing consonants (/s/, /f/, /th/) "
                "that VAD and Whisper need. This partially restores them for the muffled speaker."
            ),
        })

        # Dynamic range compression — reduce volume gap between speakers
        current_path = await apply_dynamic_range_compression(current_path, temp_dir)
        result.steps.append({
            "step": "Dynamic range compression (phone mode)",
            "status": "applied",
            "reason": (
                "Gentle ~3:1 compression with +8dB makeup gain. "
                "Reduces 15-25dB volume gap between loud local speaker and quiet muffled speaker, "
                "helping VAD detect the quiet speaker's speech segments."
            ),
        })

    # 2c. VAD
    if job_id: await update_progress(job_id, 14, "Detecting speech...")
    pre_vad_audio, pre_vad_sr = sf.read(str(current_path), dtype="float32")
    pre_vad_dur = len(pre_vad_audio) / pre_vad_sr
    result.audio_duration_seconds = pre_vad_dur  # record input audio length for cost tracking

    current_path, vad_boundaries = await loop.run_in_executor(
        None, partial(trim_silence_vad, current_path, temp_dir, phone_mode=phone_mode)
    )
    result.vad_boundaries = vad_boundaries

    # ── Diagnostic: log VAD boundaries to check if inter-speaker pauses were removed ──
    if vad_boundaries:
        logger.info(f"=== VAD boundaries ({len(vad_boundaries)} speech segments) ===")
        for i, b in enumerate(vad_boundaries):
            dur = round(b["end"] - b["start"], 2)
            gap = ""
            if i > 0:
                gap_dur = round(b["start"] - vad_boundaries[i - 1]["end"], 2)
                gap = f" (gap={gap_dur:.2f}s from prev)"
            logger.info(f"  VAD seg {i}: [{b['start']:.2f}s - {b['end']:.2f}s] ({dur:.2f}s){gap}")
        logger.info("=== End VAD boundaries ===")

    post_vad_audio, post_vad_sr = sf.read(str(current_path), dtype="float32")
    post_vad_dur = len(post_vad_audio) / post_vad_sr
    removed_dur = pre_vad_dur - post_vad_dur

    phone_mode_note = (
        f" Phone mode active: threshold=0.3, min_speech=150ms, pad=400ms."
        if phone_mode else ""
    )
    result.steps.append({
        "step": "Silero VAD silence trimming",
        "status": "applied",
        "reason": (
            f"Removed {removed_dur:.1f}s of non-speech ({pre_vad_dur:.1f}s → {post_vad_dur:.1f}s). "
            "Whisper hallucinates (generates phantom text) on silence and background noise. "
            f"VAD keeps only speech segments with padding to preserve natural boundaries.{phone_mode_note}"
        ),
    })

    # 2d. Loudnorm
    if job_id: await update_progress(job_id, 16, "Normalizing audio...")
    current_path = await apply_loudnorm_dual_pass(
        current_path, temp_dir, cfg.TARGET_LUFS, cfg.TARGET_TP, cfg.TARGET_LRA
    )
    result.steps.append({
        "step": f"Dual-pass loudnorm ({cfg.TARGET_LUFS} LUFS)",
        "status": "applied",
        "reason": (
            f"Normalizes loudness to {cfg.TARGET_LUFS} LUFS (EBU R128 broadcast standard). "
            f"Input RMS was {analysis.rms_amplitude} — dual-pass first measures actual loudness, "
            "then applies precise correction. 5-10% WER improvement per Deepgram's data."
        ),
    })

    # ── 3. Conditional processing ──
    logger.info("=== Step 3: Conditional processing ===")

    # Bandwidth extension
    if analysis.needs_bandwidth_extension:
        current_path = await loop.run_in_executor(
            None, apply_bandwidth_extension, current_path, temp_dir
        )
        result.steps.append({
            "step": "Bandwidth extension",
            "status": "applied",
            "reason": (
                f"Input sample rate was {analysis.sample_rate}Hz (≤ {cfg.LOW_SAMPLE_RATE_HZ}Hz threshold). "
                "Telephone-quality audio lacks high-frequency consonant information. "
                "High-quality resampling recovers spectral detail Whisper needs for accurate transcription."
            ),
        })
    else:
        result.steps.append({
            "step": "Bandwidth extension",
            "status": "skipped",
            "reason": (
                f"Not needed — input sample rate is {analysis.sample_rate}Hz, "
                f"above the {cfg.LOW_SAMPLE_RATE_HZ}Hz threshold. Audio already has sufficient "
                "high-frequency content for ASR."
            ),
        })

    # Declipping
    if analysis.needs_declipping:
        current_path = await loop.run_in_executor(
            None, apply_declipping, current_path, temp_dir
        )
        result.steps.append({
            "step": "Declipping",
            "status": "applied",
            "reason": (
                f"Clipping detected at {analysis.clipping_percentage}% of samples (> 0.1% threshold). "
                "Clipped audio has flat-topped waveforms that distort speech and confuse ASR. "
                "Soft saturation (tanh compression) restores smoother waveform shapes."
            ),
        })
    else:
        result.steps.append({
            "step": "Declipping",
            "status": "skipped",
            "reason": (
                f"Not needed — only {analysis.clipping_percentage}% of samples at peak "
                f"(threshold is > 0.1%). Audio waveforms are not distorted."
            ),
        })

    # Denoising
    if analysis.needs_denoising:
        current_path = await loop.run_in_executor(
            None, denoise_with_oa, current_path, temp_dir, cfg.OA_ALPHA
        )
        result.steps.append({
            "step": "DeepFilterNet3 + Observation Adding",
            "status": "applied",
            "reason": (
                f"SNR is {analysis.snr_db}dB (< {cfg.SNR_THRESHOLD_DB}dB threshold). "
                f"Audio is noisy. DeepFilterNet3 suppresses noise, then Observation Adding "
                f"blends {int(cfg.OA_ALPHA*100)}% enhanced + {int((1-cfg.OA_ALPHA)*100)}% original "
                "to prevent speech artifacts that hurt ASR. Research shows pure denoising can "
                "increase WER by up to 47% — OA blending mitigates this."
            ),
        })
    else:
        result.steps.append({
            "step": "DeepFilterNet3 denoising",
            "status": "skipped",
            "reason": (
                f"Not needed — SNR is {analysis.snr_db}dB, well above the "
                f"{cfg.SNR_THRESHOLD_DB}dB threshold. Applying denoising to clean audio "
                "introduces artifacts that hurt ASR accuracy more than the noise itself. "
                "Dec 2025 research: denoising degraded WER in all 40 tested configurations."
            ),
        })

    # Multilingual mode step
    if multilingual and language is None:
        result.steps.append({
            "step": "Multilingual Mode (Language Survey + Dual-Pass)",
            "status": "applied",
            "reason": (
                "Language survey: 5 evenly-spaced 15s samples analyzed for language identification. "
                "If multiple languages detected, each 28s transcription chunk is transcribed with every "
                "candidate language; best result picked by avg_logprob. Hallucination filtering applied "
                "(compression_ratio > 2.0, avg_logprob < -0.7, no_speech_prob > 0.5, n-gram repetition)."
            ),
        })
    elif multilingual:
        result.steps.append({
            "step": "Multilingual Mode",
            "status": "skipped",
            "reason": "Multilingual mode requires language to be 'auto'. A specific language was forced.",
        })

    # Translation step
    if translate_to_english:
        result.steps.append({
            "step": "Translate to English",
            "status": "applied",
            "reason": (
                "Indian languages (Tamil, Hindi, etc.) translated via IndicTrans2 text-to-text "
                "model for accuracy, with Whisper providing word-level timestamps. "
                "Other languages translated via Whisper translate mode (text + word timestamps)."
            ),
        })

    # Smart-processed audio ready
    processed_path = current_path
    result.processing_mode = processing_mode

    # ── 3.4 Resemble Enhance + 3.5 Diarization + 4. ASR ──
    # On GPU with path_b: run (Resemble Enhance → ASR) in parallel with Diarization
    # On CPU or path_a: run sequentially as before
    enhanced_16k = None
    diarization_result = None

    # Word-level alignment step entry (actual work happens after transcription)
    if not align:
        result.steps.append({
            "step": "Word-Level Alignment",
            "status": "skipped",
            "reason": "Not enabled. Toggle alignment in the UI for word-level timestamps.",
        })

    # Build diarization function (if needed)
    diarize_func = None
    if diarize:
        diarize_func = partial(diarize_audio, processed_path, cfg, num_speakers)

    # Prepare Path A audio metadata (needed regardless of parallel/sequential)
    if processing_mode in ("path_a", "both"):
        path_a_serve = temp_dir / "path_a_output.wav"
        shutil.copy2(str(processed_path), str(path_a_serve))
        result.path_a_audio = path_a_serve.name

        audio_a, sr_a = sf.read(str(path_a_serve), dtype="float32")
        result.path_a_snr = estimate_snr(audio_a, sr_a)
        result.path_a_duration = round(len(audio_a) / sr_a, 2)

    # Build ASR function for Path A (if needed)
    transcribe_a = None
    if processing_mode in ("path_a", "both"):
        logger.info(f"=== Step 4: Path A — Direct ASR (engine={effective_engine}, model={effective_model_size}, lang={language or 'auto'}, multilingual={multilingual}, translate={translate_to_english}) ===")
        transcribe_a = partial(
            transcribe, processed_path, effective_model_size, cfg.ASR_DEVICE,
            language, effective_engine, cfg.ASR_COMPUTE_TYPE,
            multilingual, cfg.LID_CONFIDENCE_THRESHOLD, cfg.LID_MIN_SPEECH_DURATION_MS,
            translate_to_english, align,
            hotwords="", phone_mode=phone_mode,
            initial_prompt=initial_prompt,
        )

    # ── GPU Path B parallel: (Resemble Enhance → ASR) || Diarization ──
    if processing_mode in ("path_b", "both") and diarize and cfg.ASR_DEVICE == "cuda":
        if job_id: await update_progress(job_id, 18, "Enhancing audio + Identifying speakers...")
        logger.info("=== Steps 3.4+3.5+5: Parallel (Enhance→ASR) + Diarization (GPU mode) ===")
        t0 = time.time()

        async def _enhance_and_transcribe():
            """Sequential chain: Resemble Enhance → resample → ASR on enhanced audio."""
            enhanced_path = await loop.run_in_executor(
                None, apply_resemble_enhance, processed_path, temp_dir, cfg.RESEMBLE_ENHANCE_URL
            )
            _enhanced_16k = await resample_to_16k(enhanced_path, temp_dir)
            transcribe_b = partial(
                transcribe, _enhanced_16k, effective_model_size, cfg.ASR_DEVICE,
                language, effective_engine, cfg.ASR_COMPUTE_TYPE,
                multilingual, cfg.LID_CONFIDENCE_THRESHOLD, cfg.LID_MIN_SPEECH_DURATION_MS,
                translate_to_english, align,
                hotwords="", phone_mode=phone_mode,
                initial_prompt=initial_prompt,
            )
            asr_result = await loop.run_in_executor(None, transcribe_b)
            return enhanced_path, _enhanced_16k, asr_result

        enhance_asr_task = _enhance_and_transcribe()
        diar_task = loop.run_in_executor(None, diarize_func)
        (enhanced_path, enhanced_16k, asr_result_b), diarization_result = await asyncio.gather(
            enhance_asr_task, diar_task
        )
        elapsed = time.time() - t0

        # Record Resemble Enhance results
        result.resemble_available = "fallback" not in enhanced_path.name
        path_b_serve = temp_dir / "path_b_output.wav"
        shutil.copy2(str(enhanced_16k), str(path_b_serve))
        result.path_b_audio = path_b_serve.name
        audio_b, sr_b = sf.read(str(path_b_serve), dtype="float32")
        result.path_b_snr = estimate_snr(audio_b, sr_b)
        result.path_b_duration = round(len(audio_b) / sr_b, 2)
        result.steps.append({
            "step": "Resemble Enhance",
            "status": "applied",
            "reason": (
                f"Audio enhanced via Resemble Enhance microservice. "
                f"{'Microservice available' if result.resemble_available else 'Fallback (copy) — microservice not running'}."
            ),
        })

        # Record diarization results
        result.diarization_enabled = True
        result.diarization_result = diarization_result
        result.diarization_time_seconds = elapsed

        if job_id: await update_progress(job_id, 35, "Verifying speaker clusters...")
        diar_reason = (
            f"Pyannote detected {diarization_result['num_speakers']} speaker(s) "
            f"via {diarization_result['method']} method "
            f"in {result.diarization_time_seconds:.1f}s (parallel with Enhance+ASR)."
        )
        verification = diarization_result.get("verification")
        if verification:
            v = verification
            diar_reason += (
                f" Embedding verification: {v['original_clusters']} → {v['final_clusters']} clusters"
                f" ({v['merged_clusters']} merged, {v['segments_reassigned']} segments reassigned)"
                f" in {v['processing_time_seconds']}s."
            )
        result.steps.append({
            "step": "Speaker Diarization",
            "status": "applied",
            "reason": diar_reason,
        })

        # Record Path B ASR results
        result.path_b_time_seconds = elapsed
        result.path_b_transcription = asr_result_b["text"]
        result.path_b_char_count = asr_result_b["char_count"]

        # Gated by MEMOIR_FREE_GPU_MODELS env (auto-set by vast-start.sh).
        # Free ASR + diarization models before punctuation + alignment.
        _free_gpu_models()

        # Restore punctuation before alignment and speaker assignment (Fix #6)
        logger.info("=== Punctuation Restoration — Path B ===")
        _restore_punctuation(asr_result_b["segments"])

        # Align words BEFORE speaker assignment for precise word-level splitting
        if align:
            logger.info("=== Step 4.5b: Forced Alignment — Path B (before speaker assignment) ===")
            t0_align = time.time()
            align_func_b = partial(
                align_segments, enhanced_16k, asr_result_b["segments"],
                cfg.ALIGNMENT_DEVICE, cfg.ALIGNMENT_BATCH_SIZE,
            )
            aligned_b_segments = await loop.run_in_executor(None, align_func_b)
            result.alignment_enabled = True
            result.alignment_time_seconds += time.time() - t0_align
        else:
            aligned_b_segments = asr_result_b["segments"]

        result.path_b_segments = assign_speakers_to_segments(
            diarization_result, aligned_b_segments,
            audio_path=enhanced_16k, config=cfg,
        )

        # Mark path_b ASR as done so Step 5 below is skipped
        result._path_b_asr_done = True

        logger.info(f"Parallel (Enhance→ASR) + Diarization completed in {elapsed:.1f}s")

    else:
        # ── Sequential: Resemble Enhance, then Diarization, then ASR ──
        if processing_mode in ("path_b", "both"):
            if job_id: await update_progress(job_id, 18, "Enhancing audio...")
            logger.info("=== Step 3.4: Resemble Enhance ===")
            enhanced_path = await loop.run_in_executor(
                None, apply_resemble_enhance, processed_path, temp_dir, cfg.RESEMBLE_ENHANCE_URL
            )
            result.resemble_available = "fallback" not in enhanced_path.name
            enhanced_16k = await resample_to_16k(enhanced_path, temp_dir)
            path_b_serve = temp_dir / "path_b_output.wav"
            shutil.copy2(str(enhanced_16k), str(path_b_serve))
            result.path_b_audio = path_b_serve.name
            audio_b, sr_b = sf.read(str(path_b_serve), dtype="float32")
            result.path_b_snr = estimate_snr(audio_b, sr_b)
            result.path_b_duration = round(len(audio_b) / sr_b, 2)
            result.steps.append({
                "step": "Resemble Enhance",
                "status": "applied",
                "reason": (
                    f"Audio enhanced via Resemble Enhance microservice. "
                    f"{'Microservice available' if result.resemble_available else 'Fallback (copy) — microservice not running'}."
                ),
            })
        else:
            result.steps.append({
                "step": "Resemble Enhance",
                "status": "skipped",
                "reason": "Path A selected — Resemble Enhance only applies to Path B.",
            })

        # Path A parallel (existing): diarization + ASR on GPU
        if diarize and transcribe_a is not None and cfg.ASR_DEVICE == "cuda":
            if job_id: await update_progress(job_id, 22, "Identifying speakers + Transcribing audio...")
            logger.info("=== Steps 3.5+4: Parallel diarization + ASR (GPU mode) ===")
            t0 = time.time()
            diar_task = loop.run_in_executor(None, diarize_func)
            asr_task = loop.run_in_executor(None, transcribe_a)
            diarization_result, asr_result_a = await asyncio.gather(diar_task, asr_task)
            elapsed = time.time() - t0

            result.diarization_enabled = True
            result.diarization_result = diarization_result
            result.diarization_time_seconds = elapsed

            result.path_a_time_seconds = elapsed
            result.path_a_transcription = asr_result_a["text"]
            result.path_a_char_count = asr_result_a["char_count"]

            # Restore punctuation before alignment and speaker assignment (Fix #6)
            logger.info("=== Punctuation Restoration — Path A ===")
            _restore_punctuation(asr_result_a["segments"])

            if job_id: await update_progress(job_id, 35, "Verifying speaker clusters...")
            diar_reason = (
                f"Pyannote detected {diarization_result['num_speakers']} speaker(s) "
                f"via {diarization_result['method']} method "
                f"in {result.diarization_time_seconds:.1f}s (parallel with ASR)."
            )
            verification = diarization_result.get("verification")
            if verification:
                v = verification
                diar_reason += (
                    f" Embedding verification: {v['original_clusters']} → {v['final_clusters']} clusters"
                    f" ({v['merged_clusters']} merged, {v['segments_reassigned']} segments reassigned)"
                    f" in {v['processing_time_seconds']}s."
                )
            result.steps.append({
                "step": "Speaker Diarization",
                "status": "applied",
                "reason": diar_reason,
            })

            # Align words BEFORE speaker assignment for precise word-level splitting
            if align:
                logger.info("=== Step 4.5a: Forced Alignment — Path A (before speaker assignment) ===")
                t0_align = time.time()
                align_func_a = partial(
                    align_segments, processed_path, asr_result_a["segments"],
                    cfg.ALIGNMENT_DEVICE, cfg.ALIGNMENT_BATCH_SIZE,
                )
                aligned_a_segments = await loop.run_in_executor(None, align_func_a)
                result.alignment_enabled = True
                result.alignment_time_seconds += time.time() - t0_align
            else:
                aligned_a_segments = asr_result_a["segments"]

            result.path_a_segments = assign_speakers_to_segments(
                diarization_result, aligned_a_segments,
                audio_path=processed_path, config=cfg,
            )
        else:
            # CPU: fully sequential
            if diarize:
                if job_id: await update_progress(job_id, 22, "Identifying speakers...")
                logger.info("=== Step 3.5: Speaker Diarization ===")
                t0 = time.time()
                diarization_result = await loop.run_in_executor(None, diarize_func)
                result.diarization_enabled = True
                result.diarization_result = diarization_result
                result.diarization_time_seconds = time.time() - t0

                if job_id: await update_progress(job_id, 30, "Verifying speaker clusters...")
                diar_reason = (
                    f"Pyannote detected {diarization_result['num_speakers']} speaker(s) "
                    f"via {diarization_result['method']} method "
                    f"in {result.diarization_time_seconds:.1f}s."
                )
                verification = diarization_result.get("verification")
                if verification:
                    v = verification
                    diar_reason += (
                        f" Embedding verification: {v['original_clusters']} → {v['final_clusters']} clusters"
                        f" ({v['merged_clusters']} merged, {v['segments_reassigned']} segments reassigned)"
                        f" in {v['processing_time_seconds']}s."
                    )
                result.steps.append({
                    "step": "Speaker Diarization",
                    "status": "applied",
                    "reason": diar_reason,
                })
            else:
                result.steps.append({
                    "step": "Speaker Diarization",
                    "status": "skipped",
                    "reason": "Not enabled. Toggle diarization in the UI to identify speakers.",
                })

            # ── 4. Path A: Direct ASR (sequential) ──
            if job_id: await update_progress(job_id, 35, "Transcribing audio...")
            if transcribe_a is not None:
                t0 = time.time()
                asr_result_a = await loop.run_in_executor(None, transcribe_a)
                result.path_a_time_seconds = time.time() - t0
                result.path_a_transcription = asr_result_a["text"]
                result.path_a_char_count = asr_result_a["char_count"]

                # Restore punctuation before alignment and speaker assignment (Fix #6)
                logger.info("=== Punctuation Restoration — Path A ===")
                _restore_punctuation(asr_result_a["segments"])

                # Align words BEFORE speaker assignment
                a_segs = asr_result_a["segments"]
                if align:
                    logger.info("=== Step 4.5a: Forced Alignment — Path A (before speaker assignment) ===")
                    t0_align = time.time()
                    align_func_a = partial(
                        align_segments, processed_path, a_segs,
                        cfg.ALIGNMENT_DEVICE, cfg.ALIGNMENT_BATCH_SIZE,
                    )
                    a_segs = await loop.run_in_executor(None, align_func_a)
                    result.alignment_enabled = True
                    result.alignment_time_seconds += time.time() - t0_align

                if diarization_result:
                    result.path_a_segments = assign_speakers_to_segments(
                        diarization_result, a_segs,
                        audio_path=processed_path, config=cfg,
                    )
                else:
                    result.path_a_segments = a_segs

    # ── Post-ASR steps for Path A (remapping, vocab, LLM) ──
    # Note: alignment now runs BEFORE speaker assignment (above)
    if processing_mode in ("path_a", "both") and result.path_a_segments:
        # Remap timestamps to original audio time
        _remap_segments(result.path_a_segments, vad_boundaries)

        # ── Step 4.7a: Vocabulary Correction for Path A ──
        # When LLM is available: collect_only=True (word matches go to LLM for context review)
        # When LLM is unavailable: collect_only=False (abbreviation normalization only)
        if job_id: await update_progress(job_id, 55, "Processing transcript...")
        corrected_indices_a = set()
        vocab_candidates_a = []
        if vocab and result.path_a_segments:
            from app.audio_preprocess.services.vocabulary import apply_vocabulary_corrections
            if job_id: await update_progress(job_id, 58, "Correcting vocabulary...")
            logger.info("=== Step 4.7a: Vocabulary Correction — Path A ===")
            use_llm_a = result.llm_correction_enabled and result.llm_available
            t0 = time.time()
            result.path_a_segments, corrections_a, corrected_indices_a, vocab_candidates_a = apply_vocabulary_corrections(
                result.path_a_segments, vocab, cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                collect_only=use_llm_a,
            )
            result.vocabulary_corrections_a = corrections_a
            result.vocabulary_correction_time += time.time() - t0
            # Update transcription text from corrected segments
            result.path_a_transcription = " ".join(
                s.get("text", "") for s in result.path_a_segments
            ).strip()

        # ── Step 4.8a: LLM Context Correction for Path A ──
        if vocab and result.llm_correction_enabled and result.llm_available and result.path_a_segments:
            try:
                from app.audio_preprocess.services.llm_corrector import LLMCorrector
                if job_id: await update_progress(job_id, 62, "Applying context corrections...")
                logger.info("=== Step 4.8a: LLM Context Correction — Path A ===")
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
                result.path_a_segments, llm_corrections_a = corrector.correct_segments(
                    result.path_a_segments, corrected_indices_a,
                    vocab_candidates=vocab_candidates_a,
                )
                result.llm_corrections_a = llm_corrections_a
                result.llm_corrector_calls += corrector.call_count
                result.llm_correction_time += time.time() - t0
                # Update transcription text from LLM-corrected segments
                result.path_a_transcription = " ".join(
                    s.get("text", "") for s in result.path_a_segments
                ).strip()
            except Exception as e:
                logger.warning(f"LLM correction failed for Path A: {e}")
    elif processing_mode not in ("path_a", "both"):
        logger.info("=== Path A skipped (user selected Path B only) ===")

    # ── 5. Path B: ASR on enhanced audio (skip if already done in parallel) ──
    if processing_mode in ("path_b", "both") and not getattr(result, '_path_b_asr_done', False):
        if job_id: await update_progress(job_id, 35, "Transcribing enhanced audio...")
        logger.info("=== Step 5: Path B — ASR on enhanced audio ===")
        t0 = time.time()

        transcribe_b = partial(
            transcribe, enhanced_16k, effective_model_size, cfg.ASR_DEVICE,
            language, effective_engine, cfg.ASR_COMPUTE_TYPE,
            multilingual, cfg.LID_CONFIDENCE_THRESHOLD, cfg.LID_MIN_SPEECH_DURATION_MS,
            translate_to_english, align,
            hotwords="", phone_mode=phone_mode,
            initial_prompt=initial_prompt,
        )
        asr_result_b = await loop.run_in_executor(None, transcribe_b)
        result.path_b_time_seconds = time.time() - t0
        result.path_b_transcription = asr_result_b["text"]
        result.path_b_char_count = asr_result_b["char_count"]

        # Restore punctuation before alignment and speaker assignment (Fix #6)
        logger.info("=== Punctuation Restoration — Path B ===")
        _restore_punctuation(asr_result_b["segments"])

        # Align words BEFORE speaker assignment
        b_segs = asr_result_b["segments"]
        if align:
            logger.info("=== Step 4.5b: Forced Alignment — Path B (before speaker assignment) ===")
            t0_align = time.time()
            align_func_b = partial(
                align_segments, enhanced_16k, b_segs,
                cfg.ALIGNMENT_DEVICE, cfg.ALIGNMENT_BATCH_SIZE,
            )
            b_segs = await loop.run_in_executor(None, align_func_b)
            result.alignment_enabled = True
            result.alignment_time_seconds += time.time() - t0_align

        if diarization_result:
            result.path_b_segments = assign_speakers_to_segments(
                diarization_result, b_segs,
                audio_path=enhanced_16k, config=cfg,
            )
        else:
            result.path_b_segments = b_segs

    # ── Post-ASR steps for Path B (remapping, vocab, LLM) ──
    # Note: alignment now runs BEFORE speaker assignment (above)
    if processing_mode in ("path_b", "both") and result.path_b_segments:
        # Remap timestamps to original audio time
        _remap_segments(result.path_b_segments, vad_boundaries)

        # Gated by MEMOIR_FREE_GPU_MODELS env (auto-set by vast-start.sh).
        # Second cleanup: free ONNX aligner before Ollama LLM calls.
        # ASR/diarization already freed before alignment (line ~695).
        _free_gpu_models()

        # ── Step 4.7b: Vocabulary Correction for Path B ──
        corrected_indices_b = set()
        vocab_candidates_b = []
        if vocab and result.path_b_segments:
            from app.audio_preprocess.services.vocabulary import apply_vocabulary_corrections
            logger.info("=== Step 4.7b: Vocabulary Correction — Path B ===")
            use_llm_b = result.llm_correction_enabled and result.llm_available
            t0 = time.time()
            result.path_b_segments, corrections_b, corrected_indices_b, vocab_candidates_b = apply_vocabulary_corrections(
                result.path_b_segments, vocab, cfg.VOCABULARY_CONFIDENCE_THRESHOLD,
                collect_only=use_llm_b,
            )
            result.vocabulary_corrections_b = corrections_b
            result.vocabulary_correction_time += time.time() - t0
            result.path_b_transcription = " ".join(
                s.get("text", "") for s in result.path_b_segments
            ).strip()

        # ── Step 4.8b: LLM Context Correction for Path B ──
        if vocab and result.llm_correction_enabled and result.llm_available and result.path_b_segments:
            try:
                from app.audio_preprocess.services.llm_corrector import LLMCorrector
                logger.info("=== Step 4.8b: LLM Context Correction — Path B ===")
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
                result.path_b_segments, llm_corrections_b = corrector.correct_segments(
                    result.path_b_segments, corrected_indices_b,
                    vocab_candidates=vocab_candidates_b,
                )
                result.llm_corrections_b = llm_corrections_b
                result.llm_corrector_calls += corrector.call_count
                result.llm_correction_time += time.time() - t0
                result.path_b_transcription = " ".join(
                    s.get("text", "") for s in result.path_b_segments
                ).strip()
            except Exception as e:
                logger.warning(f"LLM correction failed for Path B: {e}")
    elif processing_mode not in ("path_b", "both"):
        logger.info("=== Path B skipped (user selected Path A only) ===")

    # Final capitalization pass — ensures first letter is uppercase after all corrections
    for segs in (result.path_a_segments, result.path_b_segments):
        for seg in segs:
            text = seg.get("text", "")
            if text and text[0].islower():
                seg["text"] = text[0].upper() + text[1:]

    # Add alignment "applied" step now that we know the timing
    if align and result.alignment_enabled:
        total_words = 0
        for segs in (result.path_a_segments, result.path_b_segments):
            for seg in segs:
                total_words += len(seg.get("words", []))
        result.steps.append({
            "step": "Word-Level Alignment",
            "status": "applied",
            "reason": (
                f"ctc-forced-aligner (MMS-300M) produced word-level timestamps "
                f"for {total_words} words in {result.alignment_time_seconds:.1f}s. "
                f"Each word is located within ~50ms accuracy."
            ),
        })

    # Add vocabulary correction step summary
    if vocab:
        total_corrections = len(result.vocabulary_corrections_a) + len(result.vocabulary_corrections_b)
        result.steps.append({
            "step": "Vocabulary Correction (5a)",
            "status": "applied",
            "reason": (
                f"Vocabulary '{vocab.name}' ({len(vocab.entries)} terms). "
                f"Corrections applied: {total_corrections} (exact + sounds_like + abbreviation). "
                f"Time: {result.vocabulary_correction_time:.1f}s."
            ),
        })
    elif not vocab_correction:
        result.steps.append({
            "step": "Vocabulary Correction",
            "status": "skipped",
            "reason": "Vocabulary correction not enabled. Check 'Enable Vocabulary Correction' in the UI.",
        })
    elif not vocabulary_files:
        result.steps.append({
            "step": "Vocabulary Correction",
            "status": "skipped",
            "reason": "No vocabulary selected. Choose a vocabulary in the UI for domain-specific corrections.",
        })

    # Add LLM correction step summary
    if result.llm_correction_enabled:
        if result.llm_available:
            total_llm = len(result.llm_corrections_a) + len(result.llm_corrections_b)
            applied = sum(
                1 for c in result.llm_corrections_a + result.llm_corrections_b
                if c.get("correction_type") == "llm_context"
            )
            rejected = sum(
                1 for c in result.llm_corrections_a + result.llm_corrections_b
                if c.get("correction_type") == "llm_rejected"
            )
            result.steps.append({
                "step": "LLM Context Correction (5b)",
                "status": "applied",
                "reason": (
                    f"Qwen 2.5 7B via Ollama reviewed {total_llm} phonetic candidates. "
                    f"Applied: {applied}, rejected: {rejected}. "
                    f"Time: {result.llm_correction_time:.1f}s."
                ),
            })
        else:
            result.steps.append({
                "step": "LLM Context Correction (5b)",
                "status": "skipped",
                "reason": "LLM correction enabled but Ollama is not available. Start Ollama to enable.",
            })
    elif vocab and not llm_correction:
        result.steps.append({
            "step": "LLM Context Correction (5b)",
            "status": "skipped",
            "reason": "Not enabled. Check 'Enable LLM Correction' for context-aware phonetic corrections.",
        })

    # ── 6. Intelligence / Memoir Extraction ──
    # Use Path A segments as primary source (most common mode)
    primary_segments = result.path_a_segments if result.path_a_segments else result.path_b_segments
    result.post_processing_type = post_processing_type

    if result.intelligence_enabled and result.intelligence_available and primary_segments:
        t0 = time.time()
        total_duration = result.path_a_duration or result.path_b_duration or 0.0
        num_speakers = result.diarization_result.get("num_speakers", 2) if result.diarization_result else 2
        detected_lang = language or "en"

        # Step 6.1: Coaching metrics (always computed — shared by both flows)
        if job_id: await update_progress(job_id, 66, "Computing coaching metrics...")
        from app.audio_preprocess.services.coaching_metrics import compute_coaching_metrics
        result.coaching_metrics = compute_coaching_metrics(
            primary_segments, total_duration,
        )
        logger.info("Step 6.1: Coaching metrics computed")

        if post_processing_type == "memoir":
            # Memoir extraction moved to intelligence pipeline (operation_type=3)
            # Runs after speaker mapping for correct participant names
            logger.info("Skipping Phase 10 Memoir (moved to intelligence pipeline)")
        else:
            # Step 6.2: Phase 7 Intelligence extraction + 6.3: Scoring (7B)
            logger.info("=== Step 6: Intelligence Extraction (Phase 7) ===")
            from app.audio_preprocess.services.intelligence import run_full_intelligence

            intelligence = await loop.run_in_executor(
                None,
                partial(
                    run_full_intelligence,
                    segments=primary_segments,
                    total_duration=total_duration,
                    num_speakers=num_speakers,
                    language=detected_lang,
                    coaching_metrics=result.coaching_metrics,
                    base_url=cfg.LLM_BASE_URL,
                    model=cfg.INTELLIGENCE_LLM_MODEL,
                    temperature=cfg.LLM_TEMPERATURE,
                    timeout=cfg.LLM_TIMEOUT,
                    max_single_pass_tokens=cfg.INTELLIGENCE_MAX_SINGLE_PASS_TOKENS,
                    num_ctx=cfg.INTELLIGENCE_NUM_CTX,
                ),
            )

            result.intelligence_time = time.time() - t0

            if intelligence:
                result.intelligence_result = intelligence
                logger.info("Intelligence extraction completed in %.1fs", result.intelligence_time)
                result.steps.append({
                    "step": "Intelligence Extraction (Phase 7)",
                    "status": "applied",
                    "reason": (
                        f"LLM extraction ({cfg.INTELLIGENCE_LLM_MODEL}) + coaching metrics + "
                        f"MEDDIC/BANT scoring + deal health + coaching scorecard. "
                        f"Time: {result.intelligence_time:.1f}s."
                    ),
                })
            else:
                logger.warning("Intelligence extraction returned no results")
                result.steps.append({
                    "step": "Intelligence Extraction (Phase 7)",
                    "status": "applied",
                    "reason": "LLM extraction was attempted but returned no results. Check Ollama logs.",
                })
    elif result.intelligence_enabled and not result.intelligence_available:
        result.steps.append({
            "step": "Intelligence / Memoir Extraction",
            "status": "skipped",
            "reason": "Intelligence enabled but Ollama is not available. Start Ollama to enable.",
        })
    elif not enable_intelligence:
        result.steps.append({
            "step": "Intelligence / Memoir Extraction",
            "status": "skipped",
            "reason": "Not enabled. Check 'Enable Intelligence' in the UI for call analysis.",
        })

    # Populate ASR processing time for cost tracking (primary path only)
    if processing_mode in ("path_b", "both") and result.path_b_time_seconds > 0:
        result.asr_processing_seconds = result.path_b_time_seconds
    elif result.path_a_time_seconds > 0:
        result.asr_processing_seconds = result.path_a_time_seconds

    # Gated by MEMOIR_FREE_GPU_MODELS env (auto-set by vast-start.sh).
    # Final cleanup: ensure clean VRAM state for next upload.
    _free_gpu_models()

    if job_id: await update_progress(job_id, 68, "Pipeline complete")
    logger.info("=== Pipeline complete ===")
    return result
