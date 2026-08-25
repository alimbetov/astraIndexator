from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from astra_indexator.application.coordinator import LeaseToken

from .model import (
    ArtifactCompatibility,
    ArtifactIdentity,
    ArtifactManifest,
    ArtifactPart,
    PreparedArtifact,
    PublishedArtifact,
    ReplayDecision,
)
from .store import ArtifactObjectStore


class ArtifactCorruptionError(RuntimeError):
    pass


class ArtifactPublicationConflict(RuntimeError):
    pass


class ArtifactTooLargeError(RuntimeError):
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


def manifest_bytes(manifest: ArtifactManifest) -> bytes:
    return canonical_json_bytes(_manifest_dict(manifest)) + b"\n"


def _artifact_id(
    identity: ArtifactIdentity, compatibility_sha: str, parts: tuple[ArtifactPart, ...]
) -> str:
    seed = {
        "identity": {
            "documentId": str(identity.document_id),
            "documentVersion": identity.document_version,
            "sourceSha256": identity.source_sha256,
        },
        "compatibilitySha256": compatibility_sha,
        "parts": [{"path": part.path, "sha256": part.sha256} for part in parts],
    }
    return _sha256(canonical_json_bytes(seed))


def parse_manifest_bytes(payload: bytes) -> ArtifactManifest:
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactCorruptionError("prepared artifact manifest is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise ArtifactCorruptionError("prepared artifact manifest root must be an object")
    required = {
        "format",
        "artifactId",
        "identity",
        "compatibility",
        "compatibilitySha256",
        "parts",
        "totals",
    }
    if set(raw) != required:
        raise ArtifactCorruptionError("prepared artifact manifest fields are not canonical v1")
    try:
        identity_raw = raw["identity"]
        compatibility_raw = raw["compatibility"]
        totals = raw["totals"]
        if set(identity_raw) != {"documentId", "documentVersion", "sourceSha256"}:
            raise ValueError("identity fields")
        compatibility = ArtifactCompatibility(
            schema_version=compatibility_raw["schemaVersion"],
            parser_name=compatibility_raw["parserName"],
            parser_version=compatibility_raw["parserVersion"],
            parser_profile=compatibility_raw["parserProfile"],
            parser_config=dict(compatibility_raw.get("parserConfig") or {}),
            normalizer_version=compatibility_raw["normalizerVersion"],
            normalizer_config=dict(compatibility_raw.get("normalizerConfig") or {}),
            splitter_profile=compatibility_raw["splitterProfile"],
            splitter_version=compatibility_raw["splitterVersion"],
            splitter_config=dict(compatibility_raw.get("splitterConfig") or {}),
            ocr_model_id=compatibility_raw.get("ocrModelId"),
            ocr_artifact_revision=compatibility_raw.get("ocrArtifactRevision"),
            ocr_manifest_sha256=compatibility_raw.get("ocrManifestSha256"),
        )
        identity = ArtifactIdentity(
            document_id=UUID(identity_raw["documentId"]),
            document_version=int(identity_raw["documentVersion"]),
            source_sha256=identity_raw["sourceSha256"],
        )
        parts = tuple(
            ArtifactPart(
                kind=part["kind"],
                path=part["path"],
                sha256=part["sha256"],
                record_count=int(part["recordCount"]),
                byte_count=int(part["byteCount"]),
            )
            for part in raw["parts"]
        )
        manifest = ArtifactManifest(
            identity=identity,
            compatibility=compatibility,
            artifact_id=raw["artifactId"],
            compatibility_sha256=raw["compatibilitySha256"],
            parts=parts,
            total_element_count=int(totals["elements"]),
            total_fragment_count=int(totals["fragments"]),
            format=raw["format"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactCorruptionError("prepared artifact manifest violates v1 schema") from exc
    expected_compatibility_sha = _sha256(canonical_json_bytes(manifest.compatibility.as_dict()))
    if expected_compatibility_sha != manifest.compatibility_sha256:
        raise ArtifactCorruptionError("prepared artifact compatibility digest mismatch")
    expected_artifact_id = _artifact_id(
        manifest.identity, manifest.compatibility_sha256, manifest.parts
    )
    if expected_artifact_id != manifest.artifact_id:
        raise ArtifactCorruptionError("prepared artifact id mismatch")
    return manifest


class PreparedArtifactPublisher:
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
        max_bytes_per_part: int = 32 * 1024 * 1024,
    ) -> PublishedArtifact:
        if max_records_per_part <= 0 or max_bytes_per_part <= 0:
            raise ValueError("prepared artifact part limits must be positive")
        element_parts = self._encode_partitioned(
            "ELEMENTS", "elements", elements, max_records_per_part, max_bytes_per_part
        )
        fragment_parts = self._encode_partitioned(
            "FRAGMENTS", "fragments", fragments, max_records_per_part, max_bytes_per_part
        )
        encoded_parts = element_parts + fragment_parts
        compatibility_sha = _sha256(canonical_json_bytes(compatibility.as_dict()))
        parts = tuple(part for part, _ in encoded_parts)
        artifact_id = _artifact_id(identity, compatibility_sha, parts)
        root = self._root(identity, artifact_id)
        for part, payload in encoded_parts:
            assert_current_lease(token)
            self._put_immutable(f"{root}/{part.path}", payload, content_type="application/x-ndjson")
        manifest = ArtifactManifest(
            identity=identity,
            compatibility=compatibility,
            artifact_id=artifact_id,
            compatibility_sha256=compatibility_sha,
            parts=parts,
            total_element_count=sum(p.record_count for p, _ in element_parts),
            total_fragment_count=sum(p.record_count for p, _ in fragment_parts),
        )
        encoded_manifest = manifest_bytes(manifest)
        manifest_key = f"{root}/manifest.json"
        assert_current_lease(token)
        self._put_immutable(manifest_key, encoded_manifest, content_type="application/json")
        return PublishedArtifact(manifest, manifest_key, _sha256(encoded_manifest))

    @staticmethod
    def _encode_partitioned(
        kind: str,
        stem: str,
        records: Iterable[Mapping[str, Any]],
        max_records: int,
        max_bytes: int,
    ) -> list[tuple[ArtifactPart, bytes]]:
        result: list[tuple[ArtifactPart, bytes]] = []
        batch: list[bytes] = []
        batch_bytes = 0
        index = 0

        def flush() -> None:
            nonlocal batch, batch_bytes, index
            if not batch:
                return
            payload = b"".join(batch)
            path = f"parts/{stem}-{index:05d}.jsonl"
            result.append(
                (ArtifactPart(kind, path, _sha256(payload), len(batch), len(payload)), payload)
            )
            batch = []
            batch_bytes = 0
            index += 1

        for record in records:
            encoded = canonical_json_bytes(dict(record)) + b"\n"
            if len(encoded) > max_bytes:
                raise ArtifactTooLargeError(
                    f"single prepared artifact record exceeds byte limit: {stem}"
                )
            if batch and (len(batch) >= max_records or batch_bytes + len(encoded) > max_bytes):
                flush()
            batch.append(encoded)
            batch_bytes += len(encoded)
        flush()
        return result

    def _root(self, identity: ArtifactIdentity, artifact_id: str) -> str:
        return f"{self._prefix}/{identity.document_id}/{identity.document_version}/{identity.source_sha256}/{artifact_id}"

    def _put_immutable(self, key: str, data: bytes, *, content_type: str) -> None:
        if self._store.put_if_absent(key, data, content_type=content_type):
            return
        if self._store.get(key) != data:
            raise ArtifactPublicationConflict(f"immutable artifact collision at {key}")


class PreparedArtifactReader:
    def __init__(self, store: ArtifactObjectStore, *, prefix: str = "prepared/v1") -> None:
        self._store = store
        self._prefix = prefix.strip("/")

    def replay_decision(
        self, manifest: ArtifactManifest, expected: ArtifactCompatibility
    ) -> ReplayDecision:
        expected_sha = _sha256(canonical_json_bytes(expected.as_dict()))
        return (
            ReplayDecision.REPLAY
            if expected_sha == manifest.compatibility_sha256
            else ReplayDecision.REPROCESS
        )

    def load_manifest(self, key: str, *, expected_sha256: str | None = None) -> ArtifactManifest:
        payload = self._store.get(key)
        if expected_sha256 is not None and _sha256(payload) != expected_sha256:
            raise ArtifactCorruptionError("prepared artifact manifest checkpoint digest mismatch")
        return parse_manifest_bytes(payload)

    def load(self, manifest: ArtifactManifest) -> PreparedArtifact:
        root = f"{self._prefix}/{manifest.identity.document_id}/{manifest.identity.document_version}/{manifest.identity.source_sha256}/{manifest.artifact_id}"
        elements: list[dict[str, Any]] = []
        fragments: list[dict[str, Any]] = []
        for part in manifest.parts:
            records = tuple(self._stream_part(f"{root}/{part.path}", part))
            (elements if part.kind == "ELEMENTS" else fragments).extend(records)
        if (
            len(elements) != manifest.total_element_count
            or len(fragments) != manifest.total_fragment_count
        ):
            raise ArtifactCorruptionError(
                "prepared artifact manifest totals do not match loaded records"
            )
        return PreparedArtifact(
            manifest=manifest, elements=tuple(elements), fragments=tuple(fragments)
        )

    def _stream_part(self, key: str, part: ArtifactPart) -> Iterator[dict[str, Any]]:
        digest = hashlib.sha256()
        byte_count = 0
        record_count = 0
        buffer = b""
        try:
            chunks = self._store.iter_bytes(key)
        except AttributeError:
            chunks = iter((self._store.get(key),))
        for chunk in chunks:
            digest.update(chunk)
            byte_count += len(chunk)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ArtifactCorruptionError(
                        f"prepared artifact JSONL corruption: {part.path}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ArtifactCorruptionError(
                        f"prepared artifact record is not an object: {part.path}"
                    )
                record_count += 1
                yield record
        if buffer:
            raise ArtifactCorruptionError(f"prepared artifact truncated JSONL record: {part.path}")
        if byte_count != part.byte_count or digest.hexdigest() != part.sha256:
            raise ArtifactCorruptionError(f"prepared artifact integrity failure: {part.path}")
        if record_count != part.record_count:
            raise ArtifactCorruptionError(f"prepared artifact record-count failure: {part.path}")
