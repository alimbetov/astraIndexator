from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.astravector.contracts import AstraVectorIngestionPort, DocumentVectorStatus
from astra_indexator.astravector.policy import (
    ActivationReadinessPolicy,
    VectorReadinessDecision,
    VectorReadinessDisposition,
    evaluate_vector_readiness,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository, DeliveryIntegrityError


@dataclass(frozen=True, slots=True)
class VectorReadinessOutcome:
    decision: VectorReadinessDecision
    status: DocumentVectorStatus
    polls: int


class VectorReadinessPending(RuntimeError):
    def __init__(self, *, status: DocumentVectorStatus, polls: int) -> None:
        super().__init__(
            "AstraVector readiness did not reach the configured completion boundary after "
            f"{polls} observations; last state={status.raw_state}"
        )
        self.status = status
        self.polls = polls


class VectorReadinessTerminalError(RuntimeError):
    def __init__(self, *, status: DocumentVectorStatus, decision: VectorReadinessDecision) -> None:
        super().__init__(decision.reason)
        self.status = status
        self.decision = decision


class VectorReadinessRunner:
    """Reconcile post-finalize vector/searchability readiness through the public facade only.

    REQUIRE_SEARCHABLE is the production-safe default: READY_TO_ACTIVATE is persisted but does not
    complete delivery until AstraVector reports ACTIVE/searchable. ALLOW_READY_TO_ACTIVATE is an
    explicit manual-activation handoff mode and returns once synchronization is complete.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
        policy: ActivationReadinessPolicy = ActivationReadinessPolicy.REQUIRE_SEARCHABLE,
        max_polls: int = 20,
        poll_delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_polls <= 0:
            raise ValueError("max_polls must be positive")
        if poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()
        self._policy = policy
        self._max_polls = max_polls
        self._poll_delay_seconds = poll_delay_seconds
        self._sleeper = sleeper

    def wait_until_ready(
        self,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
        initial_status: DocumentVectorStatus | None = None,
    ) -> VectorReadinessOutcome:
        self._assert_checkpoint_session(job_id, ingestion_session_id)
        polls = 0
        status = initial_status

        while True:
            if status is None:
                status = self._port.get_document_vector_status(
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
            polls += 1
            self._record_status(job_id, status)
            decision = evaluate_vector_readiness(status, policy=self._policy)

            if decision.disposition in {
                VectorReadinessDisposition.SEARCHABLE,
                VectorReadinessDisposition.READY_TO_ACTIVATE,
            }:
                return VectorReadinessOutcome(decision=decision, status=status, polls=polls)
            if decision.disposition is VectorReadinessDisposition.TERMINAL:
                raise VectorReadinessTerminalError(status=status, decision=decision)
            if polls >= self._max_polls:
                raise VectorReadinessPending(status=status, polls=polls)

            if self._poll_delay_seconds:
                self._sleeper(self._poll_delay_seconds)
            status = self._port.get_document_vector_status(
                access_zone_id=access_zone_id,
                document_id=document_id,
                document_version=document_version,
            )

    def _record_status(self, job_id: UUID, status: DocumentVectorStatus) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._repository.record_vector_status(session, job_id=job_id, status=status)

    def _assert_checkpoint_session(self, job_id: UUID, ingestion_session_id: UUID) -> None:
        with self._session_factory() as session:
            checkpoint = self._repository.checkpoint(session, job_id)
            if checkpoint is None:
                raise DeliveryIntegrityError("delivery checkpoint does not exist for readiness")
            if checkpoint.ingestion_session_id != ingestion_session_id:
                raise DeliveryIntegrityError(
                    "vector readiness attempted for a different AstraVector ingestion session"
                )
