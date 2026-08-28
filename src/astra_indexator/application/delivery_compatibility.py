from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from astra_indexator.astravector.wire_contract import CURRENT_WIRE_CONTRACT, WireContractRevision

DELIVERY_FINGERPRINT_VERSION = "m8-delivery-compatibility-v1"
LOGICAL_BLOCK_MAPPING_REVISION = "prepared-artifact-delivery-mapper-v1"
STRUCTURAL_VALIDATION_REVISION = "logical-block-validation-v1"


class DeliveryCompatibilityError(ValueError):
    """Raised when replay delivery compatibility evidence is missing or malformed."""


@dataclass(frozen=True, slots=True)
class DeliveryCompatibilityFingerprint:
    prepared_compatibility_sha256: str
    wire: WireContractRevision = CURRENT_WIRE_CONTRACT
    mapping_revision: str = LOGICAL_BLOCK_MAPPING_REVISION
    validation_revision: str = STRUCTURAL_VALIDATION_REVISION
    fingerprint_version: str = DELIVERY_FINGERPRINT_VERSION

    def __post_init__(self) -> None:
        value = self.prepared_compatibility_sha256
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise DeliveryCompatibilityError(
                "prepared_compatibility_sha256 must be a 64-character lowercase SHA-256 digest"
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "fingerprintVersion": self.fingerprint_version,
            "preparedCompatibilitySha256": self.prepared_compatibility_sha256,
            "mappingRevision": self.mapping_revision,
            "validationRevision": self.validation_revision,
            "wire": asdict(self.wire),
        }

    def sha256(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def delivery_compatibility_sha256(prepared_compatibility_sha256: str) -> str:
    return DeliveryCompatibilityFingerprint(prepared_compatibility_sha256).sha256()
