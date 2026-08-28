from __future__ import annotations

import pytest

from astra_indexator.domain.delivery_intent import (
    DeliveryIntentValidationError,
    normalize_delivery_intent,
)


def test_code_singular_preserves_leading_zeroes_and_default_ttl() -> None:
    intent = normalize_delivery_intent({"accessZoneCode": "0001"})
    assert str(intent.access_zone.access_zone_code) == "0001"
    assert intent.ttl.ttl_days == 0
    assert intent.ttl.inherits_zone_policy is True


def test_plural_single_code_normalizes_to_one_selector() -> None:
    intent = normalize_delivery_intent({"accessZoneCodes": ["0100"]})
    assert str(intent.access_zone.access_zone_code) == "0100"


def test_duplicate_plural_values_are_one_effective_selector() -> None:
    intent = normalize_delivery_intent({"accessZoneCodes": ["0600", "0600"]})
    assert str(intent.access_zone.access_zone_code) == "0600"


def test_matching_singular_and_plural_are_correlation_compatible() -> None:
    intent = normalize_delivery_intent(
        {"accessZoneCode": "0600", "accessZoneCodes": ["0600"], "ttlDays": 30}
    )
    assert str(intent.access_zone.access_zone_code) == "0600"
    assert intent.ttl.ttl_days == 30


@pytest.mark.parametrize(
    "payload",
    [
        {"accessZoneId": "11111111-1111-1111-1111-111111111111", "accessZoneCode": "1500"},
        {"accessZoneIds": ["11111111-1111-1111-1111-111111111111"]},
    ],
)
def test_uuid_access_zone_selectors_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(DeliveryIntentValidationError, match="accessZoneId/accessZoneIds"):
        normalize_delivery_intent(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"accessZoneCodes": []},
        {"accessZoneCode": "0100", "accessZoneCodes": ["0200"]},
        {"accessZoneCodes": ["0100", "0200"]},
    ],
)
def test_missing_or_ambiguous_zone_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(DeliveryIntentValidationError):
        normalize_delivery_intent(payload)


@pytest.mark.parametrize("ttl", [-1, True, 1.5, "30"])
def test_invalid_ttl_is_rejected(ttl: object) -> None:
    with pytest.raises(DeliveryIntentValidationError):
        normalize_delivery_intent({"accessZoneCode": "0001", "ttlDays": ttl})
