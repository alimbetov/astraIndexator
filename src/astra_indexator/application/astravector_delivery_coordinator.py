from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.application.append_delivery import AppendDeliveryRunner, BatchDeliveryOutcome
from astra_indexator.application.coordinator import ClaimedJob, JobCoordinator
from astra_indexator.application.finalize_reconciliation import FinalizeReconciliationRunner
from astra_indexator.application.vector_readiness import VectorReadinessOutcome, VectorReadinessRunner
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import (
    AstraVectorIngestionPort,
    LogicalBlock,
    StartIngestionCommand,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository, DeliveryIntegrityError
from astra_indexator.persistence.models import IndexationJob


class DeliveryCoordinatorError(RuntimeError):
    pass


class AccessZoneResolutionRequired(DeliveryCoordinatorError):
    """The public facade cannot recover resolved zone identity from this ambiguous state."""


@dataclass(frozen=True, slots=True)
class AstraVectorDeliveryInput:
    logical_blocks: Sequence[LogicalBlock]
    source_file_name: str = ""
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AstraVectorDeliveryOutcome:
    ingestion_session_id: UUID
    batches: tuple[BatchDeliveryOutcome, ...]
    readiness: VectorReadinessOutcome


class AstraVectorDeliveryCoordinator:
    """Lease-fenced Start -> Append -> Finalize -> readiness orchestration.

    PostgreSQL is the durable recovery boundary. A previously bound ingestion session is reused.
    If Start succeeded remotely but the worker died before binding the session, the next worker
    replays the same idempotent Start command. Append/finalize/readiness delegate to the durable
    replay and reconciliation runners implemented in M8.2.5-M8.2.8.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        planner: DeterministicBatchPlanner,
        *,
        job_coordinator: JobCoordinator | None = None,
        repository: DeliveryBatchRepository | None = None,
        append_runner: AppendDeliveryRunner | None = None,
        finalize_runner: FinalizeReconciliationRunner | None = None,
        readiness_runner: VectorReadinessRunner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._port = port
        self._planner = planner
        self._job_coordinator = job_coordinator or JobCoordinator()
        self._repository = repository or DeliveryBatchRepository()
        self._append_runner = append_runner or AppendDeliveryRunner(
            session_factory, port, repository=self._repository
        )
        self._finalize_runner = finalize_runner or FinalizeReconciliationRunner(
            session_factory, port, repository=self._repository
        )
        self._readiness_runner = readiness_runner or VectorReadinessRunner(
            session_factory, port, repository=self._repository
        )

    def deliver(
        self,
        claimed: ClaimedJob,
        payload: AstraVectorDeliveryInput,
    ) -> AstraVectorDeliveryOutcome:
        if not payload.logical_blocks:
            raise DeliveryCoordinatorError("AstraVector delivery requires at least one LogicalBlock")

        job = self._load_owned_job(claimed)
        session_id = self._ensure_session(job, claimed, payload)

        self._advance_stage(claimed, "ASTRAVECTOR_APPEND")
        batches = self._planner.plan(payload.logical_blocks)
        batch_outcomes = self._append_runner.deliver(
            job_id=job.id,
            ingestion_session_id=session_id,
            batches=batches,
        )

        final_hash = self._planner.final_content_hash(payload.logical_blocks)
        self._persist_final_hash(job.id, session_id, final_hash)
        self._advance_stage(claimed, "ASTRAVECTOR_FINALIZE")

        expected_zone_id = job.requested_access_zone_id or job.access_zone_id
        if expected_zone_id is None:
            raise AccessZoneResolutionRequired(
                "code-only delivery needs resolved accessZoneId for finalize ambiguity/readiness; "
                "do not read AstraVector-owned registry tables directly"
            )

        finalize = self._finalize_runner.finalize(
            job_id=job.id,
            ingestion_session_id=session_id,
            final_content_hash=final_hash,
            access_zone_id=expected_zone_id,
            document_id=job.document_id,
            document_version=job.document_version,
        )

        self._advance_stage(claimed, "ASTRAVECTOR_READINESS")
        readiness = self._readiness_runner.wait_until_ready(
            job_id=job.id,
            ingestion_session_id=session_id,
            access_zone_id=expected_zone_id,
            document_id=job.document_id,
            document_version=job.document_version,
            initial_status=finalize.vector_status,
        )

        self._complete(claimed)
        return AstraVectorDeliveryOutcome(
            ingestion_session_id=session_id,
            batches=batch_outcomes,
            readiness=readiness,
        )

    def _load_owned_job(self, claimed: ClaimedJob) -> IndexationJob:
        with self._session_factory() as session:
            job = session.get(IndexationJob, claimed.token.job_id)
            if job is None:
                raise DeliveryCoordinatorError("claimed IndexationJob no longer exists")
            if job.worker_id != claimed.token.worker_id:
                raise DeliveryCoordinatorError("claimed job is no longer owned by this worker")
            if job.lease_generation != claimed.token.lease_generation:
                raise DeliveryCoordinatorError("claimed job lease generation changed")
            session.expunge(job)
            return job

    def _ensure_session(
        self,
        job: IndexationJob,
        claimed: ClaimedJob,
        payload: AstraVectorDeliveryInput,
    ) -> UUID:
        with self._session_factory() as session:
            checkpoint = self._repository.checkpoint(session, job.id)
            if checkpoint is not None and checkpoint.ingestion_session_id is not None:
                return checkpoint.ingestion_session_id

        self._advance_stage(claimed, "ASTRAVECTOR_START")
        command = StartIngestionCommand(
            access_zone_id=job.requested_access_zone_id,
            access_zone_code=job.requested_access_zone_code,
            document_id=job.document_id,
            document_version=job.document_version,
            source_uri=job.source_uri,
            file_name=payload.source_file_name or job.source_file_name or "document",
            content_hash=job.source_content_hash or "",
            idempotency_key=f"astra-indexator:{job.id}:{job.document_version}",
            total_bytes_estimate=job.source_size_bytes or 0,
            total_blocks_estimate=len(payload.logical_blocks),
            total_pages_estimate=0,
            metadata=payload.metadata or {},
            ttl_days=job.requested_ttl_days or 0,
        )
        started = self._port.start(command)

        with self._session_factory() as session:
            with session.begin():
                self._repository.bind_session(
                    session,
                    job_id=job.id,
                    ingestion_session_id=started.ingestion_session_id,
                    session_status_raw=started.raw_status,
                )
        return started.ingestion_session_id

    def _persist_final_hash(self, job_id: UUID, session_id: UUID, final_hash: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                checkpoint = self._repository.checkpoint(session, job_id)
                if checkpoint is None or checkpoint.ingestion_session_id != session_id:
                    raise DeliveryIntegrityError("final hash checkpoint belongs to another session")
                if checkpoint.final_content_hash is not None and checkpoint.final_content_hash != final_hash:
                    raise DeliveryIntegrityError(
                        "reconstructed logical document has a different final_content_hash"
                    )
                checkpoint.final_content_hash = final_hash
                session.flush()

    def _advance_stage(self, claimed: ClaimedJob, stage: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._job_coordinator.advance_stage(session, claimed.token, stage=stage)

    def _complete(self, claimed: ClaimedJob) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._job_coordinator.complete(session, claimed.token)
