"""
Audio helpers shared by the system and browser capture recorders.

- chunk_rms_dbfs: RMS measurement of a PCM WAV (s16le or f32le).
- write_wav_f32le: write a WAVE_FORMAT_IEEE_FLOAT file (Python's wave
  module does not support float WAVs, so we emit the header manually).
"""
import math
import struct
from array import array
from pathlib import Path

# RMS threshold below which a chunk is considered effectively silent.
# bot_sink.monitor with no playback hovers near -90 dBFS; meeting audio
# is typically -30 to -15 dBFS. -60 cleanly separates the two.
SILENCE_RMS_DBFS = -60.0

# WAVE format codes
_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_IEEE_FLOAT = 3


def chunk_rms_dbfs(path: Path) -> float:
    """Return RMS in dBFS for the first ~1s of a PCM WAV (s16le or f32le).

    Reads the WAV header manually because Python's stdlib `wave` module does
    not support IEEE-float WAVs. Returns -inf for unreadable / empty /
    unsupported files. Cheap by design: sampled, not full-file.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return float("-inf")
            f.read(4)  # file size
            if f.read(4) != b"WAVE":
                return float("-inf")

            fmt_code = channels = sample_rate = bps = 0
            data_offset = data_size = 0
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid = hdr[:4]
                size = int.from_bytes(hdr[4:8], "little")
                if cid == b"fmt ":
                    body = f.read(size)
                    fmt_code = int.from_bytes(body[0:2], "little")
                    channels = int.from_bytes(body[2:4], "little")
                    sample_rate = int.from_bytes(body[4:8], "little")
                    bps = int.from_bytes(body[14:16], "little")
                elif cid == b"data":
                    data_offset = f.tell()
                    data_size = size
                    break
                else:
                    f.seek(size, 1)

            if not data_offset or not channels or not sample_rate:
                return float("-inf")

            sample_bytes = bps // 8
            frame_bytes = channels * sample_bytes
            n_frames_to_read = min(data_size // max(frame_bytes, 1), sample_rate)
            if n_frames_to_read <= 0:
                return float("-inf")
            f.seek(data_offset)
            raw = f.read(n_frames_to_read * frame_bytes)

        if fmt_code == _WAVE_FORMAT_PCM and bps == 16:
            samples = array("h")
            samples.frombytes(raw)
            if not samples:
                return float("-inf")
            ms = sum(s * s for s in samples) / len(samples)
            return 20 * math.log10(math.sqrt(ms) / 32768.0) if ms > 0 else float("-inf")
        if fmt_code == _WAVE_FORMAT_IEEE_FLOAT and bps == 32:
            samples = array("f")
            samples.frombytes(raw)
            if not samples:
                return float("-inf")
            ms = sum(s * s for s in samples) / len(samples)
            return 20 * math.log10(math.sqrt(ms)) if ms > 0 else float("-inf")
        return float("-inf")
    except Exception:
        return float("-inf")


def write_wav_f32le(path: Path, interleaved: bytes, sample_rate: int, channels: int):
    """Write a WAVE_FORMAT_IEEE_FLOAT (.wav) file from raw interleaved float32 bytes.

    Python's `wave` only handles PCM, so we emit the standard 44-byte header
    manually. `interleaved` must be a bytes-like object containing
    little-endian Float32 samples in interleaved channel order.
    """
    data_size = len(interleaved)
    byte_rate = sample_rate * channels * 4
    block_align = channels * 4
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<I", 16))                  # fmt chunk size
        f.write(struct.pack("<H", _WAVE_FORMAT_IEEE_FLOAT))
        f.write(struct.pack("<H", channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", 32))                  # bits per sample
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(interleaved)
