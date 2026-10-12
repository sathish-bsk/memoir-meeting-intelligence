"""
Conditional audio processing — applied only when analysis detects issues.
- Bandwidth extension (sample rate <= 8kHz)
- Declipping (clipping detected)
- Denoising with Observation Adding (SNR < threshold)
- Treble boost (phone mode — restore stripped consonant frequencies)
- Dynamic range compression (phone mode — reduce volume gap between speakers)
"""
import asyncio
import logging
import threading
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio_preprocess.config import preprocess_config

logger = logging.getLogger("audio_preprocess")

# DeepFilterNet singleton
_df_model = None
_df_state = None
_df_lock = threading.Lock()


def _load_deepfilter():
    global _df_model, _df_state
    if _df_model is not None:
        return _df_model, _df_state
    with _df_lock:
        if _df_model is not None:
            return _df_model, _df_state
        logger.info("Loading DeepFilterNet3...")
        from df.enhance import init_df
        _df_model, _df_state, _ = init_df()
        logger.info("DeepFilterNet3 loaded")
        return _df_model, _df_state


def denoise_with_oa(input_path: Path, temp_dir: Path, alpha: float = 0.7) -> Path:
    """
    Apply DeepFilterNet3 denoising with Observation Adding.
    Blends enhanced + original at alpha ratio to preserve speech characteristics.
    output = alpha * enhanced + (1 - alpha) * original
    """
    from df.enhance import enhance, load_audio, save_audio

    logger.info(f"Denoising with DeepFilterNet3 + OA (alpha={alpha})...")

    model, df_state = _load_deepfilter()
    model_sr = df_state.sr()  # 48kHz

    # Load at model's sample rate
    audio_tensor, _ = load_audio(str(input_path), sr=model_sr)

    # Enhance
    enhanced_tensor = enhance(model, df_state, audio_tensor)

    # Save enhanced at 48kHz temporarily
    temp_enhanced = temp_dir / f"{uuid.uuid4().hex[:8]}_df_enhanced.wav"
    save_audio(str(temp_enhanced), enhanced_tensor, model_sr)

    # Read both original and enhanced at the same rate for blending
    original_audio, orig_sr = sf.read(str(input_path), dtype="float32")
    enhanced_audio, enh_sr = sf.read(str(temp_enhanced), dtype="float32")

    # Resample enhanced to match original if needed
    if enh_sr != orig_sr:
        import torchaudio
        import torch
        waveform = torch.from_numpy(enhanced_audio).unsqueeze(0)
        resampler = torchaudio.transforms.Resample(enh_sr, orig_sr)
        enhanced_audio = resampler(waveform).squeeze().numpy()

    # Ensure same length
    min_len = min(len(original_audio), len(enhanced_audio))
    original_audio = original_audio[:min_len]
    enhanced_audio = enhanced_audio[:min_len]

    # Observation Adding: blend enhanced + original
    blended = alpha * enhanced_audio + (1 - alpha) * original_audio

    # Clip to prevent overflow
    blended = np.clip(blended, -1.0, 1.0)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_denoised_oa.wav"
    sf.write(str(output_path), blended, orig_sr, subtype="PCM_16")

    # Cleanup temp
    temp_enhanced.unlink(missing_ok=True)

    logger.info(f"DeepFilterNet3 + OA complete: {output_path}")
    return output_path


def apply_bandwidth_extension(input_path: Path, temp_dir: Path) -> Path:
    """
    Extend bandwidth for low sample rate audio (<=8kHz).
    Uses high-quality resampling via subprocess as a baseline.
    ClearerVoice-Studio neural upsampling can replace this when GPU is available.
    """
    logger.info("Applying bandwidth extension (resample to 16kHz)...")

    info = sf.info(str(input_path))
    if info.samplerate > 8000:
        logger.info(f"Sample rate {info.samplerate}Hz > 8kHz, skipping bandwidth extension")
        return input_path

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_bw_ext.wav"

    import subprocess
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", "aresample=resampler=soxr:precision=33:dither_method=triangular",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    from app.audio_preprocess.config import preprocess_config
    result = subprocess.run(cmd, capture_output=True, timeout=preprocess_config.SUBPROCESS_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"Bandwidth extension failed: {result.stderr.decode()}")

    logger.info(f"Bandwidth extension complete: {output_path}")
    return output_path


def apply_declipping(input_path: Path, temp_dir: Path) -> Path:
    """
    Repair clipped audio.
    Uses soft-clipping mitigation via dynamic range compression as baseline.
    VoiceFixer can replace this when GPU is available.
    """
    logger.info("Applying declipping (soft compression)...")

    audio, sr = sf.read(str(input_path), dtype="float32")

    # Soft-clip mitigation: apply tanh compression to clipped regions
    peak = np.max(np.abs(audio))
    if peak > 0.95:
        # Apply soft saturation
        audio = np.tanh(audio * 0.8) / np.tanh(0.8)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_declipped.wav"
    sf.write(str(output_path), audio, sr, subtype="PCM_16")

    logger.info(f"Declipping complete: {output_path}")
    return output_path


async def apply_treble_boost(input_path: Path, temp_dir: Path,
                              gain: float = 6, frequency: int = 3000) -> Path:
    """
    Boost high frequencies to restore consonant clarity stripped by VoIP codecs.

    Phone/VoIP codecs (G.711, GSM-FR, Opus at low bitrate) aggressively cut
    frequencies above 3-4kHz. This strips consonants like /s/, /f/, /th/ that
    Whisper and VAD rely on to detect speech.

    A +6dB treble shelf at 3kHz partially restores these, improving both VAD
    sensitivity and ASR accuracy for the muffled speaker.

    Only applied when phone_mode is enabled.
    """
    logger.info(f"Applying treble boost: +{gain}dB at {frequency}Hz...")

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_treble.wav"
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", f"treble=gain={gain}:frequency={frequency}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Treble boost failed: {stderr.decode()}")

    logger.info(f"Treble boost complete: +{gain}dB at {frequency}Hz → {output_path}")
    return output_path


async def apply_dynamic_range_compression(input_path: Path, temp_dir: Path) -> Path:
    """
    Apply gentle dynamic range compression to reduce volume gap between speakers.

    In phone/VoIP recordings, the local speaker is often 15-25dB louder than
    the remote (muffled) speaker. This large gap causes:
    - VAD to miss the quiet speaker (below threshold)
    - Whisper to skip quiet segments (no_speech_prob too high)

    The compand filter applies ~3:1 compression with +8dB makeup gain:
    - Brings quiet speech (muffled speaker) up by ~8-10dB
    - Slightly tames loud speech to prevent clipping
    - Quick 20ms attack catches sudden loud peaks
    - 500ms decay prevents pumping artifacts

    Only applied when phone_mode is enabled.
    """
    logger.info("Applying dynamic range compression (phone mode)...")

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_drc.wav"
    # compand curve: -80→-80 (silence stays silent), -45→-35 (quiet boost),
    # -25→-20 (moderate boost), 0→-10 (loud tamed). +8dB makeup gain.
    cmd = [
        "ffmpeg", "-i", str(input_path),
        "-af", "compand=attacks=0.02:decays=0.5:points=-80/-80|-45/-35|-25/-20|0/-10:gain=8",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-y", "-loglevel", "error",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=preprocess_config.FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"Dynamic range compression failed: {stderr.decode()}")

    logger.info(f"Dynamic range compression complete: {output_path}")
    return output_path
