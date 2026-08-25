from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import DocumentVectorStatus, IngestionSessionState, IngestionStatus


class RetryDecision(str, Enum):
    RETRY_SAME_OPERATION = "RETRY_SAME_OPERATION"
    RECONCILE_STATUS = "RECONCILE_STATUS"
    BACKOFF_AND_RETRY = "BACKOFF_AND_RETRY"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True, slots=True)
class GrpcFailure:
    code: str
    message: str = ""


def classify_grpc_failure(failure: GrpcFailure) -> RetryDecision:
    code = failure.code.strip().upper()
    message = failure.message.strip().upper()

    if code == "UNAVAILABLE":
        return RetryDecision.BACKOFF_AND_RETRY
    if code == "DEADLINE_EXCEEDED":
        return RetryDecision.RECONCILE_STATUS
    if code in {"INVALID_ARGUMENT", "OUT_OF_RANGE", "PERMISSION_DENIED", "UNAUTHENTICATED"}:
        return RetryDecision.PERMANENT_FAILURE
    if code == "ABORTED":
        return RetryDecision.RECONCILE_STATUS
    if code == "FAILED_PRECONDITION":
        return (
            RetryDecision.PERMANENT_FAILURE
            if "HASH_MISMATCH" in message
            else RetryDecision.RECONCILE_STATUS
        )
    if code == "RESOURCE_EXHAUSTED":
        size_markers = ("MAX_BLOCKS", "MAX_BATCH", "SIZE", "BYTES")
        if any(marker in message for marker in size_markers):
            return RetryDecision.PERMANENT_FAILURE
        return RetryDecision.BACKOFF_AND_RETRY
    if code == "NOT_FOUND":
        return RetryDecision.RECONCILE_STATUS
    return RetryDecision.RECONCILE_STATUS


def should_retry_finalize(status: IngestionStatus) -> bool:
    return status.state == IngestionSessionState.ACTIVE


def vector_delivery_complete(status: DocumentVectorStatus) -> bool:
    return status.searchable
