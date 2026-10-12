"""
Resemble Enhance — parallel path for Path B.
Calls the resemble-enhance microservice via HTTP to apply neural speech
enhancement for perceptual quality improvement.
NOT used in the direct ASR path (Path A).
"""
import logging
import uuid
from pathlib import Path

import httpx
import soundfile as sf

logger = logging.getLogger("audio_preprocess")


def check_enhance_available(service_url: str) -> bool:
    """Check if the resemble-enhance microservice is reachable and healthy."""
    try:
        resp = httpx.get(f"{service_url}/health", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("status") == "healthy"
        return False
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


def apply_resemble_enhance(input_path: Path, temp_dir: Path, service_url: str) -> Path:
    """
    Apply Resemble Enhance via microservice for perceptual quality improvement.
    This is Path B — the enhanced audio will be separately transcribed for comparison.

    Falls back to a copy of input if the microservice is unavailable.
    """
    logger.info(f"Applying Resemble Enhance via microservice ({service_url})...")

    try:
        with open(str(input_path), "rb") as f:
            files = {"file": (input_path.name, f, "audio/wav")}
            resp = httpx.post(
                f"{service_url}/enhance",
                files=files,
                timeout=httpx.Timeout(18000.0, connect=10.0),  # 5 hours for enhancement, 10s for connect
            )

        if resp.status_code != 200:
            logger.warning(f"Resemble Enhance microservice returned {resp.status_code}: {resp.text}")
            return _copy_as_fallback(input_path, temp_dir)

        # Save the enhanced audio
        output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_resemble.wav"
        with open(str(output_path), "wb") as f:
            f.write(resp.content)

        logger.info(f"Resemble Enhance complete: {output_path}")
        return output_path

    except httpx.ConnectError:
        logger.warning("Resemble Enhance microservice not reachable, copying input as fallback")
        return _copy_as_fallback(input_path, temp_dir)
    except httpx.TimeoutException:
        logger.warning("Resemble Enhance microservice timed out, copying input as fallback")
        return _copy_as_fallback(input_path, temp_dir)
    except Exception as e:
        logger.warning(f"Resemble Enhance failed ({e}), copying input as fallback")
        return _copy_as_fallback(input_path, temp_dir)


def _copy_as_fallback(input_path: Path, temp_dir: Path) -> Path:
    """Copy input file as fallback when Resemble Enhance is unavailable."""
    audio, sr = sf.read(str(input_path), dtype="float32")
    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_enhanced_fallback.wav"
    sf.write(str(output_path), audio, sr, subtype="PCM_16")
    return output_path
