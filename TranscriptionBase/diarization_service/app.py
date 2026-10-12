"""Diarization microservice — pyannote.audio 4.0 community-1.

Runs in /opt/pyannote-venv (torch 2.5.1+cu124, pyannote 4.0.4).
TranscriptionBase POSTs to /diarize and consumes the standardized JSON output.
Listens on port 8012 by default (8011 is vLLM, 8010 is Resemble).

This service owns ONLY the pyannote pipeline call. All post-processing
(embedding verification, boundary snap, gap fill) stays in TranscriptionBase
main env and runs against the speakers list returned by this service.
"""
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("diarization_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DIARIZATION_MODEL = os.getenv("DIARIZATION_MODEL_4", "pyannote/speaker-diarization-community-1")
DEVICE = os.getenv("DIARIZATION_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
HF_TOKEN = os.getenv("HF_AUTH_TOKEN") or os.getenv("HF_TOKEN")

# Lazy GPU residency. When true (default), the pipeline lives in CPU memory
# between requests and is moved to GPU only for the duration of each /diarize
# call. Costs ~2-3s per call for CPU↔GPU transfer of ~3 GB of weights, but
# frees that GPU memory for other tenants of the same physical GPU (vLLM,
# faster-whisper, ONNX aligner). Set DIARIZATION_LAZY_GPU=false when this
# service has a GPU to itself or on 24 GB+ instances where co-residency
# isn't a problem.
# See plan/pyannote_4_migration.md §15 for the 24 GB migration TODO.
LAZY_GPU = os.getenv("DIARIZATION_LAZY_GPU", "true").lower() == "true"

_pipeline = None  # process-global singleton
_pipeline_defaults: dict = {}  # snapshot of clustering hyperparams at load time

# Serializes the apply-overrides + pipeline(...) critical section. The pipeline
# object is a process-global singleton and exposes its clustering hyperparams
# as mutable attributes; concurrent requests would race on those mutations
# (request A sets threshold=0.7, request B overwrites to 0.5 before A's
# inference reads it). FastAPI runs sync endpoints in a threadpool, so use
# threading.Lock. Pyannote diarization is also not designed for concurrent
# calls on the same pipeline instance — even without our overrides, two
# overlapping pipeline(audio) calls would share CUDA state and produce
# unpredictable results. Serializing is correct semantically AND has zero
# throughput cost for our deployment (worker MAX_CONCURRENT_JOBS=1 means
# one diarization at a time per GPU instance anyway).
_pipeline_lock = threading.Lock()


def _load_pipeline():
    """Load model into CPU memory (cheap; ~3 GB host RAM, no VRAM).
    When LAZY_GPU=true, pipeline stays on CPU between requests and is moved
    to GPU only inside the /diarize critical section. When LAZY_GPU=false,
    pipeline is moved to GPU here and stays resident.
    """
    global _pipeline, _pipeline_defaults
    if _pipeline is not None:
        return _pipeline
    from pyannote.audio import Pipeline
    if HF_TOKEN:
        # 4.0 reads HF_TOKEN; 3.x read HF_AUTH_TOKEN. Set both for safety.
        os.environ["HF_TOKEN"] = HF_TOKEN
        os.environ["HF_AUTH_TOKEN"] = HF_TOKEN
    target = "cpu" if LAZY_GPU else DEVICE
    logger.info("Loading %s on %s (LAZY_GPU=%s) ...", DIARIZATION_MODEL, target, LAZY_GPU)
    pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=HF_TOKEN)
    pipeline.to(torch.device(target))

    # Snapshot the as-loaded clustering hyperparams so per-request overrides
    # can be cleanly restored. Without this, request A's override (threshold=0.7)
    # leaks into request B that sends 0.0 ("use default"). Cross-request state
    # bleed is silent and hard to diagnose — this snapshot prevents it.
    snapshot = {}
    if hasattr(pipeline, "clustering"):
        for attr in ("threshold", "min_cluster_size"):
            if hasattr(pipeline.clustering, attr):
                snapshot[attr] = getattr(pipeline.clustering, attr)
    _pipeline_defaults = snapshot
    logger.info("Snapshot defaults: %s", snapshot)

    _pipeline = pipeline
    logger.info("Pipeline loaded")
    return _pipeline


def _to_gpu(pipeline) -> float:
    """Move pipeline weights to GPU. Returns transfer time in seconds.
    No-op if LAZY_GPU=false (model already resident on GPU)."""
    if not LAZY_GPU:
        return 0.0
    t0 = time.monotonic()
    pipeline.to(torch.device(DEVICE))
    return time.monotonic() - t0


def _to_cpu(pipeline) -> float:
    """Move pipeline weights back to CPU + flush CUDA cache. Returns time in seconds.
    No-op if LAZY_GPU=false."""
    if not LAZY_GPU:
        return 0.0
    t0 = time.monotonic()
    pipeline.to(torch.device("cpu"))
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return time.monotonic() - t0


def _apply_clustering_overrides(pipeline, clustering_threshold: float, min_cluster_size: int) -> None:
    """Set clustering hyperparams for THIS request, restoring defaults when
    the request sends 0 (meaning "use as-loaded default"). Must be called on
    every request — there is no "remember last value" semantic.
    """
    if not hasattr(pipeline, "clustering"):
        return

    # threshold: override > 0 → set; 0.0 → restore default
    if hasattr(pipeline.clustering, "threshold"):
        if clustering_threshold > 0.0:
            pipeline.clustering.threshold = clustering_threshold
        elif "threshold" in _pipeline_defaults:
            pipeline.clustering.threshold = _pipeline_defaults["threshold"]

    # min_cluster_size: override > 0 → set; 0 → restore default
    if hasattr(pipeline.clustering, "min_cluster_size"):
        if min_cluster_size > 0:
            pipeline.clustering.min_cluster_size = min_cluster_size
        elif "min_cluster_size" in _pipeline_defaults:
            pipeline.clustering.min_cluster_size = _pipeline_defaults["min_cluster_size"]


app = FastAPI(title="Diarization Service", version="1.0.0")


class DiarizeRequest(BaseModel):
    audio_path: str = Field(..., description="Absolute path to mono WAV on shared filesystem")
    num_speakers: int = Field(0, ge=0, le=20, description="0 = auto")
    clustering_threshold: float = Field(0.0, description="0.0 = use pipeline default")
    min_cluster_size: int = Field(0, description="0 = use pipeline default")
    min_speakers: int = Field(2, ge=1, le=20)
    max_speakers: int = Field(10, ge=1, le=20)


class SpeakerSegment(BaseModel):
    speaker: str
    start: float
    end: float


class DiarizeResponse(BaseModel):
    speakers: list[SpeakerSegment]
    num_speakers: int
    method: str
    processing_time_seconds: float


@app.on_event("startup")
def warmup():
    try:
        _load_pipeline()
    except Exception as e:
        # Don't crash service if HF gate isn't accepted yet; surface on first request
        logger.warning("Pipeline pre-load failed (will retry on first request): %s", e)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": DIARIZATION_MODEL,
        "device": DEVICE,
        "loaded": _pipeline is not None,
        "lazy_gpu": LAZY_GPU,
    }


@app.post("/diarize", response_model=DiarizeResponse)
def diarize(req: DiarizeRequest):
    audio = Path(req.audio_path)
    if not audio.is_file():
        raise HTTPException(status_code=400, detail=f"Audio not found: {req.audio_path}")

    try:
        pipeline = _load_pipeline()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline load failed: {e}")

    kwargs = {}
    if req.num_speakers > 0:
        kwargs["num_speakers"] = req.num_speakers
    else:
        kwargs["min_speakers"] = req.min_speakers
        kwargs["max_speakers"] = req.max_speakers

    # Pre-load audio with torchaudio (bypasses torchcodec). pyannote 4.0
    # switched its built-in file decoder to torchcodec; without it,
    # pipeline(str(path)) raises NameError: AudioDecoder is not defined.
    # Passing {'waveform', 'sample_rate'} dict skips the decode step.
    #
    # Keep the waveform on CPU here. With LAZY_GPU=true, the pipeline is also
    # on CPU until we move it inside the lock; both must be on the same device
    # at inference time. The actual cuda move happens inside the locked section
    # below — after `_to_gpu(pipeline)`.
    try:
        import torchaudio
        waveform, sr = torchaudio.load(str(audio))
        if sr != 16000:
            waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
            sr = 16000
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio load failed: {e}")

    # Serialize the apply-overrides + inference critical section so concurrent
    # requests can't race on the singleton pipeline's mutable clustering attrs.
    # Lock is held for the whole call — pyannote pipelines aren't thread-safe
    # under concurrent inference on the same instance anyway. The lock is
    # released as soon as pipeline(...) returns so the response parsing /
    # serialization runs unlocked.
    lock_wait_t0 = time.monotonic()
    with _pipeline_lock:
        lock_wait = time.monotonic() - lock_wait_t0
        if lock_wait > 0.1:
            logger.info("Pipeline lock acquired after %.2fs wait", lock_wait)

        # Lazy GPU residency: move weights to GPU just for this request, then
        # back to CPU in the finally block. Frees ~3 GB VRAM between calls.
        # No-op if LAZY_GPU=false (single-tenant GPU scenario).
        gpu_in = _to_gpu(pipeline)
        if gpu_in > 0:
            logger.info("Pipeline moved to GPU in %.2fs", gpu_in)

        try:
            # Audio waveform must live on the same device as the pipeline.
            # Move it AFTER the pipeline (LAZY_GPU=true) or directly to GPU
            # (LAZY_GPU=false — pipeline already there).
            audio_input = {
                "waveform": waveform.to(torch.device(DEVICE)),
                "sample_rate": sr,
            }

            try:
                _apply_clustering_overrides(pipeline, req.clustering_threshold, req.min_cluster_size)
            except Exception as e:
                logger.warning("Clustering override apply failed (continuing with current state): %s", e)

            t0 = time.monotonic()
            try:
                output = pipeline(audio_input, **kwargs)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Pyannote pipeline failed: {e}")
            elapsed = time.monotonic() - t0
        finally:
            gpu_out = _to_cpu(pipeline)
            if gpu_out > 0:
                logger.info("Pipeline moved back to CPU + cache flushed in %.2fs", gpu_out)

    # 4.0 API shim: DiarizeOutput.speaker_diarization → Annotation
    annotation = getattr(output, "speaker_diarization", output)

    segments: list[SpeakerSegment] = []
    speakers_seen: set[str] = set()
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append(SpeakerSegment(speaker=speaker, start=float(turn.start), end=float(turn.end)))
        speakers_seen.add(speaker)

    logger.info(
        "Diarized %s in %.1fs → %d segs, %d speakers",
        audio.name, elapsed, len(segments), len(speakers_seen),
    )

    return DiarizeResponse(
        speakers=segments,
        num_speakers=len(speakers_seen),
        method="pyannote-4.0-community-1",
        processing_time_seconds=round(elapsed, 2),
    )
