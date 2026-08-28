from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import UUID

import pytest

from astra_indexator.application.prepared_artifact_wiring import (
    PreparedArtifactDeliveryInputFactory,
    PreparedArtifactIdentityMismatch,
)
from astra_indexator.astravector.contracts import LogicalBlock
from astra_indexator.prepared_artifacts.model import (
    ArtifactIdentity,
    ArtifactManifest,
    PreparedArtifact,
    PreparedArtifactPart,
    PreparedLogicalFragment,
)

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_SHA256 = "a" * 64
COMPATIBILITY_SHA256 = "b" * 64
MANIFEST_SHA256 = "c" * 64
PART_SHA256 = "d" * 64


def _fragment(
    fragment_id: str,
    *,
    parent_fragment_id: str | None,
    kind: str,
    text: str,
    order_index: int,
) -> PreparedLogicalFragment:
    return PreparedLogicalFragment(
        fragment_id=fragment_id,
        parent_fragment_id=parent_fragment_id,
        kind=kind,
        text=text,
        order_index=order_index,
        metadata={},
        source_links=(),
    )


def _artifact() -> PreparedArtifact:
    identity = ArtifactIdentity(
        document_id=DOCUMENT_ID,
        document_version=1,
        source_sha256=SOURCE_SHA256,
    )
    fragments = (
        _fragment(
            "root",
            parent_fragment_id=None,
            kind="DOCUMENT",
            text="Document",
            order_index=0,
        ),
        _fragment(
            "paragraph-1",
            parent_fragment_id="root",
            kind="PARAGRAPH",
            text="Text",
            order_index=1,
        ),
    )
    part = PreparedArtifactPart(
        part_index=0,
        uri="seaweed://prepared/part-0000.json",
        sha256=PART_SHA256,
        byte_size=10,
        fragment_count=len(fragments),
    )
    manifest = ArtifactManifest(
        schema_version="prepared-artifact-v1",
        identity=identity,
        compatibility_sha256=COMPATIBILITY_SHA256,
        parts=(part,),
        manifest_sha256=MANIFEST_SHA256,
    )
    return PreparedArtifact(manifest=manifest, fragments=fragments)


def test_factory_maps_verified_m7_artifact_to_coordinator_input() -> None:
    delivery = PreparedArtifactDeliveryInputFactory().build(
        _artifact(),
        document_id=DOCUMENT_ID,
        document_version=1,
        source_file_name="document.txt",
        metadata={"source": "m7"},
    )

    assert delivery.source_content_hash == SOURCE_SHA256
    assert delivery.prepared_compatibility_sha256 == COMPATIBILITY_SHA256
    assert delivery.source_file_name == "document.txt"
    assert delivery.metadata == {"source": "m7"}
    assert tuple(block.block_id for block in delivery.logical_blocks) == (
        "root",
        "paragraph-1",
    )
    assert all(isinstance(block, LogicalBlock) for block in delivery.logical_blocks)


def test_factory_is_deterministic_for_same_verified_artifact() -> None:
    artifact = _artifact()
    factory = PreparedArtifactDeliveryInputFactory()
    first = factory.build(
        artifact,
        document_id=DOCUMENT_ID,
        document_version=1,
    )
    second = factory.build(
        artifact,
        document_id=DOCUMENT_ID,
        document_version=1,
    )
    assert first.logical_blocks == second.logical_blocks
    assert first.source_content_hash == second.source_content_hash
    assert first.prepared_compatibility_sha256 == second.prepared_compatibility_sha256


def test_factory_rejects_wrong_document_id() -> None:
    with pytest.raises(PreparedArtifactIdentityMismatch):
        PreparedArtifactDeliveryInputFactory().build(
            _artifact(),
            document_id=UUID("22222222-2222-2222-2222-222222222222"),
            document_version=1,
        )


def test_factory_rejects_wrong_document_version() -> None:
    with pytest.raises(PreparedArtifactIdentityMismatch):
        PreparedArtifactDeliveryInputFactory().build(
            _artifact(),
            document_id=DOCUMENT_ID,
            document_version=2,
        )


def test_prepared_compatibility_evidence_changes_with_manifest_contract() -> None:
    artifact = _artifact()
    changed = replace(
        artifact,
        manifest=replace(
            artifact.manifest,
            compatibility_sha256=hashlib.sha256(b"changed-contract").hexdigest(),
        ),
    )
    factory = PreparedArtifactDeliveryInputFactory()
    original_delivery = factory.build(
        artifact,
        document_id=DOCUMENT_ID,
        document_version=1,
    )
    changed_delivery = factory.build(
        changed,
        document_id=DOCUMENT_ID,
        document_version=1,
    )
    assert (
        original_delivery.prepared_compatibility_sha256
        != changed_delivery.prepared_compatibility_sha256
    )
