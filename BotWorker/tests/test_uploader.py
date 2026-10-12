"""
Tests for storage pipeline: LocalStorage save, merge, cleanup.
"""
import math
import shutil
import struct
import wave
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from app.services.storage import LocalStorage, StorageError, create_storage

SAMPLE_RATE = 48000


def write_wav(path: Path, seconds: float, amplitude: float = 0.0) -> Path:
    """Write a mono 48 kHz s16le WAV: silence (amplitude=0) or a 440 Hz tone."""
    n = int(SAMPLE_RATE * seconds)
    frames = b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)
    return path


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


@pytest.fixture
def storage(tmp_path):
    s = LocalStorage()
    s.base_dir = tmp_path / "recordings"
    s.base_dir.mkdir()
    return s


@pytest.fixture
def sample_chunks(tmp_path):
    """Three short WAV chunks, as produced by the recorder."""
    chunks_dir = tmp_path / "raw"
    chunks_dir.mkdir()
    return [write_wav(chunks_dir / f"chunk_{i:04d}.wav", 0.1, amplitude=0.5) for i in range(3)]


class TestLocalStorage:
    @pytest.mark.asyncio
    async def test_save_chunk(self, storage, sample_chunks):
        key = await storage.save_chunk(sample_chunks[0], "job-1", 0)
        assert "job-1" in key
        assert "chunk_0000.wav" in key

        # Verify file was copied
        saved = storage._job_dir("job-1") / "chunk_0000.wav"
        assert saved.exists()

    @pytest.mark.asyncio
    async def test_get_all_chunks(self, storage, sample_chunks):
        for i, chunk in enumerate(sample_chunks):
            await storage.save_chunk(chunk, "job-1", i)

        chunks = await storage.get_all_chunks("job-1")
        assert len(chunks) == 3
        # Should be sorted
        assert "chunk_0000" in str(chunks[0])
        assert "chunk_0002" in str(chunks[2])

    @pytest.mark.asyncio
    async def test_get_all_chunks_empty(self, storage):
        chunks = await storage.get_all_chunks("nonexistent")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_get_merged_audio_no_chunks(self, storage):
        with pytest.raises(StorageError, match="No chunks found"):
            await storage.get_merged_audio("nonexistent")

    @pytest.mark.asyncio
    @patch("app.services.storage.asyncio.create_subprocess_exec")
    async def test_get_merged_audio_ffmpeg_command(self, mock_exec, storage, sample_chunks, monkeypatch):
        """Even a single chunk goes through FFmpeg so leading silence is trimmed."""
        monkeypatch.delenv("BOTWORKER_AUDIO_FORMAT", raising=False)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = mock_proc

        await storage.save_chunk(sample_chunks[0], "job-1", 0)
        merged_target = storage._job_dir("job-1") / "merged.wav"
        merged_target.write_bytes(b"")  # stands in for FFmpeg's output

        merged = await storage.get_merged_audio("job-1")

        assert merged == merged_target
        args = list(mock_exec.call_args[0])
        assert args[args.index("-f") + 1] == "concat"
        assert any(a.startswith("silenceremove=start_periods=1") for a in args)
        assert "stop_periods" not in " ".join(args)  # never trim the tail
        assert args[args.index("-c:a") + 1] == "pcm_s16le"
        assert args[args.index("-ar") + 1] == "48000"
        assert args[args.index("-ac") + 1] == "1"

        filelist = (storage._job_dir("job-1") / "filelist.txt").read_text()
        assert "chunk_0000.wav" in filelist

    @pytest.mark.asyncio
    @patch("app.services.storage.asyncio.create_subprocess_exec")
    async def test_get_merged_audio_ffmpeg_failure(self, mock_exec, storage, sample_chunks):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"boom"))
        mock_exec.return_value = mock_proc

        await storage.save_chunk(sample_chunks[0], "job-1", 0)
        with pytest.raises(StorageError, match="FFmpeg merge failed: boom"):
            await storage.get_merged_audio("job-1")

    @pytest.mark.asyncio
    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    async def test_merge_trims_leading_silence_only(self, storage, tmp_path):
        """Real FFmpeg run: pre-admission silence is dropped, the rest is concatenated intact."""
        raw = tmp_path / "raw"
        raw.mkdir()
        silent = write_wav(raw / "chunk_0000.wav", 1.0, amplitude=0.0)
        tone_a = write_wav(raw / "chunk_0001.wav", 0.5, amplitude=0.5)
        tone_b = write_wav(raw / "chunk_0002.wav", 0.5, amplitude=0.5)
        for i, chunk in enumerate([silent, tone_a, tone_b]):
            await storage.save_chunk(chunk, "job-1", i)

        merged = await storage.get_merged_audio("job-1")

        assert merged.name == "merged.wav"
        with wave.open(str(merged), "rb") as w:
            assert w.getframerate() == SAMPLE_RATE
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
        # The 1 s of leading silence is removed and the 1 s of tone is kept,
        # minus up to start_duration (0.1 s): silenceremove also consumes the
        # detection window at the onset of the first non-silent audio.
        assert 0.85 <= wav_duration(merged) <= 1.05

    @pytest.mark.asyncio
    async def test_cleanup(self, storage, sample_chunks):
        await storage.save_chunk(sample_chunks[0], "job-1", 0)
        assert storage._job_dir("job-1").exists()

        await storage.cleanup("job-1")
        assert not storage._job_dir("job-1").exists()


class TestCreateStorage:
    @patch("app.services.storage.settings")
    def test_create_local(self, mock_settings):
        mock_settings.STORAGE_BACKEND = "local"
        mock_settings.STORAGE_DIR = "/tmp/test"
        s = create_storage()
        assert isinstance(s, LocalStorage)

    @patch("app.services.storage.settings")
    def test_always_local_even_if_s3_configured(self, mock_settings):
        """Chunks always stay inside the container; the orchestrator uploads the merged file."""
        mock_settings.STORAGE_BACKEND = "s3"
        mock_settings.STORAGE_DIR = "/tmp/test"
        s = create_storage()
        assert isinstance(s, LocalStorage)
