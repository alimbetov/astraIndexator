from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import sleep
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.astravector.contracts import (
    AbortIngestionCommand,
    AstraVectorIngestionPort,
    AstraVectorTransportError,
    IngestionSessionState,
    IngestionStatus,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository, DeliveryIntegrityError

_AMBIGUOUS_ABORT_CODES = frozenset(
    {
        "DEADLINE_EXCEEDED",
        "UNAVAILABLE",
        "CANCELLED",
        "UNKNOWN",
    }
)


class AbortResolution(str, Enum):
    DIRECT_ACK = "DIRECT_ACK"
    RECONCILED_ABORTED = "RECONCILED_ABORTED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


@dataclass(frozen=True, slots=True)
class AbortDeliveryOutcome:
    resolution: AbortResolution
    status: IngestionStatus


class AbortConflictError(RuntimeError):
    def __init__(self, status: IngestionStatus) -> None:
        super().__init__(
            "Abort cannot override an ingestion that already completed successfully: "
            f"{status.raw_status}"
        )
        self.status = status


class AbortReconciliationPending(RuntimeError):
    def __init__(self, *, state: IngestionSessionState, observations: int) -> None:
        super().__init__(
            f"Abort reconciliation remains {state.value} after {observations} status observations"
        )
        self.state = state
        self.observations = observations


class AbortReconciliationRunner:
    """Abort one existing AstraVector ingestion session without creating replacement work.

    Ambiguous transport failures are reconciled with GetLogicalDocumentIngestionStatus before any
    retry. ACTIVE permits replaying the exact same Abort command. ABORTED is success. FAILED and
    EXPIRED are already-terminal recovery outcomes. FINALIZING is observed only: Abort is not
    replayed while finalize ownership is ambiguous. COMPLETED is a conflict because successful
    finalization won the race and must never be converted into a fresh version implicitly.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
        max_abort_attempts: int = 3,
        max_status_polls: int = 10,
        poll_delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_abort_attempts <= 0:
            raise ValueError("max_abort_attempts must be positive")
        if max_status_polls <= 0:
            raise ValueError("max_status_polls must be positive")
        if poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()
        self._max_abort_attempts = max_abort_attempts
        self._max_status_polls = max_status_polls
        self._poll_delay_seconds = poll_delay_seconds
        self._sleeper = sleeper

    def abort(
        self,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        reason: str,
    ) -> AbortDeliveryOutcome:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("abort reason must not be blank")
        command = AbortIngestionCommand(
            ingestion_session_id=ingestion_session_id,
            reason=normalized_reason,
        )
        self._assert_checkpoint_session(job_id, ingestion_session_id)

        abort_attempts = 0
        observations = 0
        last_status: IngestionStatus | None = None

        while True:
            if last_status is None or last_status.state is IngestionSessionState.ACTIVE:
                if abort_attempts >= self._max_abort_attempts:
                    raise AbortReconciliationPending(
                        state=IngestionSessionState.ACTIVE,
                        observations=observations,
                    )
                abort_attempts += 1
                try:
                    status = self._port.abort(command)
                except AstraVectorTransportError as exc:
                    if exc.code not in _AMBIGUOUS_ABORT_CODES:
                        raise
                    last_status = self._observe_status(job_id, ingestion_session_id)
                    observations += 1
                    continue

                self._validate_status_identity(status, ingestion_session_id)
                self._record_status(job_id, status)
                return self._resolve_observed(status, direct=True)

            if last_status.state is IngestionSessionState.FINALIZING:
                if observations >= self._max_status_polls:
                    raise AbortReconciliationPending(
                        state=IngestionSessionState.FINALIZING,
                        observations=observations,
                    )
                if self._poll_delay_seconds:
                    self._sleeper(self._poll_delay_seconds)
                last_status = self._observe_status(job_id, ingestion_session_id)
                observations += 1
                continue

            return self._resolve_observed(last_status, direct=False)

    def _resolve_observed(
        self,
        status: IngestionStatus,
        *,
        direct: bool,
    ) -> AbortDeliveryOutcome:
        if status.state is IngestionSessionState.ABORTED:
            return AbortDeliveryOutcome(
                resolution=(
                    AbortResolution.DIRECT_ACK if direct else AbortResolution.RECONCILED_ABORTED
                ),
                status=status,
            )
        if status.state in {IngestionSessionState.FAILED, IngestionSessionState.EXPIRED}:
            return AbortDeliveryOutcome(
                resolution=AbortResolution.ALREADY_TERMINAL,
                status=status,
            )
        if status.state is IngestionSessionState.COMPLETED:
            raise AbortConflictError(status)
        if status.state is IngestionSessionState.ACTIVE:
            raise AbortReconciliationPending(state=status.state, observations=0)
        raise AbortReconciliationPending(state=status.state, observations=0)

    def _observe_status(self, job_id: UUID, ingestion_session_id: UUID) -> IngestionStatus:
        status = self._port.get_ingestion_status(ingestion_session_id)
        self._validate_status_identity(status, ingestion_session_id)
        self._record_status(job_id, status)
        return status

    def _record_status(self, job_id: UUID, status: IngestionStatus) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._repository.record_session_status(
                    session,
                    job_id=job_id,
                    ingestion_session_id=status.ingestion_session_id,
                    session_status_raw=status.raw_status,
                    error_code=status.error_code,
                    error_message=status.error_message,
                )

    def _assert_checkpoint_session(self, job_id: UUID, ingestion_session_id: UUID) -> None:
        with self._session_factory() as session:
            checkpoint = self._repository.checkpoint(session, job_id)
            if checkpoint is None:
                raise DeliveryIntegrityError("delivery checkpoint does not exist for abort")
            if checkpoint.ingestion_session_id != ingestion_session_id:
                raise DeliveryIntegrityError(
                    "Abort attempted for a different AstraVector ingestion session"
                )

    @staticmethod
    def _validate_status_identity(status: IngestionStatus, ingestion_session_id: UUID) -> None:
        if status.ingestion_session_id != ingestion_session_id:
            raise DeliveryIntegrityError(
                "Abort reconciliation returned a different AstraVector ingestion session"
            )
