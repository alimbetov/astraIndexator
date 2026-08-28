from __future__ import annotations

import re
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DeliveryIdentityError(ValueError):
    """Raised when immutable source/logical delivery identity is unavailable or inconsistent."""


def require_source_sha256(value: str | None, *, field: str = "source_content_hash") -> str:
    if value is None or not _SHA256_RE.fullmatch(value):
        raise DeliveryIdentityError(f"{field} must be a 64-character lowercase SHA-256 hex digest")
    return value


def resolve_verified_source_sha256(*, durable_hash: str | None, payload_hash: str | None) -> str:
    """Return the durable source SHA-256 and reject unverified/conflicting delivery input.

    M8 requires the durable acquisition/job lineage to contain the source hash. A payload hash may
    repeat that evidence (for example from verified M7 replay) but may not replace or contradict it.
    """

    durable = require_source_sha256(durable_hash, field="durable source_content_hash")
    if payload_hash:
        payload = require_source_sha256(payload_hash, field="delivery source_content_hash")
        if payload != durable:
            raise DeliveryIdentityError(
                "delivery source_content_hash differs from durable acquisition source_content_hash"
            )
    return durable


def start_idempotency_key(
    *, document_id: UUID, document_version: int, source_sha256: str
) -> str:
    if document_version <= 0:
        raise DeliveryIdentityError("document_version must be positive")
    digest = require_source_sha256(source_sha256, field="source_sha256")
    return f"astra-indexator:{document_id}:{document_version}:{digest}"
