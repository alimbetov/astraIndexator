from __future__ import annotations

from enum import Enum

from .contracts import AstraVectorTransportError


class AbortFailureDisposition(str, Enum):
    RECONCILE_STATUS = "RECONCILE_STATUS"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


_RECONCILE_CODES = frozenset({"DEADLINE_EXCEEDED", "UNAVAILABLE", "ABORTED"})

_TERMINAL_PRECONDITION_MARKERS = (
    "INGESTION_SESSION_FINALIZING",
    "INGESTION_SESSION_COMPLETED",
    "INGESTION_SESSION_FAILED",
    "INGESTION_SESSION_EXPIRED",
)


def classify_abort_failure(error: AstraVectorTransportError) -> AbortFailureDisposition:
    """Classify AbortLogicalDocumentIngestion failures without executing recovery.

    Abort is a mutating RPC. A transport timeout/unavailability or a concurrent state-change
    `ABORTED` response cannot prove whether the remote mutation committed, so callers must inspect
    the existing ingestion session before deciding whether any further action is safe.

    Known terminal FAILED_PRECONDITION states and all unknown failures are fail-closed.
    """

    code = error.code.strip().upper()
    message = error.message.strip().upper()

    if code in _RECONCILE_CODES:
        return AbortFailureDisposition.RECONCILE_STATUS

    if code == "FAILED_PRECONDITION":
        if any(marker in message for marker in _TERMINAL_PRECONDITION_MARKERS):
            return AbortFailureDisposition.PERMANENT_FAILURE
        return AbortFailureDisposition.PERMANENT_FAILURE

    return AbortFailureDisposition.PERMANENT_FAILURE
