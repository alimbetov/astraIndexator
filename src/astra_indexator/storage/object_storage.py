from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class StorageRef:
    storage: str
    bucket: str
    object_key: str

    @classmethod
    def parse(cls, value: str) -> "StorageRef":
        parsed = urlparse(value)
        if parsed.scheme.lower() != "seaweed":
            raise ValueError("source_uri must use seaweed:// scheme")
        bucket = parsed.netloc.strip()
        object_key = parsed.path.lstrip("/")
        if not bucket or not object_key:
            raise ValueError("source_uri must include bucket and object key")
        if ".." in object_key.split("/"):
            raise ValueError("source_uri object key must not contain parent traversal")
        return cls(storage="SEAWEEDFS", bucket=bucket, object_key=object_key)

    def as_uri(self) -> str:
        return f"seaweed://{self.bucket}/{self.object_key}"


@dataclass(frozen=True, slots=True)
class ObjectHead:
    exists: bool
    size_bytes: int | None = None
    etag: str | None = None
    version_id: str | None = None
    content_type: str | None = None
    last_modified: datetime | None = None


class ObjectStorage(Protocol):
    def head(self, ref: StorageRef) -> ObjectHead: ...

    def iter_bytes(self, ref: StorageRef, *, chunk_size: int = 1024 * 1024) -> Iterable[bytes]: ...
