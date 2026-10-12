"""
Unified storage abstraction — local filesystem (dev) or S3 (prod).
Switches based on STORAGE_BACKEND env var.
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import aiofiles
import boto3
from botocore.config import Config as BotoConfig


class StorageBackend(ABC):
    """Abstract storage interface."""

    @abstractmethod
    async def save_json(self, path: str, data: Any) -> None: ...

    @abstractmethod
    async def read_json(self, path: str) -> Any: ...

    @abstractmethod
    async def save_text(self, path: str, text: str) -> None: ...

    @abstractmethod
    async def read_text(self, path: str) -> str: ...

    @abstractmethod
    async def save_file(self, path: str, local_path: str) -> None:
        """Copy a local file into storage at the given logical path."""
        ...

    @abstractmethod
    async def download_file(self, path: str, local_path: str) -> None:
        """Download a file from storage to a local path."""
        ...

    @abstractmethod
    async def get_presigned_url(
        self,
        path: str,
        expires_in: int = 900,
        download_filename: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def exists(self, path: str) -> bool: ...

    @abstractmethod
    async def copy(self, src_path: str, dst_path: str) -> None:
        """Copy a file within storage from src_path to dst_path."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete a file from storage. No error if it doesn't exist."""
        ...


class LocalStorage(StorageBackend):
    """Filesystem-based storage for development."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        full = self.base_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    async def save_json(self, path: str, data: Any) -> None:
        async with aiofiles.open(self._resolve(path), "w") as f:
            await f.write(json.dumps(data, indent=2, default=str))

    async def read_json(self, path: str) -> Any:
        async with aiofiles.open(self._resolve(path), "r") as f:
            return json.loads(await f.read())

    async def save_text(self, path: str, text: str) -> None:
        async with aiofiles.open(self._resolve(path), "w") as f:
            await f.write(text)

    async def read_text(self, path: str) -> str:
        async with aiofiles.open(self._resolve(path), "r") as f:
            return await f.read()

    async def save_file(self, path: str, local_path: str) -> None:
        import shutil
        shutil.copy2(local_path, str(self._resolve(path)))

    async def download_file(self, path: str, local_path: str) -> None:
        import shutil
        shutil.copy2(str(self._resolve(path)), local_path)

    async def get_presigned_url(
        self,
        path: str,
        expires_in: int = 900,
        download_filename: str | None = None,
    ) -> str:
        # Local dev: return a file:// URL. The download_filename hint is
        # not actionable for file:// — the browser uses its own naming.
        del expires_in, download_filename  # silence unused warnings
        return f"file://{self._resolve(path)}"

    async def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    async def copy(self, src_path: str, dst_path: str) -> None:
        import shutil
        src = self._resolve(src_path)
        dst = self._resolve(dst_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))

    async def delete(self, path: str) -> None:
        p = self._resolve(path)
        if p.exists():
            p.unlink()


class S3Storage(StorageBackend):
    """S3-compatible storage for production (also works with MinIO)."""

    def __init__(self, bucket: str, endpoint_url: str = "", region: str = "us-east-1"):
        self.bucket = bucket
        # Explicit timeouts + retry config. Without these, boto3 defaults to
        # 60 s connect / 60 s read with unlimited retries — meaning a single
        # R2 outage or transient stall could pin a request (and any DB
        # session it holds) for minutes. The values below are aggressive
        # enough to fail fast on a degraded R2, conservative enough to
        # tolerate normal latency spikes. read_timeout dominates because
        # PUT/GET bodies vary in size; connect_timeout is short because
        # the TCP handshake to R2 is consistently fast and a slow
        # handshake almost always means the endpoint is down.
        boto_config = BotoConfig(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        kwargs: dict = {"region_name": region, "config": boto_config}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._s3 = boto3.client("s3", **kwargs)

    async def save_json(self, path: str, data: Any) -> None:
        body = json.dumps(data, indent=2, default=str)
        self._s3.put_object(Bucket=self.bucket, Key=path, Body=body, ContentType="application/json")

    async def read_json(self, path: str) -> Any:
        resp = self._s3.get_object(Bucket=self.bucket, Key=path)
        return json.loads(resp["Body"].read())

    async def save_text(self, path: str, text: str) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=path, Body=text, ContentType="text/plain")

    async def read_text(self, path: str) -> str:
        resp = self._s3.get_object(Bucket=self.bucket, Key=path)
        return resp["Body"].read().decode()

    async def save_file(self, path: str, local_path: str) -> None:
        self._s3.upload_file(local_path, self.bucket, path)

    async def download_file(self, path: str, local_path: str) -> None:
        self._s3.download_file(self.bucket, path, local_path)

    async def get_presigned_url(
        self,
        path: str,
        expires_in: int = 900,
        download_filename: str | None = None,
    ) -> str:
        """Return a presigned GET URL.

        When `download_filename` is provided, the URL bakes in a signed
        ``ResponseContentDisposition`` parameter so the response carries
        ``Content-Disposition: attachment; filename="…"`` and browsers
        trigger a download instead of streaming inline.
        """
        params: dict = {"Bucket": self.bucket, "Key": path}
        if download_filename:
            # Quote-escape any stray quotes in the filename; per RFC 6266,
            # quoted-string can't contain bare double-quotes.
            safe = download_filename.replace('"', "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return self._s3.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    async def exists(self, path: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=path)
            return True
        except self._s3.exceptions.ClientError:
            return False

    async def copy(self, src_path: str, dst_path: str) -> None:
        self._s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": src_path},
            Key=dst_path,
        )

    async def delete(self, path: str) -> None:
        try:
            self._s3.delete_object(Bucket=self.bucket, Key=path)
        except Exception:
            pass


def create_storage() -> StorageBackend:
    """Factory: create the right storage backend from env vars."""
    backend = os.getenv("STORAGE_BACKEND", "local")
    if backend == "s3":
        return S3Storage(
            bucket=os.getenv("S3_BUCKET", ""),
            endpoint_url=os.getenv("S3_ENDPOINT_URL", ""),
            region=os.getenv("S3_REGION", "us-east-1"),
        )
    return LocalStorage(os.getenv("DATA_DIR", "./data"))
