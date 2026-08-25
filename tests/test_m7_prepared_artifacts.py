from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest

from astra_indexator.application.coordinator import LeaseToken
from astra_indexator.prepared_artifacts import (
    ArtifactCompatibility,
    ArtifactCorruptionError,
    ArtifactIdentity,
    ArtifactTooLargeError,
    PreparedArtifactPublisher,
    PreparedArtifactReader,
    ReplayDecision,
    parse_manifest_bytes,
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
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def iter_bytes(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        payload = self.get(key)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def exists(self, key: str) -> bool:
        return key in self.objects


def identity() -> ArtifactIdentity:
    return ArtifactIdentity(uuid4(), 7, "a" * 64)


def compatibility(*, splitter_version: str = "logical-v1", hard_max_chars: int = 12000) -> ArtifactCompatibility:
    return ArtifactCompatibility(
        schema_version="prepared-v1",
        parser_name="canonical",
        parser_version="m4-v1",
        parser_profile="default",
        parser_config={"readingOrder": "canonical-v1"},
        normalizer_version="m6-v1",
        normalizer_config={"unicodeForm": "NFC"},
        splitter_profile="multilingual-general-v1",
        splitter_version=splitter_version,
        splitter_config={"hardMaxChars": hard_max_chars},
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
    published = publisher.publish(
        token=lease,
        assert_current_lease=lambda value: calls.append(value.lease_generation),
        identity=identity(),
        compatibility=compatibility(),
        elements=({"elementId": str(i), "text": f"элемент {i}"} for i in range(3)),
        fragments=({"fragmentId": str(i), "text": f"бөлік {i}"} for i in range(2)),
        max_records_per_part=2,
    )
    manifest = published.manifest
    assert [p.path for p in manifest.parts] == [
        "parts/elements-00000.jsonl",
        "parts/elements-00001.jsonl",
        "parts/fragments-00000.jsonl",
    ]
    assert store.write_order[-1] == published.manifest_key
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
    assert first.manifest.artifact_id == second.manifest.artifact_id
    assert first.manifest_sha256 == second.manifest_sha256
    assert len(store.write_order) == write_count


def test_crash_before_manifest_leaves_no_commit_marker() -> None:
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
    assert any("/parts/" in key for key in store.objects)
    assert not any(key.endswith("/manifest.json") for key in store.objects)


def test_replay_requires_full_effective_pipeline_compatibility() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    reader = PreparedArtifactReader(store)
    published = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[],
        fragments=[],
    )
    manifest = published.manifest
    assert reader.replay_decision(manifest, compatibility()) is ReplayDecision.REPLAY
    assert reader.replay_decision(manifest, compatibility(splitter_version="logical-v2")) is ReplayDecision.REPROCESS
    assert reader.replay_decision(manifest, compatibility(hard_max_chars=16000)) is ReplayDecision.REPROCESS


def test_manifest_is_loaded_and_verified_from_durable_bytes() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    reader = PreparedArtifactReader(store)
    published = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[{"elementId": "e1"}],
        fragments=[{"fragmentId": "f1"}],
    )
    restored = reader.load_manifest(published.manifest_key, expected_sha256=published.manifest_sha256)
    assert restored == published.manifest
    replay = reader.load(restored)
    assert replay.elements[0]["elementId"] == "e1"
    assert replay.fragments[0]["fragmentId"] == "f1"


def test_corrupted_manifest_fails_closed() -> None:
    store = MemoryStore()
    published = PreparedArtifactPublisher(store).publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[],
        fragments=[],
    )
    store.objects[published.manifest_key] += b" "
    with pytest.raises(ArtifactCorruptionError, match="checkpoint digest mismatch"):
        PreparedArtifactReader(store).load_manifest(
            published.manifest_key,
            expected_sha256=published.manifest_sha256,
        )


def test_manifest_recomputation_detects_tampered_artifact_id() -> None:
    store = MemoryStore()
    published = PreparedArtifactPublisher(store).publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[],
        fragments=[],
    )
    payload = store.objects[published.manifest_key].replace(
        published.manifest.artifact_id.encode(), b"0" * 64
    )
    with pytest.raises(ArtifactCorruptionError, match="artifact id mismatch"):
        parse_manifest_bytes(payload)


def test_reader_rejects_missing_and_truncated_parts() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    reader = PreparedArtifactReader(store)
    published = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[{"elementId": "e1", "text": "original"}],
        fragments=[],
    )
    part_key = next(key for key in store.objects if key.endswith("elements-00000.jsonl"))
    original = store.objects.pop(part_key)
    with pytest.raises(FileNotFoundError):
        reader.load(published.manifest)
    store.objects[part_key] = original[:-1]
    with pytest.raises(ArtifactCorruptionError, match="truncated JSONL"):
        reader.load(published.manifest)


def test_byte_bound_splits_and_rejects_single_oversized_record() -> None:
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    published = publisher.publish(
        token=token(),
        assert_current_lease=lambda _: None,
        identity=identity(),
        compatibility=compatibility(),
        elements=[{"text": "a" * 20}, {"text": "b" * 20}],
        fragments=[],
        max_records_per_part=100,
        max_bytes_per_part=40,
    )
    assert len(published.manifest.parts) == 2
    with pytest.raises(ArtifactTooLargeError):
        publisher.publish(
            token=token(),
            assert_current_lease=lambda _: None,
            identity=identity(),
            compatibility=compatibility(),
            elements=[{"text": "x" * 100}],
            fragments=[],
            max_bytes_per_part=32,
        )
