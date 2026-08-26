from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

import pytest

from astra_indexator.application.prepared_artifact_delivery import (
    PreparedArtifactDeliveryMapper,
    PreparedArtifactDeliveryMappingError,
)
from astra_indexator.application.prepared_artifact_wiring import (
    PreparedArtifactDeliveryInputFactory,
    PreparedArtifactIdentityMismatch,
)
from astra_indexator.prepared_artifacts.model import (
    ArtifactCompatibility,
    ArtifactIdentity,
    ArtifactManifest,
    ArtifactPart,
    PreparedArtifact,
)
from astra_indexator.splitter.model import (
    FragmentSource,
    FragmentStatistics,
    FragmentType,
    LogicalFragment,
    SplitDecision,
)

DOCUMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DOCUMENT_VERSION = 3
SOURCE_SHA256 = "a" * 64


def _fragment(
    fragment_id: str,
    sequence: int,
    fragment_type: FragmentType,
    text: str,
    *,
    context_prefix: str = "",
    hierarchy: tuple[str, ...] = (),
    page_from: int | None = None,
    page_to: int | None = None,
) -> dict[str, object]:
    fragment = LogicalFragment(
        fragment_id=fragment_id,
        document_id=DOCUMENT_ID,
        document_version=DOCUMENT_VERSION,
        sequence=sequence,
        fragment_type=fragment_type,
        normalized_text=text,
        context_prefix=context_prefix,
        hierarchy=hierarchy,
        source=FragmentSource(
            element_ids=(f"element-{sequence}",),
            element_from=f"element-{sequence}",
            element_to=f"element-{sequence}",
            page_from=page_from,
            page_to=page_to,
        ),
        statistics=FragmentStatistics(char_count=len(text), word_count=1, sentence_count=1),
        split=SplitDecision(
            reason="STRUCTURE_BOUNDARY",
            forced=False,
            profile="multilingual-general-v1",
            splitter_version="logical-v1",
        ),
        primary_language="kk",
        languages=("kk",),
        metadata={"source": "m7"},
    )
    return asdict(fragment)


def _artifact(*records: dict[str, object]) -> PreparedArtifact:
    identity = ArtifactIdentity(DOCUMENT_ID, DOCUMENT_VERSION, SOURCE_SHA256)
    compatibility = ArtifactCompatibility(
        schema_version="prepared-v1",
        parser_name="canonical",
        parser_version="m4-v1",
        parser_profile="default",
        normalizer_version="text-normalizer-v1",
        splitter_profile="multilingual-general-v1",
        splitter_version="logical-v1",
    )
    part = ArtifactPart(
        kind="FRAGMENTS",
        path="parts/fragments-00000.jsonl",
        sha256="b" * 64,
        record_count=len(records),
        byte_count=100,
    )
    manifest = ArtifactManifest(
        identity=identity,
        compatibility=compatibility,
        artifact_id="c" * 64,
        compatibility_sha256="d" * 64,
        parts=(part,),
        total_element_count=0,
        total_fragment_count=len(records),
    )
    return PreparedArtifact(manifest=manifest, elements=(), fragments=tuple(records))


def test_m7_fragments_map_to_rooted_deterministic_logical_blocks() -> None:
    artifact = _artifact(
        _fragment(
            "fragment-2",
            2,
            FragmentType.CODE,
            'println("Әлем")',
            hierarchy=("API", "Examples"),
            page_from=2,
            page_to=2,
        ),
        _fragment(
            "fragment-1",
            1,
            FragmentType.PARAGRAPH,
            "Негізгі мәтін",
            context_prefix="Бөлім тақырыбы",
            hierarchy=("Бөлім тақырыбы",),
            page_from=1,
            page_to=1,
        ),
    )

    blocks = PreparedArtifactDeliveryMapper().logical_blocks(artifact)

    assert [block.block_type for block in blocks] == ["DOCUMENT", "PARAGRAPH", "CODE_BLOCK"]
    assert [block.order_index for block in blocks] == [0, 1, 2]
    assert blocks[1].parent_block_id == blocks[0].block_id
    assert blocks[2].parent_block_id == blocks[0].block_id
    assert blocks[1].text == "Бөлім тақырыбы\n\nНегізгі мәтін"
    assert blocks[1].metadata["astra.context_prefix"] == "Бөлім тақырыбы"
    assert blocks[1].metadata["astra.primary_language"] == "kk"
    assert blocks[1].source_location is not None
    assert blocks[1].source_location.page_start == 1
    assert blocks[1].source_location.page_end == 1
    assert blocks[1].source_location.section_path == "Бөлім тақырыбы"
    assert blocks[2].source_location is not None
    assert blocks[2].source_location.heading == "Examples"


def test_factory_asserts_manifest_identity_before_coordinator_input() -> None:
    artifact = _artifact(_fragment("fragment-1", 0, FragmentType.PARAGRAPH, "text"))
    factory = PreparedArtifactDeliveryInputFactory()

    payload = factory.build(
        artifact,
        document_id=DOCUMENT_ID,
        document_version=DOCUMENT_VERSION,
        source_file_name="document.pdf",
        metadata={"preparedArtifactId": artifact.manifest.artifact_id},
    )
    assert payload.source_file_name == "document.pdf"
    assert payload.source_content_hash == SOURCE_SHA256
    assert payload.metadata == {"preparedArtifactId": artifact.manifest.artifact_id}
    assert payload.logical_blocks[0].block_type == "DOCUMENT"

    with pytest.raises(PreparedArtifactIdentityMismatch):
        factory.build(
            artifact,
            document_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            document_version=DOCUMENT_VERSION,
        )


def test_malformed_or_ambiguous_m7_fragment_set_fails_closed() -> None:
    first = _fragment("fragment-1", 0, FragmentType.PARAGRAPH, "one")
    duplicate_sequence = _fragment("fragment-2", 0, FragmentType.PARAGRAPH, "two")
    with pytest.raises(
        PreparedArtifactDeliveryMappingError, match="sequence values must be unique"
    ):
        PreparedArtifactDeliveryMapper().logical_blocks(_artifact(first, duplicate_sequence))

    wrong_identity = _fragment("fragment-3", 3, FragmentType.PARAGRAPH, "three")
    wrong_identity["document_version"] = 99
    with pytest.raises(PreparedArtifactDeliveryMappingError, match="identity does not match"):
        PreparedArtifactDeliveryMapper().logical_blocks(_artifact(wrong_identity))
