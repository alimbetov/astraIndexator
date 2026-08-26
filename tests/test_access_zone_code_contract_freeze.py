from __future__ import annotations

from uuid import uuid4

import pytest

from astra_indexator.domain.contracts import AccessZoneCode
from astra_indexator.persistence.repository import NewIndexationJob


def _job(**overrides: object) -> NewIndexationJob:
    values: dict[str, object] = {
        "producer_request_id": uuid4(),
        "document_id": uuid4(),
        "document_version": 1,
        "source_uri": "seaweed://docs/frozen.txt",
        "access_zone_code": "0001",
    }
    values.update(overrides)
    return NewIndexationJob(**values)  # type: ignore[arg-type]


def test_access_zone_code_is_four_digit_string_and_preserves_leading_zeroes() -> None:
    code = AccessZoneCode("0001")
    assert code.value == "0001"
    assert str(code) == "0001"


@pytest.mark.parametrize("value", ["1", "001", "10000", "06A0", " 0600"])
def test_access_zone_code_rejects_noncanonical_lexical_forms(value: str) -> None:
    with pytest.raises(ValueError):
        AccessZoneCode(value)


def test_new_job_rejects_invalid_code_before_postgres() -> None:
    with pytest.raises(ValueError, match="access_zone_code must match"):
        _job(access_zone_code="600")


def test_new_job_rejects_legacy_requested_code_mismatch() -> None:
    with pytest.raises(ValueError, match="must be identical"):
        _job(access_zone_code="0001", requested_access_zone_code="0600")


def test_new_job_keeps_matching_requested_code_byte_exact() -> None:
    job = _job(access_zone_code="0001", requested_access_zone_code="0001")
    assert job.access_zone_code == "0001"
    assert job.requested_access_zone_code == "0001"
