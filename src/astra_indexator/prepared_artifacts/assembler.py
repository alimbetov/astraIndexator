from __future__ import annotations

from dataclasses import asdict
from typing import Any

from astra_indexator.normalization import NormalizationProfile, NormalizedDocument
from astra_indexator.splitter.model import LogicalFragment, SplitterProfile

from .model import ArtifactCompatibility, ArtifactIdentity


class PreparedArtifactAssembler:
    """Canonical M4/M5/M6 → M7 adapter; callers do not handcraft artifact dicts."""

    def assemble(
        self,
        *,
        document: NormalizedDocument,
        fragments: tuple[LogicalFragment, ...],
        normalization_profile: NormalizationProfile,
        splitter_profile: SplitterProfile,
        parser_name: str,
        parser_version: str,
        parser_profile: str,
        parser_config: dict[str, Any] | None = None,
        ocr_model_id: str | None = None,
        ocr_artifact_revision: str | None = None,
        ocr_manifest_sha256: str | None = None,
    ) -> tuple[
        ArtifactIdentity,
        ArtifactCompatibility,
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
    ]:
        for fragment in fragments:
            if (
                fragment.document_id != document.document_id
                or fragment.document_version != document.document_version
            ):
                raise ValueError("M7_ASSEMBLER_FRAGMENT_IDENTITY_MISMATCH")
        identity = ArtifactIdentity(
            document.document_id, document.document_version, document.source_sha256
        )
        compatibility = ArtifactCompatibility(
            schema_version="prepared-v1",
            parser_name=parser_name,
            parser_version=parser_version,
            parser_profile=parser_profile,
            parser_config=dict(parser_config or {}),
            normalizer_version=document.normalizer_version,
            normalizer_config=asdict(normalization_profile),
            splitter_profile=splitter_profile.profile_id,
            splitter_version=splitter_profile.version,
            splitter_config=asdict(splitter_profile),
            ocr_model_id=ocr_model_id,
            ocr_artifact_revision=ocr_artifact_revision,
            ocr_manifest_sha256=ocr_manifest_sha256,
        )
        elements = tuple(asdict(element) for element in document.elements)
        fragment_records = tuple(asdict(fragment) for fragment in fragments)
        return identity, compatibility, elements, fragment_records
