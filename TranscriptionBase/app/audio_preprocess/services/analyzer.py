"""
Audio analysis — inspects raw audio and reports metrics.
Detects SNR, clipping, sample rate, duration to guide conditional processing.
"""
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger("audio_preprocess")


@dataclass
class AudioAnalysis:
    """Results of audio analysis."""
    file_name: str
    sample_rate: int
    duration_seconds: float
    num_samples: int
    channels: int
    bit_depth: str
    peak_amplitude: float
    rms_amplitude: float
    snr_db: float
    is_clipped: bool
    clipping_percentage: float
    needs_bandwidth_extension: bool
    needs_declipping: bool
    needs_denoising: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ensure numpy types are converted to native Python types for JSON
        for k, v in d.items():
            if isinstance(v, (np.bool_,)):
                d[k] = bool(v)
            elif isinstance(v, (np.integer,)):
                d[k] = int(v)
            elif isinstance(v, (np.floating,)):
                d[k] = float(v)
        return d


def estimate_snr(audio: np.ndarray, sr: int, frame_ms: int = 30) -> float:
    """
    Estimate Signal-to-Noise Ratio using energy-based method.
    Splits audio into frames. Uses the energy variance to distinguish
    speech-like signals (high variance) from steady signals.
    For speech: bottom 10% frames = noise, top 50% = signal.
    For steady signals: uses overall RMS as a quality proxy.
    Returns SNR in dB.
    """
    frame_size = int(sr * frame_ms / 1000)
    if len(audio) < frame_size * 4:
        rms = np.sqrt(np.mean(audio ** 2))
        return 30.0 if rms > 0.01 else 0.0

    num_frames = len(audio) // frame_size
    frames = audio[:num_frames * frame_size].reshape(num_frames, frame_size)
    frame_energies = np.mean(frames ** 2, axis=1)

    sorted_energies = np.sort(frame_energies)

    # Check if signal has speech-like energy variation
    energy_std = np.std(frame_energies)
    energy_mean = np.mean(frame_energies)
    coefficient_of_variation = energy_std / energy_mean if energy_mean > 0 else 0

    if coefficient_of_variation < 0.3:
        # Steady signal (music, tone, constant noise) — use RMS-based estimate
        rms = np.sqrt(np.mean(audio ** 2))
        if rms > 0.1:
            return 40.0  # Strong steady signal
        elif rms > 0.01:
            return 25.0  # Moderate steady signal
        else:
            return 5.0   # Very quiet

    # Speech-like signal: use frame energy distribution
    noise_count = max(1, int(num_frames * 0.10))
    signal_count = max(1, int(num_frames * 0.50))

    noise_energy = np.mean(sorted_energies[:noise_count])
    signal_energy = np.mean(sorted_energies[-signal_count:])

    if noise_energy <= 0:
        return 60.0

    snr = 10 * np.log10(signal_energy / noise_energy)
    return round(float(np.clip(snr, 0, 60)), 1)


def detect_clipping(audio: np.ndarray, threshold: float = 0.99) -> tuple:
    """
    Detect clipping in audio signal.
    Returns (is_clipped, clipping_percentage).
    """
    clipped_samples = np.sum(np.abs(audio) >= threshold)
    total_samples = len(audio)
    clipping_pct = round((clipped_samples / total_samples) * 100, 3) if total_samples > 0 else 0.0
    is_clipped = clipping_pct > 0.1
    return is_clipped, clipping_pct


def analyze_audio(file_path: Path, snr_threshold: float = 15.0,
                  clipping_threshold: float = 0.99,
                  low_sr_hz: int = 8000) -> AudioAnalysis:
    """
    Analyze an audio file and return comprehensive metrics.
    """
    logger.info(f"Analyzing audio: {file_path}")

    info = sf.info(str(file_path))
    audio, sr = sf.read(str(file_path), dtype="float32", always_2d=False)

    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    duration = len(audio) / sr
    peak = float(np.max(np.abs(audio))) if len(audio) > 0 else 0.0
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) > 0 else 0.0

    snr = estimate_snr(audio, sr)
    is_clipped, clip_pct = detect_clipping(audio, clipping_threshold)

    analysis = AudioAnalysis(
        file_name=file_path.name,
        sample_rate=sr,
        duration_seconds=round(duration, 2),
        num_samples=len(audio),
        channels=info.channels,
        bit_depth=info.subtype,
        peak_amplitude=round(peak, 4),
        rms_amplitude=round(rms, 4),
        snr_db=snr,
        is_clipped=is_clipped,
        clipping_percentage=clip_pct,
        needs_bandwidth_extension=(sr <= low_sr_hz),
        needs_declipping=is_clipped,
        needs_denoising=(snr < snr_threshold),
    )

    logger.info(
        f"Analysis: SR={sr}Hz, dur={duration:.1f}s, SNR={snr}dB, "
        f"clipped={is_clipped}, bw_ext={analysis.needs_bandwidth_extension}, "
        f"denoise={analysis.needs_denoising}"
    )
    return analysis
