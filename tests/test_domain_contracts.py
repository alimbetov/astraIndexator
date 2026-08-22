from uuid import uuid4

import pytest

from astra_indexator.domain.contracts import (
    AccessZoneCode,
    CANONICAL_ACCESS_ZONES,
    DocumentIdentity,
    KnowledgeType,
)


def test_all_ten_canonical_access_zone_roots_are_frozen() -> None:
    assert CANONICAL_ACCESS_ZONES == {
        KnowledgeType.GENERAL: "0000",
        KnowledgeType.CORPORATE: "0100",
        KnowledgeType.REGULATORY: "0200",
        KnowledgeType.LEGAL: "0300",
        KnowledgeType.FINANCE: "0400",
        KnowledgeType.HR: "0500",
        KnowledgeType.TECHNICAL: "0600",
        KnowledgeType.OPERATIONS: "0700",
        KnowledgeType.SECURITY: "0800",
        KnowledgeType.ARCHIVE: "0900",
    }
    assert all(AccessZoneCode(code).is_canonical_root for code in CANONICAL_ACCESS_ZONES.values())


@pytest.mark.parametrize("code", ["0000", "0001", "0100", "0999", "9999"])
def test_access_zone_code_preserves_four_digit_string(code: str) -> None:
    assert str(AccessZoneCode(code)) == code


@pytest.mark.parametrize("code", ["1", "999", "10000", "15A0", "LEGAL", " 0600"])
def test_invalid_access_zone_code_is_rejected(code: str) -> None:
    with pytest.raises(ValueError):
        AccessZoneCode(code)


def test_document_version_must_be_positive_numeric() -> None:
    assert DocumentIdentity(uuid4(), 1).document_version == 1
    with pytest.raises(ValueError):
        DocumentIdentity(uuid4(), 0)
