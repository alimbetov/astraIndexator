from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .contracts import LogicalBlock, SourceLink


class LogicalBlockValidationError(ValueError):
    """LogicalBlock graph violates the public AstraVector application contract."""


class SourceLinkSecurityError(ValueError):
    """SourceLink contains credential-bearing material that must not cross the wire boundary."""


_SENSITIVE_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
    "x_api_key",
    "x_amz_credential",
    "x_amz_security_token",
    "x_amz_signature",
}


def validate_logical_blocks(blocks: Sequence[LogicalBlock]) -> tuple[LogicalBlock, ...]:
    """Validate one connected, deterministic LogicalBlock tree."""

    materialized = tuple(blocks)
    if not materialized:
        raise LogicalBlockValidationError("logical document must contain at least one block")

    by_id: dict[str, LogicalBlock] = {}
    order_indexes: set[int] = set()
    document_roots: list[LogicalBlock] = []

    for block in materialized:
        block_id = block.block_id.strip()
        if not block_id:
            raise LogicalBlockValidationError("logical block_id must not be blank")
        if block_id in by_id:
            raise LogicalBlockValidationError(f"duplicate logical block_id: {block_id!r}")
        by_id[block_id] = block

        if block.order_index < 0:
            raise LogicalBlockValidationError(
                f"logical block {block_id!r} order_index must be non-negative"
            )
        if block.order_index in order_indexes:
            raise LogicalBlockValidationError(
                f"duplicate logical block order_index: {block.order_index}"
            )
        order_indexes.add(block.order_index)

        if block.block_type.strip().upper() == "DOCUMENT":
            document_roots.append(block)

    if len(document_roots) != 1:
        raise LogicalBlockValidationError(
            f"logical document must contain exactly one DOCUMENT block; found {len(document_roots)}"
        )

    root = document_roots[0]
    root_id = root.block_id.strip()
    if root.parent_block_id.strip():
        raise LogicalBlockValidationError("DOCUMENT root parent_block_id must be blank")

    for block_id, block in by_id.items():
        if block_id == root_id:
            continue
        parent_id = block.parent_block_id.strip()
        if not parent_id:
            raise LogicalBlockValidationError(
                f"non-root logical block {block_id!r} must reference a parent"
            )
        if parent_id == block_id:
            raise LogicalBlockValidationError(f"logical block {block_id!r} cannot parent itself")
        if parent_id not in by_id:
            raise LogicalBlockValidationError(
                f"logical block {block_id!r} references missing parent {parent_id!r}"
            )

    # Follow every parent chain to the single root. A repeated node proves a cycle; reaching a
    # blank parent before the root proves a disconnected second root-like component.
    for start_id in by_id:
        if start_id == root_id:
            continue
        seen: set[str] = set()
        current_id = start_id
        while current_id != root_id:
            if current_id in seen:
                raise LogicalBlockValidationError(
                    f"logical block hierarchy contains a cycle involving {current_id!r}"
                )
            seen.add(current_id)
            current = by_id[current_id]
            parent_id = current.parent_block_id.strip()
            if not parent_id:
                raise LogicalBlockValidationError(
                    f"logical block {start_id!r} is not connected to DOCUMENT root {root_id!r}"
                )
            current_id = parent_id

    return materialized


def validate_source_link(link: SourceLink) -> None:
    """Reject credential-bearing SourceLink material without echoing secret values."""

    parsed = urlsplit(link.url.strip())
    if parsed.username is not None or parsed.password is not None:
        raise SourceLinkSecurityError("source link URL must not contain userinfo credentials")

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _sensitive_name(key) or _looks_like_bearer(value):
            raise SourceLinkSecurityError(
                f"source link URL contains prohibited credential query field {key!r}"
            )

    _validate_attributes(link.attributes)


def _validate_attributes(attributes: Mapping[str, str]) -> None:
    for key, value in attributes.items():
        if _sensitive_name(key) or _looks_like_bearer(value):
            raise SourceLinkSecurityError(
                f"source link attributes contain prohibited credential field {key!r}"
            )


def _sensitive_name(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_").replace(".", "_")
    return (
        normalized in _SENSITIVE_NAMES
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_api_key")
    )


def _looks_like_bearer(value: str) -> bool:
    return value.lstrip().lower().startswith("bearer ")
