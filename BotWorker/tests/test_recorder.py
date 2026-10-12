"""
Tests for AudioRecorder: FFmpeg command args, stop behavior, chunk detection.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.core.config import settings
from app.services.recorder import AudioRecorder


@pytest.fixture
def chunk_dir(tmp_path):
    d = tmp_path / "chunks"
    d.mkdir()
    return d


@pytest.fixture
def recorder(chunk_dir):
    return AudioRecorder(
        job_id="test-job",
        chunk_dir=chunk_dir,
    )


class TestAudioRecorder:
    @pytest.mark.asyncio
    @patch("app.services.recorder.asyncio.create_subprocess_exec")
    async def test_start_launches_ffmpeg(self, mock_exec, recorder):
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_exec.return_value = mock_proc

        await recorder.start()

        mock_exec.assert_called_once()
        args = list(mock_exec.call_args[0])
        assert args[0] == "ffmpeg"
        # Input: the PulseAudio monitor of the bot's virtual sink
        assert args[args.index("-i") - 1] == "pulse"
        assert args[args.index("-i") + 1] == f"{settings.PULSE_SINK_NAME}.monitor"
        # Lossless 48 kHz mono 16-bit PCM
        assert args[args.index("-c:a") + 1] == "pcm_s16le"
        assert args[args.index("-ar") + 1] == "48000"
        assert args[args.index("-ac") + 1] == "1"
        assert "libopus" not in args
        # Segmented into fixed-length WAV chunks
        assert "segment" in args
        assert args[args.index("-segment_format") + 1] == "wav"
        assert args[args.index("-segment_time") + 1] == str(settings.CHUNK_DURATION_SECONDS)
        assert args[-1].endswith("chunk_%04d.wav")

        # Cleanup
        recorder._running = False
        if recorder._watcher_task:
            recorder._watcher_task.cancel()
        if recorder._stderr_task:
            recorder._stderr_task.cancel()

    @pytest.mark.asyncio
    @patch("app.services.recorder.asyncio.create_subprocess_exec")
    async def test_stop_sends_sigint(self, mock_exec, recorder):
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.send_signal = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.kill = MagicMock()
        mock_exec.return_value = mock_proc

        await recorder.start()
        total = await recorder.stop()

        mock_proc.send_signal.assert_called()
        assert total == 0  # No chunks created

    @pytest.mark.asyncio
    async def test_chunk_callback_called(self, chunk_dir):
        callback = AsyncMock()
        recorder = AudioRecorder(
            job_id="test-job",
            chunk_dir=chunk_dir,
            on_chunk_ready=callback,
        )

        # Simulate chunks already existing
        (chunk_dir / "chunk_0000.wav").write_bytes(b"fake wav data")
        (chunk_dir / "chunk_0001.wav").write_bytes(b"fake wav data")

        # Scan final chunks (simulates what happens after FFmpeg stops)
        await recorder._scan_final_chunks()

        assert callback.call_count == 2
        assert callback.call_args_list[0][0][1] == 0  # chunk_index
        assert callback.call_args_list[1][0][1] == 1

    @pytest.mark.asyncio
    async def test_known_chunks_avoid_duplicates(self, chunk_dir):
        callback = AsyncMock()
        recorder = AudioRecorder(
            job_id="test-job",
            chunk_dir=chunk_dir,
            on_chunk_ready=callback,
        )

        (chunk_dir / "chunk_0000.wav").write_bytes(b"fake")
        await recorder._scan_final_chunks()
        await recorder._scan_final_chunks()  # Second scan

        # Should only be called once despite two scans
        assert callback.call_count == 1

    @pytest.mark.asyncio
    async def test_live_scan_skips_chunk_still_being_written(self, chunk_dir):
        """While FFmpeg is running, the newest chunk is still open and must not be handed off."""
        callback = AsyncMock()
        recorder = AudioRecorder(
            job_id="test-job",
            chunk_dir=chunk_dir,
            on_chunk_ready=callback,
        )

        (chunk_dir / "chunk_0000.wav").write_bytes(b"done")
        (chunk_dir / "chunk_0001.wav").write_bytes(b"in progress")

        await recorder._process_new_chunks(exclude_latest=True)

        assert callback.call_count == 1
        assert callback.call_args[0][0].name == "chunk_0000.wav"

    @pytest.mark.asyncio
    async def test_non_wav_files_ignored(self, chunk_dir):
        callback = AsyncMock()
        recorder = AudioRecorder(
            job_id="test-job",
            chunk_dir=chunk_dir,
            on_chunk_ready=callback,
        )

        (chunk_dir / "chunk_0000.ogg").write_bytes(b"legacy")
        await recorder._scan_final_chunks()

        callback.assert_not_called()
