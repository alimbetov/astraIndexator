from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import sleep
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.astravector.contracts import (
    AstraVectorIngestionPort,
    DocumentVectorStatus,
    FinalizeIngestionCommand,
    FinalizeIngestionResult,
    IngestionSessionState,
    IngestionStatus,
)
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcError
from astra_indexator.persistence.delivery import DeliveryBatchRepository, DeliveryIntegrityError


_AMBIGUOUS_FINALIZE_CODES = frozenset(
    {
        "DEADLINE_EXCEEDED",
        "UNAVAILABLE",
        "CANCELLED",
        "UNKNOWN",
    }
)


class FinalizeResolution(str, Enum):
    DIRECT_ACK = "DIRECT_ACK"
    RECONCILED_COMPLETED = "RECONCILED_COMPLETED"


@dataclass(frozen=True, slots=True)
class FinalizeDeliveryOutcome:
    resolution: FinalizeResolution
    finalize_result: FinalizeIngestionResult | None
    reconciled_status: IngestionStatus | None
    vector_status: DocumentVectorStatus


class FinalizeTerminalError(RuntimeError):
    def __init__(self, status: IngestionStatus) -> None:
        detail = status.error_code or status.state.value
        message = status.error_message or status.raw_status
        super().__init__(f"AstraVector ingestion terminal state {detail}: {message}")
        self.status = status


class FinalizeReconciliationPending(RuntimeError):
    def __init__(self, *, state: IngestionSessionState, polls: int) -> None:
        super().__init__(
            f"Finalize reconciliation remains {state.value} after {polls} status observations"
        )
        self.state = state
        self.polls = polls


class FinalizeReconciliationRunner:
    """Resolve ambiguous Finalize outcomes without creating a new document version.

    A timeout-like Finalize failure is never blindly replayed. The existing ingestion session is
    queried first. ACTIVE permits retrying the exact same Finalize command; FINALIZING only polls;
    COMPLETED transitions to vector-status inspection; terminal states fail explicitly.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
        max_finalize_attempts: int = 3,
        max_status_polls: int = 10,
        poll_delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_finalize_attempts <= 0:
            raise ValueError("max_finalize_attempts must be positive")
        if max_status_polls <= 0:
            raise ValueError("max_status_polls must be positive")
        if poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()
        self._max_finalize_attempts = max_finalize_attempts
        self._max_status_polls = max_status_polls
        self._poll_delay_seconds = poll_delay_seconds
        self._sleeper = sleeper

    def finalize(
        self,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        final_content_hash: str,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> FinalizeDeliveryOutcome:
        command = FinalizeIngestionCommand(
            ingestion_session_id=ingestion_session_id,
            final_content_hash=final_content_hash,
        )
        self._assert_checkpoint_session(job_id, ingestion_session_id)

        finalize_attempts = 0
        status_polls = 0
        last_status: IngestionStatus | None = None

        while True:
            if last_status is None or last_status.state is IngestionSessionState.ACTIVE:
                if finalize_attempts >= self._max_finalize_attempts:
                    raise FinalizeReconciliationPending(
                        state=IngestionSessionState.ACTIVE,
                        polls=status_polls,
                    )
                finalize_attempts += 1
                try:
                    result = self._port.finalize(command)
                except AstraVectorGrpcError as exc:
                    if exc.code not in _AMBIGUOUS_FINALIZE_CODES:
                        raise
                    last_status = self._observe_status(job_id, ingestion_session_id)
                    status_polls += 1
                    continue

                self._validate_finalize_identity(
                    result,
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
                if result.raw_operation_state.endswith("FAILED"):
                    raise RuntimeError(
                        "FinalizeLogicalDocumentIngestion returned failed operation state: "
                        f"{result.raw_operation_state}: {result.message}"
                    )
                vector_status = self._port.get_document_vector_status(
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
                return FinalizeDeliveryOutcome(
                    resolution=FinalizeResolution.DIRECT_ACK,
                    finalize_result=result,
                    reconciled_status=None,
                    vector_status=vector_status,
                )

            if last_status.state is IngestionSessionState.FINALIZING:
                if status_polls >= self._max_status_polls:
                    raise FinalizeReconciliationPending(
                        state=IngestionSessionState.FINALIZING,
                        polls=status_polls,
                    )
                if self._poll_delay_seconds:
                    self._sleeper(self._poll_delay_seconds)
                last_status = self._observe_status(job_id, ingestion_session_id)
                status_polls += 1
                continue

            if last_status.state is IngestionSessionState.COMPLETED:
                vector_status = self._port.get_document_vector_status(
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
                return FinalizeDeliveryOutcome(
                    resolution=FinalizeResolution.RECONCILED_COMPLETED,
                    finalize_result=None,
                    reconciled_status=last_status,
                    vector_status=vector_status,
                )

            if last_status.state in {
                IngestionSessionState.FAILED,
                IngestionSessionState.ABORTED,
                IngestionSessionState.EXPIRED,
            }:
                raise FinalizeTerminalError(last_status)

            raise FinalizeReconciliationPending(
                state=last_status.state,
                polls=status_polls,
            )

    def _observe_status(self, job_id: UUID, ingestion_session_id: UUID) -> IngestionStatus:
        status = self._port.get_ingestion_status(ingestion_session_id)
        if status.ingestion_session_id != ingestion_session_id:
            raise DeliveryIntegrityError(
                "GetIngestionStatus returned a different ingestion session during reconciliation"
            )
        with self._session_factory() as session:
            with session.begin():
                self._repository.record_session_status(
                    session,
                    job_id=job_id,
                    ingestion_session_id=ingestion_session_id,
                    session_status_raw=status.raw_status,
                    error_code=status.error_code,
                    error_message=status.error_message,
                )
        return status

    def _assert_checkpoint_session(self, job_id: UUID, ingestion_session_id: UUID) -> None:
        with self._session_factory() as session:
            checkpoint = self._repository.checkpoint(session, job_id)
            if checkpoint is None:
                raise DeliveryIntegrityError("delivery checkpoint does not exist for finalize")
            if checkpoint.ingestion_session_id != ingestion_session_id:
                raise DeliveryIntegrityError(
                    "Finalize attempted for a different AstraVector ingestion session"
                )

    @staticmethod
    def _validate_finalize_identity(
        result: FinalizeIngestionResult,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> None:
        if result.access_zone_id != access_zone_id:
            raise DeliveryIntegrityError("Finalize acknowledged a different access_zone_id")
        if result.document_id != document_id:
            raise DeliveryIntegrityError("Finalize acknowledged a different document_id")
        if result.document_version != document_version:
            raise DeliveryIntegrityError("Finalize acknowledged a different document_version")
