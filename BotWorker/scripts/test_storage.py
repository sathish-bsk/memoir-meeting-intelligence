"""
Local test for the storage pipeline.
Creates real OGG/Opus audio chunks via FFmpeg, saves them through LocalStorage,
merges them, and verifies the output.

Run: source venv/bin/activate && python scripts/test_storage.py
"""
import asyncio
import subprocess
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.storage import LocalStorage


TEST_DIR = Path("/tmp/botworker_storage_test")
CHUNK_DIR = TEST_DIR / "raw_chunks"
JOB_ID = "storage-test-001"


def create_test_chunks(count: int = 3, duration: float = 5.0):
    """Generate real OGG/Opus audio chunks using FFmpeg (sine wave tone)."""
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    paths = []

    for i in range(count):
        freq = 440 + (i * 100)  # Different tone per chunk so we can hear the merge
        path = CHUNK_DIR / f"chunk_{i:04d}.ogg"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={duration}",
            "-c:a", "libopus",
            "-b:a", "48k",
            "-ar", "48000",
            "-ac", "1",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"FAIL: FFmpeg error creating chunk {i}: {result.stderr}")
            sys.exit(1)

        size = path.stat().st_size
        print(f"  Created {path.name}: {size} bytes, {freq}Hz tone, {duration}s")
        paths.append(path)

    return paths


def probe_audio(path: Path) -> dict:
    """Get audio file info via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_name,sample_rate,channels",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    import json
    return json.loads(result.stdout)


async def main():
    print("=" * 60)
    print("Storage Pipeline Test")
    print("=" * 60)

    # Cleanup from previous runs
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)

    # Step 1: Create test chunks
    print("\n[1/5] Creating test audio chunks...")
    chunks = create_test_chunks(count=3, duration=5.0)
    print(f"  Created {len(chunks)} chunks")

    # Step 2: Initialize storage
    print("\n[2/5] Initializing LocalStorage...")
    storage = LocalStorage()
    storage.base_dir = TEST_DIR / "recordings"
    storage.base_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Storage dir: {storage.base_dir}")

    # Step 3: Save chunks through storage
    print("\n[3/5] Saving chunks via storage.save_chunk()...")
    for i, chunk_path in enumerate(chunks):
        key = await storage.save_chunk(chunk_path, JOB_ID, i)
        print(f"  Saved chunk {i} -> key: {key}")

    # Verify saved
    saved_chunks = await storage.get_all_chunks(JOB_ID)
    assert len(saved_chunks) == 3, f"Expected 3 saved chunks, got {len(saved_chunks)}"
    print(f"  Verified: {len(saved_chunks)} chunks in storage")

    for chunk in saved_chunks:
        print(f"    {chunk.name}: {chunk.stat().st_size} bytes")

    # Step 4: Merge chunks
    print("\n[4/5] Merging chunks via storage.get_merged_audio()...")
    merged_path = await storage.get_merged_audio(JOB_ID)
    merged_size = merged_path.stat().st_size
    print(f"  Merged file: {merged_path}")
    print(f"  Merged size: {merged_size} bytes")

    # Verify merged audio
    info = probe_audio(merged_path)
    if info:
        duration = float(info.get("format", {}).get("duration", 0))
        streams = info.get("streams", [{}])
        codec = streams[0].get("codec_name", "unknown") if streams else "unknown"
        print(f"  Codec: {codec}")
        print(f"  Duration: {duration:.1f}s (expected ~15.0s)")

        assert codec == "opus", f"Expected opus codec, got {codec}"
        assert duration > 14.0, f"Expected ~15s duration, got {duration:.1f}s"
        print("  PASS: Merged audio is valid")
    else:
        print("  WARN: Could not probe merged file")

    # Step 5: Cleanup test
    print("\n[5/5] Testing cleanup...")
    job_dir = storage._job_dir(JOB_ID)
    assert job_dir.exists(), "Job dir should exist before cleanup"
    await storage.cleanup(JOB_ID)
    assert not job_dir.exists(), "Job dir should be removed after cleanup"
    print("  PASS: Cleanup removed all files")

    # Final cleanup
    shutil.rmtree(TEST_DIR)

    print("\n" + "=" * 60)
    print("ALL STORAGE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
