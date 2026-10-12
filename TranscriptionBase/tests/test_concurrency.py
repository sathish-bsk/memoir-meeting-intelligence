"""
Dedicated concurrency tests with more scenarios.
"""
import pytest
import asyncio
import time
import httpx

from app.main import app


@pytest.mark.asyncio
async def test_concurrent_health_checks():
    """Test concurrent health check requests."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        start = time.time()
        results = await asyncio.gather(
            *[ac.get("/health") for _ in range(10)]
        )
        elapsed = time.time() - start

        # All should succeed
        assert all(r.status_code == 200 for r in results)
        assert all(r.json() == {"status": "ok"} for r in results)

        # Should complete quickly (parallel, not serial)
        assert elapsed < 2.0  # Should be much faster than 10 serial requests


@pytest.mark.asyncio
async def test_concurrent_validation_errors():
    """Test that concurrent requests with validation errors don't interfere."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        results = await asyncio.gather(
            ac.post("/transcribe", json={"media_url": "http://localhost/a.mp3"}),
            ac.post("/transcribe", json={"media_url": "http://127.0.0.1/b.mp3"}),
            ac.post("/transcribe", json={"media_url": "http://192.168.1.1/c.mp3"}),
        )

        # All should fail with 400 (security check)
        assert all(r.status_code == 400 for r in results)

        # Each should have a unique request ID
        ids = [r.headers.get("X-Request-ID") for r in results]
        assert len(set(ids)) == 3


@pytest.mark.asyncio
async def test_mixed_concurrent_requests():
    """Test mixed request types concurrently."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        results = await asyncio.gather(
            ac.get("/health"),
            ac.get("/"),
            ac.post("/transcribe", json={}),
            ac.get("/health"),
        )

        # Health checks should succeed
        assert results[0].status_code == 200
        assert results[3].status_code == 200

        # Home page should return HTML
        assert results[1].status_code == 200
        assert "text/html" in results[1].headers["content-type"]

        # Transcribe with no input should fail
        assert results[2].status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
