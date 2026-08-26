from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import sleep
from typing import Callable

from astra_indexator.astravector.contracts import (
    AstraVectorIngestionPort,
    AstraVectorTransportError,
    DeleteDocumentCommand,
    DeleteDocumentResult,
    DocumentVectorStatus,
)

_AMBIGUOUS_MUTATION_CODES = frozenset(
    {
        "DEADLINE_EXCEEDED",
        "UNAVAILABLE",
        "CANCELLED",
        "UNKNOWN",
    }
)

_DELETE_IN_PROGRESS = frozenset({"DELETE_SCHEDULED", "DELETING"})
_DELETE_NOT_CONFIRMED = frozenset(
    {
        "ACCEPTED",
        "INDEXING",
        "VECTORING",
        "PUBLISHING",
        "SYNCING",
        "READY_TO_ACTIVATE",
        "ACTIVE",
    }
)


class ReconciliationClassification(str, Enum):
    CONFIRMED_SUCCEEDED = "CONFIRMED_SUCCEEDED"
    CONFIRMED_NOT_APPLIED = "CONFIRMED_NOT_APPLIED"
    STILL_IN_PROGRESS = "STILL_IN_PROGRESS"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"
    UNKNOWN_RETRY_LATER = "UNKNOWN_RETRY_LATER"
    INTEGRITY_CONFLICT = "INTEGRITY_CONFLICT"


@dataclass(frozen=True, slots=True)
class DeleteReconciliationOutcome:
    classification: ReconciliationClassification
    status: DocumentVectorStatus
    delete_result: DeleteDocumentResult | None = None


class DeleteReconciliationPending(RuntimeError):
    def __init__(
        self,
        *,
        classification: ReconciliationClassification,
        raw_state: str,
    ) -> None:
        super().__init__(
            f"delete reconciliation pending: {classification.value} state={raw_state}"
        )
        self.classification = classification
        self.raw_state = raw_state


class DeleteReconciliationFailed(RuntimeError):
    def __init__(self, *, raw_state: str, message: str = "") -> None:
        super().__init__(f"AstraVector delete failed: {raw_state} {message}".strip())
        self.raw_state = raw_state
        self.message = message


class DeleteReconciliationRunner:
    """Idempotent/reconcilable AstraVector document-version deletion."""

    def __init__(
        self,
        port: AstraVectorIngestionPort,
        *,
        max_delete_attempts: int = 3,
        max_status_polls: int = 10,
        poll_delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_delete_attempts <= 0:
            raise ValueError("max_delete_attempts must be positive")
        if max_status_polls <= 0:
            raise ValueError("max_status_polls must be positive")
        if poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must not be negative")
        self._port = port
        self._max_delete_attempts = max_delete_attempts
        self._max_status_polls = max_status_polls
        self._poll_delay_seconds = poll_delay_seconds
        self._sleeper = sleeper

    def delete(self, command: DeleteDocumentCommand) -> DeleteReconciliationOutcome:
        attempts = 0
        polls = 0
        last_result: DeleteDocumentResult | None = None

        while attempts < self._max_delete_attempts:
            attempts += 1
            try:
                last_result = self._port.delete_document(command)
            except AstraVectorTransportError as exc:
                if exc.code not in _AMBIGUOUS_MUTATION_CODES:
                    raise
                status = self._status(command)
                classification = self._classify(status)
                if classification is ReconciliationClassification.CONFIRMED_SUCCEEDED:
                    return DeleteReconciliationOutcome(classification, status)
                if classification is ReconciliationClassification.CONFIRMED_FAILED:
                    raise DeleteReconciliationFailed(
                        raw_state=status.raw_state,
                        message=status.message,
                    )
                if classification is ReconciliationClassification.STILL_IN_PROGRESS:
                    return self._poll_until_resolved(
                        command,
                        initial=status,
                        delete_result=None,
                        polls_used=polls,
                    )
                if classification is ReconciliationClassification.CONFIRMED_NOT_APPLIED:
                    continue
                raise DeleteReconciliationPending(
                    classification=classification,
                    raw_state=status.raw_state,
                ) from exc

            raw = self._normalize(last_result.raw_operation_state)
            if raw == "DELETED":
                status = self._status(command)
                if self._classify(status) is ReconciliationClassification.CONFIRMED_SUCCEEDED:
                    return DeleteReconciliationOutcome(
                        ReconciliationClassification.CONFIRMED_SUCCEEDED,
                        status,
                        last_result,
                    )
            if raw in _DELETE_IN_PROGRESS:
                status = self._status(command)
                return self._poll_until_resolved(
                    command,
                    initial=status,
                    delete_result=last_result,
                    polls_used=polls,
                )
            if raw == "FAILED":
                raise DeleteReconciliationFailed(
                    raw_state=raw,
                    message=last_result.message,
                )

            status = self._status(command)
            classification = self._classify(status)
            if classification is ReconciliationClassification.CONFIRMED_SUCCEEDED:
                return DeleteReconciliationOutcome(classification, status, last_result)
            if classification is ReconciliationClassification.STILL_IN_PROGRESS:
                return self._poll_until_resolved(
                    command,
                    initial=status,
                    delete_result=last_result,
                    polls_used=polls,
                )
            if classification is ReconciliationClassification.CONFIRMED_FAILED:
                raise DeleteReconciliationFailed(
                    raw_state=status.raw_state,
                    message=status.message,
                )
            if classification is ReconciliationClassification.CONFIRMED_NOT_APPLIED:
                continue
            raise DeleteReconciliationPending(
                classification=classification,
                raw_state=status.raw_state,
            )

        status = self._status(command)
        classification = self._classify(status)
        if classification is ReconciliationClassification.CONFIRMED_SUCCEEDED:
            return DeleteReconciliationOutcome(classification, status, last_result)
        raise DeleteReconciliationPending(
            classification=classification,
            raw_state=status.raw_state,
        )

    def _poll_until_resolved(
        self,
        command: DeleteDocumentCommand,
        *,
        initial: DocumentVectorStatus,
        delete_result: DeleteDocumentResult | None,
        polls_used: int,
    ) -> DeleteReconciliationOutcome:
        status = initial
        polls = polls_used
        while True:
            classification = self._classify(status)
            if classification is ReconciliationClassification.CONFIRMED_SUCCEEDED:
                return DeleteReconciliationOutcome(
                    classification,
                    status,
                    delete_result,
                )
            if classification is ReconciliationClassification.CONFIRMED_FAILED:
                raise DeleteReconciliationFailed(
                    raw_state=status.raw_state,
                    message=status.message,
                )
            if classification is ReconciliationClassification.CONFIRMED_NOT_APPLIED:
                raise DeleteReconciliationPending(
                    classification=classification,
                    raw_state=status.raw_state,
                )
            if classification is not ReconciliationClassification.STILL_IN_PROGRESS:
                raise DeleteReconciliationPending(
                    classification=classification,
                    raw_state=status.raw_state,
                )
            if polls >= self._max_status_polls:
                raise DeleteReconciliationPending(
                    classification=classification,
                    raw_state=status.raw_state,
                )
            polls += 1
            if self._poll_delay_seconds:
                self._sleeper(self._poll_delay_seconds)
            status = self._status(command)

    def _status(self, command: DeleteDocumentCommand) -> DocumentVectorStatus:
        return self._port.get_document_vector_status(
            access_zone_id=command.access_zone_id,
            document_id=command.document_id,
            document_version=command.document_version,
        )

    @classmethod
    def _classify(
        cls,
        status: DocumentVectorStatus,
    ) -> ReconciliationClassification:
        raw = cls._normalize(status.raw_state)
        if raw == "DELETED":
            if status.searchable:
                return ReconciliationClassification.INTEGRITY_CONFLICT
            return ReconciliationClassification.CONFIRMED_SUCCEEDED
        if raw in _DELETE_IN_PROGRESS:
            return ReconciliationClassification.STILL_IN_PROGRESS
        if raw in _DELETE_NOT_CONFIRMED:
            return ReconciliationClassification.CONFIRMED_NOT_APPLIED
        if raw in {"FAILED", "EXPIRED"}:
            return ReconciliationClassification.CONFIRMED_FAILED
        return ReconciliationClassification.UNKNOWN_RETRY_LATER

    @staticmethod
    def _normalize(raw_state: str) -> str:
        normalized = raw_state.strip().upper()
        prefix = "OPERATION_STATE_"
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
        return normalized
