from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from astra_indexator.application.delivery_compatibility import (
    DeliveryCompatibilityFingerprint,
)
from astra_indexator.application.delivery_identity import (
    DeliveryIdentityError,
    resolve_verified_source_sha256,
    start_idempotency_key,
)
from astra_indexator.astravector.wire_contract import CURRENT_WIRE_CONTRACT

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_SHA256 = "a" * 64
OTHER_SHA256 = "b" * 64
PREPARED_COMPATIBILITY_SHA256 = "c" * 64


def test_start_idempotency_is_logical_and_deterministic() -> None:
    key = start_idempotency_key(
        document_id=DOCUMENT_ID,
        document_version=7,
        source_sha256=SOURCE_SHA256,
    )
    assert key == f"astra-indexator:{DOCUMENT_ID}:7:{SOURCE_SHA256}"
    assert key == start_idempotency_key(
        document_id=DOCUMENT_ID,
        document_version=7,
        source_sha256=SOURCE_SHA256,
    )
    assert key != start_idempotency_key(
        document_id=DOCUMENT_ID,
        document_version=8,
        source_sha256=SOURCE_SHA256,
    )
    assert key != start_idempotency_key(
        document_id=DOCUMENT_ID,
        document_version=7,
        source_sha256=OTHER_SHA256,
    )


def test_source_identity_requires_durable_verified_sha256() -> None:
    with pytest.raises(DeliveryIdentityError):
        resolve_verified_source_sha256(durable_hash=None, payload_hash=SOURCE_SHA256)
    with pytest.raises(DeliveryIdentityError):
        resolve_verified_source_sha256(durable_hash="not-a-sha", payload_hash=None)
    with pytest.raises(DeliveryIdentityError):
        resolve_verified_source_sha256(
            durable_hash=SOURCE_SHA256,
            payload_hash=OTHER_SHA256,
        )

    assert (
        resolve_verified_source_sha256(
            durable_hash=SOURCE_SHA256,
            payload_hash=SOURCE_SHA256,
        )
        == SOURCE_SHA256
    )


def test_delivery_compatibility_fingerprint_is_deterministic_and_contract_sensitive() -> None:
    baseline = DeliveryCompatibilityFingerprint(PREPARED_COMPATIBILITY_SHA256)
    assert (
        baseline.sha256()
        == DeliveryCompatibilityFingerprint(PREPARED_COMPATIBILITY_SHA256).sha256()
    )

    changed_prepared = DeliveryCompatibilityFingerprint(OTHER_SHA256)
    assert changed_prepared.sha256() != baseline.sha256()

    changed_wire = replace(
        CURRENT_WIRE_CONTRACT,
        proto_blob_sha="0" * 40,
    )
    assert (
        DeliveryCompatibilityFingerprint(
            PREPARED_COMPATIBILITY_SHA256,
            wire=changed_wire,
        ).sha256()
        != baseline.sha256()
    )

    assert (
        DeliveryCompatibilityFingerprint(
            PREPARED_COMPATIBILITY_SHA256,
            mapping_revision="prepared-artifact-delivery-mapper-v2",
        ).sha256()
        != baseline.sha256()
    )
