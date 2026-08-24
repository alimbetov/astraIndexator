from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping
from uuid import UUID

from astra_indexator.application.coordinator import LeaseToken

from .model import (
    ArtifactCompatibility,
    ArtifactIdentity,
    ArtifactManifest,
    ArtifactPart,
    PreparedArtifact,
    ReplayDecision,
)
from .store import ArtifactObjectStore


class ArtifactCorruptionError(RuntimeError):
    pass


class ArtifactPublicationConflict(RuntimeError):
    pass


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (UUID, Enum)):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"unsupported canonical JSON value: {type(value)!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """Stable UTF-8 JSON used only by the M7 artifact format, never M8 wire hashes."""
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(records: Iterable[Mapping[str, Any]]) -> tuple[bytes, int]:
    lines: list[bytes] = []
    count = 0
    for record in records:
        lines.append(canonical_json_bytes(dict(record)) + b"\n")
        count += 1
    return b"".join(lines), count


def _part(kind: str, path: str, payload: bytes, count: int) -> ArtifactPart:
    return ArtifactPart(kind=kind, path=path, sha256=_sha256(payload), record_count=count, byte_count=len(payload))


def _manifest_dict(manifest: ArtifactManifest) -> dict[str, Any]:
    return {
        "format": manifest.format,
        "artifactId": manifest.artifact_id,
        "identity": {
            "documentId": str(manifest.identity.document_id),
            "documentVersion": manifest.identity.document_version,
            "sourceSha256": manifest.identity.source_sha256,
        },
        "compatibility": manifest.compatibility.as_dict(),
        "compatibilitySha256": manifest.compatibility_sha256,
        "parts": [
            {
                "kind": p.kind,
                "path": p.path,
                "sha256": p.sha256,
                "recordCount": p.record_count,
                "byteCount": p.byte_count,
            }
            for p in manifest.parts
        ],
        "totals": {
            "elements": manifest.total_element_count,
            "fragments": manifest.total_fragment_count,
        },
    }


class PreparedArtifactPublisher:
    """Crash-safe M7 publisher: immutable parts first, manifest commit marker last."""

    def __init__(self, store: ArtifactObjectStore, *, prefix: str = "prepared/v1") -> None:
        self._store = store
        self._prefix = prefix.strip("/")

    def publish(
        self,
        *,
        token: LeaseToken,
        assert_current_lease: Callable[[LeaseToken], None],
        identity: ArtifactIdentity,
        compatibility: ArtifactCompatibility,
        elements: Iterable[Mapping[str, Any]],
        fragments: Iterable[Mapping[str, Any]],
        max_records_per_part: int = 10_000,
    ) -> ArtifactManifest:
        if max_records_per_part <= 0:
            raise ValueError("max_records_per_part must be positive")

        element_parts = self._encode_partitioned("ELEMENTS", "elements", elements, max_records_per_part)
        fragment_parts = self._encode_partitioned("FRAGMENTS", "fragments", fragments, max_records_per_part)
        encoded_parts = element_parts + fragment_parts

        compatibility_sha = _sha256(canonical_json_bytes(compatibility.as_dict()))
        artifact_seed = {
            "identity": {
                "documentId": str(identity.document_id),
                "documentVersion": identity.document_version,
                "sourceSha256": identity.source_sha256,
            },
            "compatibilitySha256": compatibility_sha,
            "parts": [{"path": part.path, "sha256": part.sha256} for part, _ in encoded_parts],
        }
        artifact_id = _sha256(canonical_json_bytes(artifact_seed))
        root = self._root(identity, artifact_id)

        # Every externally visible mutation is fenced. A stale worker may have
        # uploaded unreachable immutable parts, but it can never publish the
        # manifest commit marker after losing its lease.
        for part, payload in encoded_parts:
            assert_current_lease(token)
            self._put_immutable(f"{root}/{part.path}", payload, content_type="application/x-ndjson")

        manifest = ArtifactManifest(
            identity=identity,
            compatibility=compatibility,
            artifact_id=artifact_id,
            compatibility_sha256=compatibility_sha,
            parts=tuple(part for part, _ in encoded_parts),
            total_element_count=sum(p.record_count for p, _ in element_parts),
            total_fragment_count=sum(p.record_count for p, _ in fragment_parts),
        )
        manifest_bytes = canonical_json_bytes(_manifest_dict(manifest)) + b"\n"
        assert_current_lease(token)
        self._put_immutable(f"{root}/manifest.json", manifest_bytes, content_type="application/json")
        return manifest

    @staticmethod
    def _encode_partitioned(kind: str, stem: str, records: Iterable[Mapping[str, Any]], limit: int):
        result: list[tuple[ArtifactPart, bytes]] = []
        batch: list[Mapping[str, Any]] = []
        index = 0
        for record in records:
            batch.append(record)
            if len(batch) == limit:
                payload, count = _jsonl(batch)
                path = f"parts/{stem}-{index:05d}.jsonl"
                result.append((_part(kind, path, payload, count), payload))
                batch = []
                index += 1
        if batch:
            payload, count = _jsonl(batch)
            path = f"parts/{stem}-{index:05d}.jsonl"
            result.append((_part(kind, path, payload, count), payload))
        return result

    def _root(self, identity: ArtifactIdentity, artifact_id: str) -> str:
        return (
            f"{self._prefix}/{identity.document_id}/{identity.document_version}/"
            f"{identity.source_sha256}/{artifact_id}"
        )

    def _put_immutable(self, key: str, data: bytes, *, content_type: str) -> None:
        if self._store.put_if_absent(key, data, content_type=content_type):
            return
        existing = self._store.get(key)
        if existing != data:
            raise ArtifactPublicationConflict(f"immutable artifact collision at {key}")


class PreparedArtifactReader:
    def __init__(self, store: ArtifactObjectStore, *, prefix: str = "prepared/v1") -> None:
        self._store = store
        self._prefix = prefix.strip("/")

    def replay_decision(self, manifest: ArtifactManifest, expected: ArtifactCompatibility) -> ReplayDecision:
        expected_sha = _sha256(canonical_json_bytes(expected.as_dict()))
        return ReplayDecision.REPLAY if expected_sha == manifest.compatibility_sha256 else ReplayDecision.REPROCESS

    def load(self, manifest: ArtifactManifest) -> PreparedArtifact:
        root = (
            f"{self._prefix}/{manifest.identity.document_id}/{manifest.identity.document_version}/"
            f"{manifest.identity.source_sha256}/{manifest.artifact_id}"
        )
        elements: list[dict[str, Any]] = []
        fragments: list[dict[str, Any]] = []
        for part in manifest.parts:
            payload = self._store.get(f"{root}/{part.path}")
            if len(payload) != part.byte_count or _sha256(payload) != part.sha256:
                raise ArtifactCorruptionError(f"prepared artifact integrity failure: {part.path}")
            records = [json.loads(line) for line in payload.splitlines() if line]
            if len(records) != part.record_count:
                raise ArtifactCorruptionError(f"prepared artifact record-count failure: {part.path}")
            (elements if part.kind == "ELEMENTS" else fragments).extend(records)
        if len(elements) != manifest.total_element_count or len(fragments) != manifest.total_fragment_count:
            raise ArtifactCorruptionError("prepared artifact manifest totals do not match loaded records")
        return PreparedArtifact(manifest=manifest, elements=tuple(elements), fragments=tuple(fragments))
