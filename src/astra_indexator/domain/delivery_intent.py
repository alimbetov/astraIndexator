from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import UUID

from .contracts import AccessZoneCode, AccessZoneIntent, DeliveryIntent, TtlIntent


class DeliveryIntentValidationError(ValueError):
    """Producer boundary payload cannot normalize to one effective delivery intent."""


def normalize_delivery_intent(payload: Mapping[str, Any]) -> DeliveryIntent:
    """Normalize Spring compatibility fields into one immutable producer intent."""

    zone_id = _normalize_selector(
        singular=payload.get("accessZoneId"),
        plural=payload.get("accessZoneIds"),
        field="accessZoneId",
        parser=_uuid,
    )
    zone_code = _normalize_selector(
        singular=payload.get("accessZoneCode"),
        plural=payload.get("accessZoneCodes"),
        field="accessZoneCode",
        parser=_zone_code,
    )
    if zone_id is None and zone_code is None:
        raise DeliveryIntentValidationError("exactly one effective AccessZone is required")

    ttl_raw = payload.get("ttlDays", 0)
    if isinstance(ttl_raw, bool) or not isinstance(ttl_raw, int):
        raise DeliveryIntentValidationError("ttlDays must be an integer")
    try:
        ttl = TtlIntent(ttl_raw)
    except ValueError as exc:
        raise DeliveryIntentValidationError(str(exc)) from exc

    return DeliveryIntent(
        access_zone=AccessZoneIntent(access_zone_id=zone_id, access_zone_code=zone_code),
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


def _uuid(value: Any, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise DeliveryIntentValidationError(f"{field} must contain UUID strings")
    try:
        return UUID(value)
    except ValueError as exc:
        raise DeliveryIntentValidationError(f"{field} contains an invalid UUID") from exc


def _zone_code(value: Any, field: str) -> AccessZoneCode:
    if not isinstance(value, str):
        raise DeliveryIntentValidationError(f"{field} must contain four-digit strings")
    try:
        return AccessZoneCode(value)
    except ValueError as exc:
        raise DeliveryIntentValidationError(str(exc)) from exc
