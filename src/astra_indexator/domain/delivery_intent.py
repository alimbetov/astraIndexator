from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .contracts import AccessZoneCode, AccessZoneIntent, DeliveryIntent, TtlIntent


class DeliveryIntentValidationError(ValueError):
    """Producer boundary payload cannot normalize to one effective delivery intent."""


def normalize_delivery_intent(payload: Mapping[str, Any]) -> DeliveryIntent:
    """Normalize producer input into immutable code-only AccessZone/TTL intent.

    AstraIndexator accepts only ``accessZoneCode`` / ``accessZoneCodes`` at its producer
    boundary. AstraVector UUID identities are downstream-private implementation details and
    therefore ``accessZoneId`` / ``accessZoneIds`` are rejected rather than ignored.
    """

    if payload.get("accessZoneId") is not None or payload.get("accessZoneIds") is not None:
        raise DeliveryIntentValidationError(
            "accessZoneId/accessZoneIds are not supported; use accessZoneCode/accessZoneCodes"
        )

    zone_code = _normalize_selector(
        singular=payload.get("accessZoneCode"),
        plural=payload.get("accessZoneCodes"),
        field="accessZoneCode",
        parser=_zone_code,
    )
    if zone_code is None:
        raise DeliveryIntentValidationError("exactly one effective accessZoneCode is required")

    ttl_raw = payload.get("ttlDays", 0)
    if isinstance(ttl_raw, bool) or not isinstance(ttl_raw, int):
        raise DeliveryIntentValidationError("ttlDays must be an integer")
    try:
        ttl = TtlIntent(ttl_raw)
    except ValueError as exc:
        raise DeliveryIntentValidationError(str(exc)) from exc

    return DeliveryIntent(
        access_zone=AccessZoneIntent(access_zone_code=zone_code),
        ttl=ttl,
    )


def _normalize_selector(
    *,
    singular: Any,
    plural: Any,
    field: str,
    parser: Callable[[Any, str], Any],
) -> Any:
    single = None if singular is None else parser(singular, field)
    values: list[Any] = []
    if plural is not None:
        if isinstance(plural, (str, bytes)) or not isinstance(plural, Sequence):
            raise DeliveryIntentValidationError(f"{field}s must be an array")
        values = [parser(value, f"{field}s") for value in plural]

    distinct = list(dict.fromkeys(values))
    if len(distinct) > 1:
        raise DeliveryIntentValidationError(f"multiple distinct {field}s are not supported")
    plural_value = distinct[0] if distinct else None
    if single is not None and plural_value is not None and single != plural_value:
        raise DeliveryIntentValidationError(f"conflicting {field} and {field}s")
    return single if single is not None else plural_value


def _zone_code(value: Any, field: str) -> AccessZoneCode:
    if not isinstance(value, str):
        raise DeliveryIntentValidationError(f"{field} must contain four-digit strings")
    try:
        return AccessZoneCode(value)
    except ValueError as exc:
        raise DeliveryIntentValidationError(str(exc)) from exc
