from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import DocumentVectorStatus, IngestionSessionState, IngestionStatus


class RetryDecision(str, Enum):
    RETRY_SAME_OPERATION = "RETRY_SAME_OPERATION"
    RECONCILE_STATUS = "RECONCILE_STATUS"
    BACKOFF_AND_RETRY = "BACKOFF_AND_RETRY"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


class ActivationReadinessPolicy(str, Enum):
    REQUIRE_SEARCHABLE = "REQUIRE_SEARCHABLE"
    ALLOW_READY_TO_ACTIVATE = "ALLOW_READY_TO_ACTIVATE"


class VectorReadinessDisposition(str, Enum):
    WAIT = "WAIT"
    READY_TO_ACTIVATE = "READY_TO_ACTIVATE"
    SEARCHABLE = "SEARCHABLE"
    TERMINAL = "TERMINAL"


class VectorReadinessIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GrpcFailure:
    code: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class VectorReadinessDecision:
    disposition: VectorReadinessDisposition
    completion_level: str
    reason: str


_TRANSIENT_RESOURCE_EXHAUSTED_MARKERS = (
    "MAX_CONCURRENT_INGESTION_SESSIONS EXCEEDED",
    "MAX_SESSIONS_PER_ACCESS_ZONE EXCEEDED",
    "MAX_SESSIONS_PER_DOCUMENT EXCEEDED",
)

_RECONCILABLE_PRECONDITION_MARKERS = (
    "INGESTION_SESSION_FINALIZING",
    "INGESTION_SESSION_COMPLETED",
    "INGESTION_SESSION_ABORTED",
)


def classify_grpc_failure(failure: GrpcFailure) -> RetryDecision:
    """Classify one AstraVector ingestion RPC failure without executing recovery.

    The classifier is deliberately fail-closed. A retry/reconciliation decision is emitted only
    for transport codes or server markers verified against the pinned AstraVector ingestion
    implementation. Unknown codes and unknown FAILED_PRECONDITION/RESOURCE_EXHAUSTED messages are
    permanent until the wire contract is reviewed and re-qualified.
    """

    code = failure.code.strip().upper()
    message = failure.message.strip().upper()

    if code == "UNAVAILABLE":
        return RetryDecision.BACKOFF_AND_RETRY
    if code in {"DEADLINE_EXCEEDED", "ABORTED"}:
        return RetryDecision.RECONCILE_STATUS
    if code == "RESOURCE_EXHAUSTED":
        if any(marker in message for marker in _TRANSIENT_RESOURCE_EXHAUSTED_MARKERS):
            return RetryDecision.BACKOFF_AND_RETRY
        return RetryDecision.PERMANENT_FAILURE
    if code == "FAILED_PRECONDITION":
        if "HASH_MISMATCH" in message:
            return RetryDecision.PERMANENT_FAILURE
        if any(marker in message for marker in _RECONCILABLE_PRECONDITION_MARKERS):
            return RetryDecision.RECONCILE_STATUS
        return RetryDecision.PERMANENT_FAILURE
    if code in {
        "INVALID_ARGUMENT",
        "OUT_OF_RANGE",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "DATA_LOSS",
    }:
        return RetryDecision.PERMANENT_FAILURE
    return RetryDecision.PERMANENT_FAILURE


def should_retry_finalize(status: IngestionStatus) -> bool:
    return status.state == IngestionSessionState.ACTIVE


def vector_delivery_complete(status: DocumentVectorStatus) -> bool:
    return status.searchable


def evaluate_vector_readiness(
    status: DocumentVectorStatus,
    *,
    policy: ActivationReadinessPolicy = ActivationReadinessPolicy.REQUIRE_SEARCHABLE,
) -> VectorReadinessDecision:
    """Classify AstraVector post-finalize readiness from public status evidence.

    AstraIndexator treats the public ingestion-facade status as authoritative. READY_TO_ACTIVATE
    proves vector synchronization, but production completion still requires ACTIVE/searchable.
    """

    state = _normalize_operation_state(status.raw_state)
    _assert_non_negative_sync_counters(status)

    transitional = {"ACCEPTED", "INDEXING", "VECTORING", "PUBLISHING", "SYNCING"}
    terminal = {"FAILED", "EXPIRED", "DELETED", "DELETE_SCHEDULED", "DELETING"}

    if state in transitional:
        if status.searchable or status.ready_to_activate:
            raise VectorReadinessIntegrityError(
                f"transitional vector state {state} cannot be searchable/ready_to_activate"
            )
        return VectorReadinessDecision(
            disposition=VectorReadinessDisposition.WAIT,
            completion_level="FINALIZED",
            reason=f"AstraVector is still {state.lower()}",
        )

    if state == "READY_TO_ACTIVATE":
        if not status.ready_to_activate:
            raise VectorReadinessIntegrityError(
                "READY_TO_ACTIVATE state must set ready_to_activate=true"
            )
        _assert_ready_sync_consistency(status)
        if status.searchable and status.document_status.strip().upper() == "ACTIVE":
            return VectorReadinessDecision(
                disposition=VectorReadinessDisposition.SEARCHABLE,
                completion_level="SEARCHABLE",
                reason="document version is active and searchable",
            )
        if policy is ActivationReadinessPolicy.ALLOW_READY_TO_ACTIVATE:
            return VectorReadinessDecision(
                disposition=VectorReadinessDisposition.READY_TO_ACTIVATE,
                completion_level="VECTOR_READY",
                reason="vector synchronization is complete; activation ownership is external",
            )
        return VectorReadinessDecision(
            disposition=VectorReadinessDisposition.WAIT,
            completion_level="VECTOR_READY",
            reason="waiting for activation to make the document searchable",
        )

    if state == "ACTIVE":
        if not status.searchable:
            raise VectorReadinessIntegrityError("ACTIVE vector state must be searchable")
        _assert_ready_sync_consistency(status)
        return VectorReadinessDecision(
            disposition=VectorReadinessDisposition.SEARCHABLE,
            completion_level="SEARCHABLE",
            reason="document version is active and searchable",
        )

    if state in terminal:
        if status.searchable or status.ready_to_activate:
            raise VectorReadinessIntegrityError(
                f"terminal vector state {state} cannot be searchable/ready_to_activate"
            )
        return VectorReadinessDecision(
            disposition=VectorReadinessDisposition.TERMINAL,
            completion_level="FAILED",
            reason=status.message or f"AstraVector vector state is {state}",
        )

    raise VectorReadinessIntegrityError(
        f"unsupported AstraVector operation state {status.raw_state!r}"
    )


def _normalize_operation_state(raw_state: str) -> str:
    normalized = raw_state.strip().upper()
    prefix = "OPERATION_STATE_"
    if normalized.startswith(prefix):
        normalized = normalized[len(prefix) :]
    return normalized


def _assert_non_negative_sync_counters(status: DocumentVectorStatus) -> None:
    counters = (
        status.expected_bindings,
        status.synced_bindings,
        status.pending_bindings,
        status.failed_bindings,
        status.outbox_pending,
        status.outbox_retry_pending,
        status.outbox_failed,
        status.qdrant_points_expected,
        status.qdrant_points_found,
        status.qdrant_points_missing,
        status.qdrant_points_extra,
    )
    if min(counters) < 0:
        raise VectorReadinessIntegrityError("AstraVector readiness counters must be non-negative")


def _assert_ready_sync_consistency(status: DocumentVectorStatus) -> None:
    if status.failed_bindings or status.outbox_failed or status.qdrant_points_missing:
        raise VectorReadinessIntegrityError(
            "ready/searchable state conflicts with failed bindings/outbox or missing Qdrant points"
        )
    if status.expected_bindings and status.synced_bindings != status.expected_bindings:
        raise VectorReadinessIntegrityError(
            "ready/searchable state requires synced_bindings == expected_bindings"
        )
    if status.pending_bindings or status.outbox_pending or status.outbox_retry_pending:
        raise VectorReadinessIntegrityError(
            "ready/searchable state conflicts with pending bindings/outbox work"
        )
    if status.qdrant_points_expected and status.qdrant_points_found < status.qdrant_points_expected:
        raise VectorReadinessIntegrityError(
            "ready/searchable state requires all expected Qdrant points to be present"
        )
