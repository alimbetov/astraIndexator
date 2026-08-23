from __future__ import annotations

from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from .object_storage import ObjectHead, StorageRef


class SeaweedFilerStorage:
    """SeaweedFS Filer HTTP adapter.

    Credentials/transport policy are supplied through the configured httpx client.
    Durable object references never contain credentials.
    """

    def __init__(self, base_url: str, *, client: httpx.Client | None = None, timeout_seconds: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def _url(self, ref: StorageRef) -> str:
        parts = [quote(part, safe="") for part in (ref.bucket, *ref.object_key.split("/"))]
        return f"{self._base_url}/{'/'.join(parts)}"

    def head(self, ref: StorageRef) -> ObjectHead:
        response = self._client.head(self._url(ref))
        if response.status_code == 404:
            return ObjectHead(exists=False)
        response.raise_for_status()
        last_modified = response.headers.get("last-modified")
        return ObjectHead(
            exists=True,
            size_bytes=int(response.headers["content-length"]) if response.headers.get("content-length") else None,
            etag=response.headers.get("etag"),
            version_id=response.headers.get("x-seaweedfs-version-id") or response.headers.get("x-amz-version-id"),
            content_type=response.headers.get("content-type"),
            last_modified=parsedate_to_datetime(last_modified) if last_modified else None,
        )

    def iter_bytes(self, ref: StorageRef, *, chunk_size: int = 1024 * 1024):
        with self._client.stream("GET", self._url(ref)) as response:
            if response.status_code == 404:
                raise FileNotFoundError(ref.as_uri())
            response.raise_for_status()
            yield from response.iter_bytes(chunk_size=chunk_size)
