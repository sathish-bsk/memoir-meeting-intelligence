"""
Resemble Enhance Microservice — isolated environment with torch 2.1.1.

Accepts audio files via HTTP, runs resemble-enhance denoiser,
returns enhanced audio. Stateless — no model caching beyond what
resemble-enhance does internally.
"""
import io
import logging
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resemble-enhance-service")

app = FastAPI(
    title="Resemble Enhance Microservice",
    description="Isolated speech enhancement service (torch 2.1.1)",
    version="1.0.0",
)


@app.get("/health")
async def health():
    """Health check — verifies resemble-enhance is importable."""
    try:
        from resemble_enhance.enhancer.inference import denoise
        import torch
        return {
            "status": "healthy",
            "torch_version": torch.__version__,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
        }
    except ImportError as e:
        return {"status": "unhealthy", "error": str(e)}


@app.post("/enhance")
async def enhance_audio(file: UploadFile = File(...)):
    """
    Accept a WAV file, run resemble-enhance denoiser, return enhanced WAV.

    Input: WAV file (any sample rate, mono or stereo)
    Output: Enhanced WAV file (sample rate determined by resemble-enhance)
    """
    try:
        from resemble_enhance.enhancer.inference import denoise
        import torch
        import torchaudio
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"resemble-enhance not available: {e}",
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"

    try:
        # Save uploaded file to temp location
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        logger.info(f"Enhancing {file.filename} ({len(content)} bytes, device={device})")

        # Load audio
        waveform, sr = torchaudio.load(str(tmp_path))

        # Resemble Enhance expects mono
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        waveform = waveform.to(device)

        # Run denoiser (lighter and safer than full enhance)
        denoised, new_sr = denoise(waveform.squeeze(), sr, device)

        # Convert to numpy and write to buffer
        audio_np = denoised.cpu().numpy()
        buffer = io.BytesIO()
        sf.write(buffer, audio_np, new_sr, format="WAV", subtype="PCM_16")
        buffer.seek(0)

        logger.info(f"Enhancement complete: {new_sr}Hz, {len(audio_np)} samples")

        # Cleanup temp file
        tmp_path.unlink(missing_ok=True)

        # Free GPU memory — model only needed during processing, not between requests.
        # Next request will reload from disk cache (~5s, acceptable for once-per-meeting use).
        del denoised, waveform
        _free_gpu()

        return Response(
            content=buffer.read(),
            media_type="audio/wav",
            headers={"Content-Disposition": f"attachment; filename=enhanced_{file.filename}"},
        )

    except Exception as e:
        logger.exception(f"Enhancement failed: {e}")
        # Cleanup temp file on error
        if "tmp_path" in locals():
            tmp_path.unlink(missing_ok=True)
        _free_gpu()
        raise HTTPException(status_code=500, detail=str(e))


def _free_gpu():
    """Unload resemble-enhance model and free all GPU memory."""
    try:
        import torch
        import gc
        # Clear the cached model inside resemble-enhance's module
        from resemble_enhance.enhancer import inference as _re_inf
        if hasattr(_re_inf, '_model'):
            _re_inf._model = None
        if hasattr(_re_inf, '_hparams'):
            _re_inf._hparams = None
        # Clear any module-level cached enhancer
        import sys
        for key in list(sys.modules.keys()):
            if 'resemble_enhance' in key:
                mod = sys.modules[key]
                for attr in dir(mod):
                    obj = getattr(mod, attr, None)
                    if isinstance(obj, torch.nn.Module):
                        setattr(mod, attr, None)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            vram = torch.cuda.memory_allocated() / 1e9
            logger.info(f"GPU memory freed. Remaining allocated: {vram:.2f} GB")
    except Exception as e:
        logger.warning(f"GPU cleanup failed (non-fatal): {e}")
