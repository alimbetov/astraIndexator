from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol
from urllib.parse import quote

import httpx


class ArtifactObjectStore(Protocol):
    """Immutable object operations required by M7."""

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool: ...

    def get(self, key: str) -> bytes: ...

    def iter_bytes(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...


class SeaweedPreparedArtifactStore:
    """SeaweedFS Filer adapter for immutable M7 prepared artifacts."""

    def __init__(self, base_url: str, *, bucket: str, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket.strip("/")
        if not self._bucket:
            raise ValueError("artifact bucket must not be blank")
        self._client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=True)

    @property
    def bucket(self) -> str:
        return self._bucket

    def _url(self, key: str) -> str:
        clean = key.strip("/")
        if not clean or ".." in clean.split("/"):
            raise ValueError("invalid artifact object key")
        encoded = "/".join(quote(part, safe="") for part in (self._bucket, *clean.split("/")))
        return f"{self._base_url}/{encoded}"

    def exists(self, key: str) -> bool:
        response = self._client.head(self._url(key))
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def get(self, key: str) -> bytes:
        response = self._client.get(self._url(key))
        if response.status_code == 404:
            raise FileNotFoundError(key)
        response.raise_for_status()
        return response.content

    def iter_bytes(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        with self._client.stream("GET", self._url(key)) as response:
            if response.status_code == 404:
                raise FileNotFoundError(key)
            response.raise_for_status()
            yield from response.iter_bytes(chunk_size=chunk_size)

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        if self.exists(key):
            return False
        response = self._client.put(self._url(key), content=data, headers={"content-type": content_type})
        response.raise_for_status()
        return True
