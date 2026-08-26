from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class DocumentLifecycleState(str, Enum):
    BUILDING = "BUILDING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class LifecycleOperationType(str, Enum):
    REINDEX = "REINDEX"
    CANCEL = "CANCEL"
    DELETE = "DELETE"
    RECONCILE = "RECONCILE"


class LifecycleOperationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_ALLOWED_TRANSITIONS: dict[DocumentLifecycleState, frozenset[DocumentLifecycleState]] = {
    DocumentLifecycleState.BUILDING: frozenset(
        {
            DocumentLifecycleState.READY,
            DocumentLifecycleState.CANCEL_PENDING,
            DocumentLifecycleState.FAILED,
        }
    ),
    DocumentLifecycleState.READY: frozenset(
        {
            DocumentLifecycleState.ACTIVE,
            DocumentLifecycleState.CANCEL_PENDING,
            DocumentLifecycleState.DELETE_PENDING,
            DocumentLifecycleState.FAILED,
        }
    ),
    DocumentLifecycleState.ACTIVE: frozenset(
        {
            DocumentLifecycleState.SUPERSEDED,
            DocumentLifecycleState.DELETE_PENDING,
        }
    ),
    DocumentLifecycleState.SUPERSEDED: frozenset(
        {DocumentLifecycleState.DELETE_PENDING}
    ),
    DocumentLifecycleState.CANCEL_PENDING: frozenset(
        {
            DocumentLifecycleState.CANCELLED,
            DocumentLifecycleState.DELETE_PENDING,
            DocumentLifecycleState.FAILED,
        }
    ),
    DocumentLifecycleState.DELETE_PENDING: frozenset(
        {
            DocumentLifecycleState.DELETED,
            DocumentLifecycleState.FAILED,
        }
    ),
    DocumentLifecycleState.CANCELLED: frozenset(),
    DocumentLifecycleState.DELETED: frozenset(),
    DocumentLifecycleState.FAILED: frozenset(
        {DocumentLifecycleState.DELETE_PENDING}
    ),
}


class InvalidLifecycleTransition(ValueError):
    pass


def require_lifecycle_transition(
    current: DocumentLifecycleState,
    target: DocumentLifecycleState,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidLifecycleTransition(
            f"invalid lifecycle transition: {current.value} -> {target.value}"
        )


@dataclass(frozen=True, slots=True)
class DocumentVersionIdentity:
    document_id: UUID
    document_version: int

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("document_version must be positive")


@dataclass(frozen=True, slots=True)
class LifecycleTarget:
    identity: DocumentVersionIdentity
    requested_access_zone_code: str | None
    requested_access_zone_id: UUID | None

    def __post_init__(self) -> None:
        if self.requested_access_zone_code is None and self.requested_access_zone_id is None:
            raise ValueError("one access-zone selector is required")
        if self.requested_access_zone_code is not None:
            code = self.requested_access_zone_code
            if len(code) != 4 or not code.isascii() or not code.isdigit():
                raise ValueError("requested_access_zone_code must match ^[0-9]{4}$")
