"""
API tests for the transcription service.
"""
import pytest
import asyncio
from fastapi.testclient import TestClient
import httpx
import io

from app.main import app


# Synchronous test client
client = TestClient(app)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestHomeEndpoint:
    """Tests for / endpoint (HTML page)."""

    def test_home_returns_html(self):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Transcription Service" in response.text


class TestTranscribeEndpoint:
    """Tests for /transcribe endpoint."""

    def test_no_input_returns_400(self):
        response = client.post(
            "/transcribe",
            json={}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "X-Request-ID" in response.headers

    def test_invalid_json_returns_400(self):
        response = client.post(
            "/transcribe",
            content="not json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400

    def test_request_id_header_present(self):
        response = client.post("/transcribe", json={})
        assert "X-Request-ID" in response.headers
        # Should be a valid UUID format
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 36  # UUID format


class TestConcurrency:
    """Concurrency tests."""

    @pytest.mark.asyncio
    async def test_parallel_requests_get_unique_ids(self):
        """Test that concurrent requests get unique request IDs."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # Send two requests in parallel
            results = await asyncio.gather(
                ac.post("/transcribe", json={}),
                ac.post("/transcribe", json={})
            )

            # Both should complete
            assert len(results) == 2

            # Extract request IDs
            ids = [r.headers.get("X-Request-ID") for r in results]

            # Both should have request IDs
            assert all(id is not None for id in ids)

            # Request IDs should be unique
            assert ids[0] != ids[1]

    @pytest.mark.asyncio
    async def test_many_parallel_requests(self):
        """Test handling multiple concurrent requests."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # Send 5 requests in parallel
            results = await asyncio.gather(
                *[ac.post("/transcribe", json={}) for _ in range(5)]
            )

            # All should complete
            assert len(results) == 5

            # All should have unique request IDs
            ids = [r.headers.get("X-Request-ID") for r in results]
            assert len(set(ids)) == 5


class TestSecurity:
    """Security tests."""

    def test_ssrf_localhost_blocked(self):
        """Test that localhost URLs are blocked."""
        response = client.post(
            "/transcribe",
            json={"media_url": "http://localhost/audio.mp3"}
        )
        assert response.status_code == 400
        assert "Security" in response.json().get("detail", "") or "private" in response.json().get("detail", "").lower()

    def test_ssrf_private_ip_blocked(self):
        """Test that private IP URLs are blocked."""
        response = client.post(
            "/transcribe",
            json={"media_url": "http://192.168.1.1/audio.mp3"}
        )
        assert response.status_code == 400

    def test_invalid_youtube_domain_blocked(self):
        """Test that non-YouTube domains are blocked for youtube_url."""
        response = client.post(
            "/transcribe",
            json={"youtube_url": "https://evil.com/watch?v=123"}
        )
        assert response.status_code == 400
        assert "YouTube" in response.json().get("detail", "") or "domain" in response.json().get("detail", "").lower()


class TestFileUpload:
    """File upload tests."""

    def test_invalid_extension_rejected(self):
        """Test that invalid file extensions are rejected."""
        file_content = b"not a real audio file"
        response = client.post(
            "/transcribe",
            files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")}
        )
        assert response.status_code == 400
        assert "Invalid file type" in response.json().get("detail", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
