from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class ReplayDecision(StrEnum):
    REPLAY = "REPLAY"
    REPROCESS = "REPROCESS"


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a 64-character lowercase hex digest")


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    document_id: UUID
    document_version: int
    source_sha256: str

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("documentVersion must be positive")
        _validate_sha256("sourceSha256", self.source_sha256)


@dataclass(frozen=True, slots=True)
class ArtifactCompatibility:
    schema_version: str
    parser_name: str
    parser_version: str
    parser_profile: str
    normalizer_version: str
    splitter_profile: str
    splitter_version: str
    parser_config: dict[str, Any] = field(default_factory=dict)
    normalizer_config: dict[str, Any] = field(default_factory=dict)
    splitter_config: dict[str, Any] = field(default_factory=dict)
    ocr_model_id: str | None = None
    ocr_artifact_revision: str | None = None
    ocr_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        ocr_fields = (self.ocr_model_id, self.ocr_artifact_revision, self.ocr_manifest_sha256)
        if any(value is not None for value in ocr_fields) and not all(value is not None for value in ocr_fields):
            raise ValueError("OCR compatibility identity must be complete or fully absent")
        if self.ocr_manifest_sha256 is not None:
            _validate_sha256("ocrManifestSha256", self.ocr_manifest_sha256)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "parserName": self.parser_name,
            "parserVersion": self.parser_version,
            "parserProfile": self.parser_profile,
            "parserConfig": self.parser_config,
            "normalizerVersion": self.normalizer_version,
            "normalizerConfig": self.normalizer_config,
            "splitterProfile": self.splitter_profile,
            "splitterVersion": self.splitter_version,
            "splitterConfig": self.splitter_config,
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
        _validate_sha256("part.sha256", self.sha256)
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
        if self.format != "ASTRA_PREPARED_ARTIFACT_V1":
            raise ValueError("unsupported prepared artifact format")
        _validate_sha256("artifactId", self.artifact_id)
        _validate_sha256("compatibilitySha256", self.compatibility_sha256)
        paths = [part.path for part in self.parts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact part paths must be unique")
        if self.total_element_count != sum(p.record_count for p in self.parts if p.kind == "ELEMENTS"):
            raise ValueError("totalElementCount does not match parts")
        if self.total_fragment_count != sum(p.record_count for p in self.parts if p.kind == "FRAGMENTS"):
            raise ValueError("totalFragmentCount does not match parts")


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    manifest: ArtifactManifest
    manifest_key: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        _validate_sha256("manifestSha256", self.manifest_sha256)


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    manifest: ArtifactManifest
    elements: tuple[dict[str, Any], ...]
    fragments: tuple[dict[str, Any], ...]
