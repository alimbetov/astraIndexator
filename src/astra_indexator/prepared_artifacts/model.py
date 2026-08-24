from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class ReplayDecision(StrEnum):
    REPLAY = "REPLAY"
    REPROCESS = "REPROCESS"


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    document_id: UUID
    document_version: int
    source_sha256: str

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("documentVersion must be positive")
        digest = self.source_sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("sourceSha256 must be a 64-character lowercase hex digest")


@dataclass(frozen=True, slots=True)
class ArtifactCompatibility:
    schema_version: str
    parser_name: str
    parser_version: str
    parser_profile: str
    normalizer_version: str
    splitter_profile: str
    splitter_version: str
    ocr_model_id: str | None = None
    ocr_artifact_revision: str | None = None
    ocr_manifest_sha256: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "schemaVersion": self.schema_version,
            "parserName": self.parser_name,
            "parserVersion": self.parser_version,
            "parserProfile": self.parser_profile,
            "normalizerVersion": self.normalizer_version,
            "splitterProfile": self.splitter_profile,
            "splitterVersion": self.splitter_version,
            "ocrModelId": self.ocr_model_id,
            "ocrArtifactRevision": self.ocr_artifact_revision,
            "ocrManifestSha256": self.ocr_manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class ArtifactPart:
    kind: str
    path: str
    sha256: str
    record_count: int
    byte_count: int

    def __post_init__(self) -> None:
        if self.kind not in {"ELEMENTS", "FRAGMENTS"}:
            raise ValueError("unsupported prepared artifact part kind")
        if not self.path.startswith("parts/") or not self.path.endswith(".jsonl"):
            raise ValueError("artifact part path must be parts/*.jsonl")
        if self.record_count < 0 or self.byte_count < 0:
            raise ValueError("artifact part counts must be non-negative")


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    identity: ArtifactIdentity
    compatibility: ArtifactCompatibility
    artifact_id: str
    compatibility_sha256: str
    parts: tuple[ArtifactPart, ...]
    total_element_count: int
    total_fragment_count: int
    format: str = "ASTRA_PREPARED_ARTIFACT_V1"

    def __post_init__(self) -> None:
        paths = [part.path for part in self.parts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact part paths must be unique")
        if self.total_element_count != sum(p.record_count for p in self.parts if p.kind == "ELEMENTS"):
            raise ValueError("totalElementCount does not match parts")
        if self.total_fragment_count != sum(p.record_count for p in self.parts if p.kind == "FRAGMENTS"):
            raise ValueError("totalFragmentCount does not match parts")


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    manifest: ArtifactManifest
    elements: tuple[dict[str, Any], ...]
    fragments: tuple[dict[str, Any], ...]
