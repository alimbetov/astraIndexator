from __future__ import annotations

import pytest

from astra_indexator.domain.contracts import (
    AccessZoneCode,
    AccessZoneIntent,
    DeliveryIntent,
    TtlIntent,
)


@pytest.mark.parametrize(
    "code",
    ["0000", "0001", "0010", "0100", "0999", "1500", "9999"],
)
def test_access_zone_preserves_four_digit_code(code: str) -> None:
    zone = AccessZoneCode(code)
    assert str(zone) == code
    assert len(str(zone)) == 4


@pytest.mark.parametrize(
    "code",
    ["0", "001", "10000", "-001", "ABCD", " 100", "100 "],
)
def test_access_zone_rejects_non_wire_codes(code: str) -> None:
    with pytest.raises(ValueError):
        AccessZoneCode(code)


def test_access_zone_intent_accepts_code_only() -> None:
    intent = AccessZoneIntent(access_zone_code=AccessZoneCode("0001"))
    assert str(intent.access_zone_code) == "0001"
    assert set(intent.__dataclass_fields__) == {"access_zone_code"}


def test_zero_ttl_means_inherit_not_forever() -> None:
    ttl = TtlIntent(0)
    assert ttl.inherits_zone_policy is True
    assert ttl.ttl_days == 0


def test_positive_ttl_is_explicit_finite_intent() -> None:
    ttl = TtlIntent(30)
    assert ttl.inherits_zone_policy is False
    assert ttl.ttl_days == 30


def test_negative_ttl_is_rejected() -> None:
    with pytest.raises(ValueError):
        TtlIntent(-1)


def test_delivery_intent_keeps_zone_and_ttl_independent() -> None:
    delivery = DeliveryIntent(
        access_zone=AccessZoneIntent(access_zone_code=AccessZoneCode("0100")),
        ttl=TtlIntent(0),
    )
    assert str(delivery.access_zone.access_zone_code) == "0100"
    assert delivery.ttl.inherits_zone_policy is True
