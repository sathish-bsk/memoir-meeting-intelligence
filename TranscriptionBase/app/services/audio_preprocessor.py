"""
Audio preprocessing pipeline for improving Whisper transcription quality.

Steps (when enabled):
1. Noise reduction (spectral gating via noisereduce)
2. VAD-based silence trimming (Silero VAD)
3. Loudness normalization + dynamic range compression (FFmpeg)
"""
import asyncio
import threading
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch

from app.core.logging import get_request_logger


class PreprocessError(Exception):
    """Raised when audio preprocessing fails."""
    pass


# Silero VAD singleton
_vad_model = None
_vad_utils = None
_vad_lock = threading.Lock()


def _load_vad_model():
    """Load Silero VAD model (cached singleton)."""
    global _vad_model, _vad_utils
    if _vad_model is not None:
        return _vad_model, _vad_utils

    with _vad_lock:
        if _vad_model is not None:
            return _vad_model, _vad_utils

        import logging
        logger = logging.getLogger("transcription")
        logger.info("Loading Silero VAD model...")

        _vad_model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            trust_repo=True,
        )
        _vad_utils = utils
        logger.info("Silero VAD model loaded")
        return _vad_model, _vad_utils


def reduce_noise(wav_path: Path, request_id: str, temp_dir: Path, method: str = "noisereduce") -> Path:
    """
    Apply noise reduction using the selected method.

    Methods:
        - "noisereduce": Spectral gating (lightweight, good for stationary noise)
        - "deepfilternet": Neural speech enhancement (handles all noise types, reverb)
    """
    logger = get_request_logger(request_id)

    if method == "deepfilternet":
        return _reduce_noise_deepfilter(wav_path, request_id, temp_dir)
    else:
        return _reduce_noise_spectral(wav_path, request_id, temp_dir)


def _reduce_noise_spectral(wav_path: Path, request_id: str, temp_dir: Path) -> Path:
    """Apply spectral gating noise reduction via noisereduce."""
    import noisereduce as nr

    logger = get_request_logger(request_id)
    logger.info("Preprocessing step 1: Noise reduction (spectral gating)...")

    audio, sr = sf.read(wav_path, dtype='float32')
    original_size = len(audio)

    reduced = nr.reduce_noise(
        y=audio,
        sr=sr,
        prop_decrease=0.8,
        n_fft=2048,
        hop_length=512,
    )

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_denoised.wav"
    sf.write(str(output_path), reduced, sr, subtype='PCM_16')

    logger.info(f"Spectral noise reduction complete: {original_size} samples")
    return output_path


# DeepFilterNet singleton
_df_model = None
_df_state = None
_df_lock = threading.Lock()


def _load_deepfilter_model():
    """Load DeepFilterNet3 model (cached singleton)."""
    global _df_model, _df_state
    if _df_model is not None:
        return _df_model, _df_state

    with _df_lock:
        if _df_model is not None:
            return _df_model, _df_state

        import logging
        logger = logging.getLogger("transcription")
        logger.info("Loading DeepFilterNet3 model...")

        from df.enhance import init_df
        _df_model, _df_state, _ = init_df()

        logger.info("DeepFilterNet3 model loaded")
        return _df_model, _df_state


def _reduce_noise_deepfilter(wav_path: Path, request_id: str, temp_dir: Path) -> Path:
    """Apply neural speech enhancement via DeepFilterNet3."""
    from df.enhance import enhance, load_audio, save_audio

    logger = get_request_logger(request_id)
    logger.info("Preprocessing step 1: Noise reduction (DeepFilterNet3)...")

    model, df_state = _load_deepfilter_model()
    model_sr = df_state.sr()  # 48000

    # Load and resample to model's sample rate
    audio_tensor, _ = load_audio(str(wav_path), sr=model_sr)
    logger.info(f"Loaded audio for DeepFilterNet: {audio_tensor.shape}")

    # Enhance
    enhanced = enhance(model, df_state, audio_tensor)

    # Save at 48kHz then resample back to 16kHz via soundfile
    temp_48k = temp_dir / f"{uuid.uuid4().hex[:8]}_df_48k.wav"
    save_audio(str(temp_48k), enhanced, model_sr)

    # Read back and resample to 16kHz
    import torchaudio
    waveform, orig_sr = torchaudio.load(str(temp_48k))
    if orig_sr != 16000:
        resampler = torchaudio.transforms.Resample(orig_freq=orig_sr, new_freq=16000)
        waveform = resampler(waveform)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_denoised.wav"
    sf.write(str(output_path), waveform.squeeze().numpy(), 16000, subtype='PCM_16')

    # Clean up temp 48k file
    temp_48k.unlink(missing_ok=True)

    logger.info(f"DeepFilterNet3 noise reduction complete")
    return output_path


def trim_silence_vad(wav_path: Path, request_id: str, temp_dir: Path) -> Path:
    """
    Detect speech segments using Silero VAD and trim silence.
    Concatenates speech chunks with small padding to prevent hallucinations.
    """
    logger = get_request_logger(request_id)
    logger.info("Preprocessing step 2: VAD silence trimming...")

    model, utils = _load_vad_model()
    (get_speech_timestamps, _, read_audio, _, collect_chunks) = utils

    # Load audio as torch tensor (Silero expects 16kHz mono)
    audio, sr = sf.read(wav_path, dtype='float32')
    wav_tensor = torch.from_numpy(audio)

    # Get speech timestamps
    speech_timestamps = get_speech_timestamps(
        wav_tensor,
        model,
        sampling_rate=sr,
        threshold=0.5,
        min_speech_duration_ms=250,
        min_silence_duration_ms=300,
        speech_pad_ms=200,      # Padding around speech segments
    )

    if not speech_timestamps:
        logger.warning("VAD detected no speech segments, returning original audio")
        return wav_path

    logger.info(f"VAD detected {len(speech_timestamps)} speech segments")

    # Collect speech chunks
    speech_audio = collect_chunks(speech_timestamps, wav_tensor)

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_vad_trimmed.wav"
    sf.write(str(output_path), speech_audio.numpy(), sr, subtype='PCM_16')

    original_duration = len(audio) / sr
    trimmed_duration = len(speech_audio) / sr
    logger.info(
        f"VAD trimming complete: {original_duration:.1f}s -> {trimmed_duration:.1f}s "
        f"(removed {original_duration - trimmed_duration:.1f}s silence)"
    )
    return output_path


async def loudnorm_compress(wav_path: Path, request_id: str, temp_dir: Path) -> Path:
    """
    Apply EBU R128 loudness normalization and dynamic range compression via FFmpeg.
    """
    logger = get_request_logger(request_id)
    logger.info("Preprocessing step 3: Loudness normalization + compression...")

    output_path = temp_dir / f"{uuid.uuid4().hex[:8]}_loudnorm.wav"

    cmd = [
        "ffmpeg",
        "-i", str(wav_path),
        "-af", (
            "loudnorm=I=-23:TP=-1.5:LRA=11,"
            "acompressor=threshold=-20dB:ratio=4:attack=5:release=50"
        ),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-y",
        "-loglevel", "error",
        str(output_path)
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300
            )
        except asyncio.TimeoutError:
            process.kill()
            raise PreprocessError("Loudness normalization timeout")

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise PreprocessError(f"Loudness normalization failed: {error_msg}")

    except FileNotFoundError:
        raise PreprocessError("FFmpeg not installed")

    if not output_path.exists():
        raise PreprocessError("Loudness normalization completed but output file not found")

    logger.info(f"Loudness normalization complete: {output_path}")
    return output_path


def get_speech_chunks(wav_path: Path, request_id: str, max_chunk_s: float = 28.0):
    """
    Use Silero VAD to detect speech segments and group them into optimal chunks
    for Whisper transcription. Splits at natural pauses, never mid-speech.

    Returns list of (start_sample, end_sample) tuples.
    Each chunk is <= max_chunk_s seconds and aligned to speech boundaries.
    """
    logger = get_request_logger(request_id)

    model, utils = _load_vad_model()
    (get_speech_timestamps, _, _, _, _) = utils

    audio, sr = sf.read(wav_path, dtype='float32')
    wav_tensor = torch.from_numpy(audio)
    total_duration = len(audio) / sr

    speech_timestamps = get_speech_timestamps(
        wav_tensor,
        model,
        sampling_rate=sr,
        threshold=0.5,
        min_speech_duration_ms=250,
        min_silence_duration_ms=500,
        speech_pad_ms=200,
    )

    if not speech_timestamps:
        logger.warning("VAD detected no speech for chunking, returning whole audio")
        return [(0, len(audio))]

    # Group speech segments into chunks of <= max_chunk_s
    chunks = []
    current_start = speech_timestamps[0]['start']
    current_end = speech_timestamps[0]['end']

    for ts in speech_timestamps[1:]:
        # Would adding this segment exceed max chunk duration?
        potential_duration = (ts['end'] - current_start) / sr

        if potential_duration <= max_chunk_s:
            # Extend current chunk to include this segment
            current_end = ts['end']
        else:
            # Save current chunk, start a new one
            chunks.append((current_start, current_end))
            current_start = ts['start']
            current_end = ts['end']

    # Don't forget the last chunk
    chunks.append((current_start, current_end))

    logger.info(
        f"Smart chunking: {total_duration:.1f}s audio -> {len(chunks)} chunks "
        f"(from {len(speech_timestamps)} speech segments)"
    )
    for i, (s, e) in enumerate(chunks):
        logger.info(f"  Chunk {i+1}: {s/sr:.1f}s - {e/sr:.1f}s ({(e-s)/sr:.1f}s)")

    return chunks


async def preprocess_audio(
    wav_path: Path, request_id: str, temp_dir: Path,
    noise_method: str = "noisereduce"
) -> Path:
    """
    Run the full audio preprocessing pipeline:
    1. Noise reduction (spectral gating or DeepFilterNet3)
    2. VAD silence trimming
    3. Loudness normalization + dynamic range compression

    Args:
        noise_method: "noisereduce" (spectral gating) or "deepfilternet" (neural)

    Input: 16kHz mono WAV (from normalize_audio)
    Output: Preprocessed 16kHz mono WAV ready for Whisper
    """
    logger = get_request_logger(request_id)
    logger.info(f"Starting audio preprocessing pipeline (noise: {noise_method})...")

    original_size = wav_path.stat().st_size

    try:
        # Step 1: Noise reduction (CPU-bound, run in thread)
        loop = asyncio.get_event_loop()
        denoised_path = await loop.run_in_executor(
            None, reduce_noise, wav_path, request_id, temp_dir, noise_method
        )

        # Step 2: VAD silence trimming (CPU-bound, run in thread)
        trimmed_path = await loop.run_in_executor(
            None, trim_silence_vad, denoised_path, request_id, temp_dir
        )

        # Step 3: Loudness normalization + compression (async FFmpeg)
        final_path = await loudnorm_compress(trimmed_path, request_id, temp_dir)

        final_size = final_path.stat().st_size
        logger.info(
            f"Preprocessing pipeline complete: {original_size} -> {final_size} bytes"
        )
        return final_path

    except PreprocessError:
        raise
    except Exception as e:
        raise PreprocessError(f"Audio preprocessing failed: {e}")
