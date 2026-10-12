"""
Core audio processing — always applied to every file.
1. Highpass 80Hz (remove rumble, NO lowpass)
2. Silero VAD silence trimming
3. Dual-pass FFmpeg loudnorm (-23 LUFS)
4. Resample to 16kHz mono
"""
import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from app.audio_preprocess.config import preprocess_config

logger = logging.getLogger("audio_preprocess")

# Silero VAD singleton
_vad_model = None
_vad_utils = None
_vad_lock = threading.Lock()


def _load_vad_model():
    global _vad_model, _vad_utils
    if _vad_model is not None:
        return _vad_model, _vad_utils
    with _vad_lock:
        if _vad_model is not None:
            return _vad_model, _vad_utils
        logger.info("Loading Silero VAD model...")
        _vad_model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        _vad_utils = utils
        logger.info("Silero VAD model loaded")
        return _vad_model, _vad_utils


async def resample_and_highpass(input_path: Path, temp_dir: Path, target_sr: int = 48000) -> Path:
    """Resample to target SR mono AND apply 80Hz highpass in a single FFmpeg pass."""
    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_{target_sr // 1000}k_highpass.wav"
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", "highpass=f=80",
        "-ar", str(target_sr),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Resample+highpass failed: {stderr.decode()}")
    logger.info(f"Resampled to {target_sr // 1000}kHz mono + highpass 80Hz applied: {output_path}")
    return output_path


async def apply_highpass(input_path: Path, temp_dir: Path) -> Path:
    """Apply 80Hz highpass filter (remove rumble). No lowpass — preserve high-freq info."""
    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_highpass.wav"
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", "highpass=f=80",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Highpass filter failed: {stderr.decode()}")
    logger.info(f"Highpass 80Hz applied: {output_path}")
    return output_path


def trim_silence_vad(wav_path: Path, temp_dir: Path, phone_mode: bool = False) -> tuple:
    """Trim silence using Silero VAD. Keeps speech segments with padding.

    Accepts any sample rate. Internally resamples to 16kHz for Silero VAD,
    then applies the detected boundaries to the original-SR audio.

    Returns:
        (trimmed_audio_path, vad_boundaries) where vad_boundaries is a list of
        {"start": float, "end": float} in seconds relative to the original audio.
    """
    cfg = preprocess_config
    if phone_mode:
        vad_threshold = cfg.PHONE_MODE_VAD_THRESHOLD
        min_speech_ms = cfg.PHONE_MODE_MIN_SPEECH_MS
        min_silence_ms = cfg.PHONE_MODE_MIN_SILENCE_MS
        speech_pad_ms = cfg.PHONE_MODE_SPEECH_PAD_MS
        logger.info(f"VAD silence trimming (phone mode: threshold={vad_threshold}, "
                     f"min_speech={min_speech_ms}ms, pad={speech_pad_ms}ms)...")
    else:
        vad_threshold = 0.5
        min_speech_ms = 250
        min_silence_ms = 300
        speech_pad_ms = 200
        logger.info("VAD silence trimming...")

    model, utils = _load_vad_model()
    get_speech_timestamps, _, _, _, collect_chunks = utils

    audio, sr = sf.read(str(wav_path), dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    # Silero VAD requires 16kHz — resample internally if needed
    if sr != 16000:
        import torchaudio
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)
        resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
        vad_audio = resampler(audio_tensor).squeeze(0)
        vad_sr = 16000
    else:
        vad_audio = torch.from_numpy(audio)
        vad_sr = sr

    speech_timestamps = get_speech_timestamps(
        vad_audio, model, sampling_rate=vad_sr,
        threshold=vad_threshold, min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=min_silence_ms, speech_pad_ms=speech_pad_ms,
    )

    if not speech_timestamps:
        logger.warning("VAD found no speech, returning original")
        return wav_path, []

    # Convert VAD boundaries (in 16kHz samples) to seconds
    vad_boundaries = [
        {"start": round(ts["start"] / vad_sr, 3), "end": round(ts["end"] / vad_sr, 3)}
        for ts in speech_timestamps
    ]

    # Apply boundaries to original-SR audio (not the 16kHz version)
    orig_tensor = torch.from_numpy(audio)
    orig_timestamps = [
        {"start": int(b["start"] * sr), "end": int(b["end"] * sr)}
        for b in vad_boundaries
    ]
    speech_audio = collect_chunks(orig_timestamps, orig_tensor)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_vad.wav"
    sf.write(str(output_path), speech_audio.numpy(), sr, subtype="PCM_16")

    orig_dur = len(audio) / sr
    trim_dur = len(speech_audio) / sr
    logger.info(f"VAD: {orig_dur:.1f}s → {trim_dur:.1f}s (removed {orig_dur - trim_dur:.1f}s), {len(vad_boundaries)} speech segments")
    return output_path, vad_boundaries


async def apply_loudnorm_dual_pass(
    input_path: Path, temp_dir: Path,
    target_i: float = -23.0, target_tp: float = -1.5, target_lra: float = 11.0,
) -> Path:
    """
    Apply EBU R128 loudness normalization using dual-pass (measure then apply).
    More accurate than single-pass for file-based processing.
    """
    logger.info("Loudnorm dual-pass: measuring...")

    # Pass 1: Measure loudness
    measure_cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *measure_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    stderr_text = stderr_bytes.decode()

    # Parse measured values from ffmpeg output
    try:
        json_start = stderr_text.rfind("{")
        json_end = stderr_text.rfind("}") + 1
        measured = json.loads(stderr_text[json_start:json_end])
    except (json.JSONDecodeError, ValueError):
        logger.warning("Dual-pass measure failed, falling back to single-pass")
        return await _apply_loudnorm_single_pass(input_path, temp_dir, target_i, target_tp, target_lra)

    measured_i = measured.get("input_i", "-23.0")
    measured_lra = measured.get("input_lra", "11.0")
    measured_tp = measured.get("input_tp", "-1.5")
    measured_thresh = measured.get("input_thresh", "-34.0")

    # If audio is silent/near-silent (-inf), skip loudnorm
    if measured_i == "-inf" or (isinstance(measured_i, str) and "inf" in measured_i.lower()):
        logger.warning("Audio is silent (measured_I=-inf), skipping loudnorm")
        return input_path

    # Pass 2: Apply with measured values (preserve input sample rate)
    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_loudnorm.wav"
    apply_cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", (
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
            f":measured_I={measured_i}:measured_LRA={measured_lra}"
            f":measured_TP={measured_tp}:measured_thresh={measured_thresh}"
            f":linear=true"
        ),
        "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *apply_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Loudnorm apply failed: {stderr.decode()}")

    logger.info(f"Loudnorm dual-pass complete: measured_I={measured_i}, output={output_path}")
    return output_path


async def _apply_loudnorm_single_pass(
    input_path: Path, temp_dir: Path,
    target_i: float, target_tp: float, target_lra: float,
) -> Path:
    """Fallback single-pass loudnorm (preserves input sample rate)."""
    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_loudnorm.wav"
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}",
        "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Loudnorm single-pass failed: {stderr.decode()}")
    return output_path


async def resample_to_16k(input_path: Path, temp_dir: Path) -> Path:
    """Resample audio to 16kHz mono WAV."""
    info = sf.info(str(input_path))
    if info.samplerate == 16000 and info.channels == 1:
        return input_path

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_16k.wav"
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Resample failed: {stderr.decode()}")
    logger.info(f"Resampled to 16kHz mono: {output_path}")
    return output_path
