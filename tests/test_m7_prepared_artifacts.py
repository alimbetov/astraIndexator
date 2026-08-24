from __future__ import annotations

from uuid import uuid4

import pytest

from astra_indexator.application.coordinator import LeaseToken
from astra_indexator.prepared_artifacts import (
    ArtifactCompatibility,
    ArtifactCorruptionError,
    ArtifactIdentity,
    PreparedArtifactPublisher,
    PreparedArtifactReader,
    ReplayDecision,
)


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.write_order: list[str] = []

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = data
        self.write_order.append(key)
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects


def identity() -> ArtifactIdentity:
    return ArtifactIdentity(uuid4(), 7, "a" * 64)


def compatibility(*, splitter_version: str = "logical-v1") -> ArtifactCompatibility:
    return ArtifactCompatibility(
        schema_version="prepared-v1",
        parser_name="canonical",
        parser_version="m4-v1",
        parser_profile="default",
        normalizer_version="m6-v1",
        splitter_profile="multilingual-general-v1",
        splitter_version=splitter_version,
        ocr_model_id="ppocrv5-mobile-det-cyrillic-mobile-rec-onnx-fp32",
        ocr_artifact_revision="2026.08.candidate1",
        ocr_manifest_sha256="b" * 64,
    )


def token() -> LeaseToken:
    return LeaseToken(uuid4(), "worker-a", 3, uuid4())


def test_manifest_is_commit_marker_and_parts_are_partitioned() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    calls: list[int] = []
    lease = token()

    manifest = publisher.publish(
        token=lease,
        assert_current_lease=lambda value: calls.append(value.lease_generation),
        identity=identity(),
        compatibility=compatibility(),
        elements=({"elementId": str(i), "text": f"элемент {i}"} for i in range(3)),
        fragments=({"fragmentId": str(i), "text": f"бөлік {i}"} for i in range(2)),
        max_records_per_part=2,
    )

    assert [p.path for p in manifest.parts] == [
        "parts/elements-00000.jsonl",
        "parts/elements-00001.jsonl",
        "parts/fragments-00000.jsonl",
    ]
    assert store.write_order[-1].endswith("/manifest.json")
    assert len(calls) == len(manifest.parts) + 1
    assert manifest.total_element_count == 3
    assert manifest.total_fragment_count == 2


def test_publish_is_idempotent_for_identical_content() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    ident = identity()
    lease = token()
    kwargs = dict(
        token=lease,
        assert_current_lease=lambda _: None,
        identity=ident,
        compatibility=compatibility(),
        elements=[{"elementId": "e1", "text": "Әлем"}],
        fragments=[{"fragmentId": "f1", "text": "Қазақ тілі"}],
    )
    first = publisher.publish(**kwargs)
    write_count = len(store.write_order)
    second = publisher.publish(**kwargs)
    assert first.artifact_id == second.artifact_id
    assert len(store.write_order) == write_count


def test_stale_lease_cannot_publish_manifest_commit_marker() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    calls = 0

    def fence(_: LeaseToken) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise RuntimeError("LEASE_LOST")

    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        publisher.publish(
            token=token(),
            assert_current_lease=fence,
            identity=identity(),
            compatibility=compatibility(),
            elements=[{"elementId": "e1"}],
            fragments=[{"fragmentId": "f1"}],
        )

    assert not any(key.endswith("/manifest.json") for key in store.objects)


def test_replay_requires_exact_pipeline_compatibility() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    reader = PreparedArtifactReader(store)
    manifest = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[],
        fragments=[],
    )
    assert reader.replay_decision(manifest, compatibility()) is ReplayDecision.REPLAY
    assert reader.replay_decision(manifest, compatibility(splitter_version="logical-v2")) is ReplayDecision.REPROCESS


def test_reader_rejects_tampered_part() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    reader = PreparedArtifactReader(store)
    manifest = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[{"elementId": "e1", "text": "original"}],
        fragments=[],
    )
    part_key = next(key for key in store.objects if key.endswith("elements-00000.jsonl"))
    store.objects[part_key] = b'{"elementId":"e1","text":"tampered"}\n'
    with pytest.raises(ArtifactCorruptionError, match="integrity failure"):
        reader.load(manifest)
