from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import LeaseToken
from astra_indexator.application.durable_append_delivery import DurableAppendLeaseFence
from astra_indexator.application.vector_readiness import (
    VectorReadinessOutcome,
    VectorReadinessRunner,
)
from astra_indexator.astravector.contracts import (
    ActivateDocumentVersionCommand,
    ActivateDocumentVersionResult,
    AstraVectorIngestionPort,
    AstraVectorTransportError,
    DocumentVectorStatus,
)
from astra_indexator.astravector.policy import (
    ActivationReadinessPolicy,
    VectorReadinessDisposition,
    evaluate_vector_readiness,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository


@dataclass(frozen=True, slots=True)
class VectorActivationOutcome:
    activation: ActivateDocumentVersionResult | None
    readiness: VectorReadinessOutcome


class VectorActivationPending(RuntimeError):
    def __init__(self, *, status: DocumentVectorStatus, polls: int) -> None:
        super().__init__(
            "AstraVector activation did not reach ACTIVE/searchable after "
            f"{polls} observations; last state={status.raw_state}"
        )
        self.status = status
        self.polls = polls


class VectorActivationRunner:
    """Activate a MANUAL AstraVector document version using public APIs and lease fencing."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
        lease_fence: DurableAppendLeaseFence | None = None,
        max_active_polls: int = 20,
        poll_delay_seconds: float = 0.0,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if max_active_polls <= 0:
            raise ValueError("max_active_polls must be positive")
        if poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()
        self._lease_fence = lease_fence or DurableAppendLeaseFence()
        self._max_active_polls = max_active_polls
        self._poll_delay_seconds = poll_delay_seconds
        self._sleeper = sleeper

    def activate_until_searchable(
        self,
        *,
        token: LeaseToken,
        ingestion_session_id: UUID,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
        initial_status: DocumentVectorStatus,
    ) -> VectorActivationOutcome:
        ready = VectorReadinessRunner(
            self._session_factory,
            self._port,
            repository=self._repository,
            policy=ActivationReadinessPolicy.ALLOW_READY_TO_ACTIVATE,
            max_polls=self._max_active_polls,
            poll_delay_seconds=self._poll_delay_seconds,
            sleeper=self._sleeper,
        ).wait_until_ready(
            job_id=token.job_id,
            ingestion_session_id=ingestion_session_id,
            access_zone_id=access_zone_id,
            document_id=document_id,
            document_version=document_version,
            initial_status=initial_status,
        )
        if ready.decision.disposition is VectorReadinessDisposition.SEARCHABLE:
            return VectorActivationOutcome(activation=None, readiness=ready)
        if ready.decision.disposition is not VectorReadinessDisposition.READY_TO_ACTIVATE:
            raise VectorActivationPending(status=ready.status, polls=ready.polls)

        self._assert_owned(token)
        try:
            activation = self._port.activate_document_version(
                ActivateDocumentVersionCommand(
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
            )
        except AstraVectorTransportError:
            status = self._port.get_document_vector_status(
                access_zone_id=access_zone_id,
                document_id=document_id,
                document_version=document_version,
            )
            active = self._wait_for_active(
                token=token,
                ingestion_session_id=ingestion_session_id,
                access_zone_id=access_zone_id,
                document_id=document_id,
                document_version=document_version,
                initial_status=status,
            )
            return VectorActivationOutcome(activation=None, readiness=active)

        self._assert_owned(token)
        active = self._wait_for_active(
            token=token,
            ingestion_session_id=ingestion_session_id,
            access_zone_id=access_zone_id,
            document_id=document_id,
            document_version=document_version,
            initial_status=None,
        )
        return VectorActivationOutcome(activation=activation, readiness=active)

    def _wait_for_active(
        self,
        *,
        token: LeaseToken,
        ingestion_session_id: UUID,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
        initial_status: DocumentVectorStatus | None,
    ) -> VectorReadinessOutcome:
        polls = 0
        status = initial_status
        last_status: DocumentVectorStatus | None = None
        while polls < self._max_active_polls:
            self._assert_owned(token)
            if status is None:
                status = self._port.get_document_vector_status(
                    access_zone_id=access_zone_id,
                    document_id=document_id,
                    document_version=document_version,
                )
            last_status = status
            self._record_status(token.job_id, status)
            decision = evaluate_vector_readiness(status)
            polls += 1
            if decision.disposition is VectorReadinessDisposition.SEARCHABLE:
                return VectorReadinessOutcome(decision=decision, status=status, polls=polls)
            if self._poll_delay_seconds:
                self._sleeper(self._poll_delay_seconds)
            status = None
        if last_status is None:
            raise RuntimeError("activation polling produced no status observations")
        raise VectorActivationPending(status=last_status, polls=polls)

    def _record_status(self, job_id: UUID, status: DocumentVectorStatus) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._repository.record_vector_status(session, job_id=job_id, status=status)

    def _assert_owned(self, token: LeaseToken) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, token)
