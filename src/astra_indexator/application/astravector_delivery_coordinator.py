from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import ClaimedJob, JobCoordinator, LeaseToken
from astra_indexator.application.delivery_compatibility import delivery_compatibility_sha256
from astra_indexator.application.delivery_identity import (
    resolve_verified_source_sha256,
    start_idempotency_key,
)
from astra_indexator.application.durable_append_delivery import (
    DurableAppendDeliveryRunner,
    DurableAppendLeaseFence,
    DurableBatchDeliveryOutcome,
)
from astra_indexator.application.finalize_reconciliation import (
    FinalizeReadinessIdentityUnavailable,
    FinalizeReconciliationRunner,
)
from astra_indexator.application.vector_activation import VectorActivationRunner
from astra_indexator.application.vector_readiness import (
    VectorReadinessOutcome,
    VectorReadinessRunner,
)
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import (
    AstraVectorIngestionPort,
    LogicalBlock,
    StartIngestionCommand,
)
from astra_indexator.astravector.validation import validate_logical_blocks
from astra_indexator.persistence.delivery import DeliveryBatchRepository, DeliveryIntegrityError
from astra_indexator.persistence.models import IndexationJob


class DeliveryCoordinatorError(RuntimeError):
    pass


class DeliveryRecoveryContractGap(DeliveryCoordinatorError):
    """Finalized AstraVector wire cannot reconstruct readiness UUID after ambiguity.

    AstraIndexator's producer/domain identity remains AccessZoneCode-only. The UUID involved in
    this recovery case is downstream AstraVector evidence required by the current DocumentRef wire.
    """


@dataclass(frozen=True, slots=True)
class AstraVectorDeliveryInput:
    logical_blocks: Sequence[LogicalBlock]
    source_file_name: str = ""
    source_content_hash: str = ""
    prepared_compatibility_sha256: str = ""
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AstraVectorDeliveryOutcome:
    ingestion_session_id: UUID
    access_zone_code: str
    resolved_access_zone_id: UUID
    batches: tuple[DurableBatchDeliveryOutcome, ...]
    readiness: VectorReadinessOutcome


class AstraVectorDeliveryCoordinator:
    """Lease-fenced Start -> Append -> Finalize -> readiness orchestration.

    ``access_zone_code`` is the only producer-owned AccessZone identity in AstraIndexator and is
    preserved unchanged through Start. AstraIndexator never accepts or derives an AccessZone UUID.
    ``resolved_access_zone_id`` is retained only as private downstream recovery evidence because
    the finalized AstraVector GetDocumentVectorStatus wire currently requires DocumentRef UUID.

    PostgreSQL is the durable recovery boundary. The complete LogicalBlock graph is validated
    before downstream mutation. A verified durable source SHA-256 and M7 compatibility fingerprint
    are required before Start. Mutating RPCs start only when their configured deadline plus safety
    margin fits inside the remaining PostgreSQL lease window.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        planner: DeterministicBatchPlanner,
        *,
        job_coordinator: JobCoordinator | None = None,
        repository: DeliveryBatchRepository | None = None,
        append_runner: DurableAppendDeliveryRunner | None = None,
        lease_fence: DurableAppendLeaseFence | None = None,
        finalize_runner: FinalizeReconciliationRunner | None = None,
        readiness_runner: VectorReadinessRunner | None = None,
        activation_runner: VectorActivationRunner | None = None,
        mutating_rpc_deadline_seconds: float = 30.0,
        rpc_safety_margin_seconds: float = 5.0,
    ) -> None:
        if mutating_rpc_deadline_seconds <= 0:
            raise ValueError("mutating_rpc_deadline_seconds must be positive")
        if rpc_safety_margin_seconds < 0:
            raise ValueError("rpc_safety_margin_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._planner = planner
        self._job_coordinator = job_coordinator or JobCoordinator()
        self._repository = repository or DeliveryBatchRepository()
        self._lease_fence = lease_fence or DurableAppendLeaseFence()
        self._mutating_rpc_deadline_seconds = mutating_rpc_deadline_seconds
        self._rpc_safety_margin_seconds = rpc_safety_margin_seconds
        self._append_runner = append_runner or DurableAppendDeliveryRunner(
            session_factory,
            port,
            repository=self._repository,
            lease_fence=self._lease_fence,
            rpc_deadline_seconds=mutating_rpc_deadline_seconds,
            rpc_safety_margin_seconds=rpc_safety_margin_seconds,
        )
        self._finalize_runner = finalize_runner or FinalizeReconciliationRunner(
            session_factory, port, repository=self._repository
        )
        self._readiness_runner = readiness_runner or VectorReadinessRunner(
            session_factory, port, repository=self._repository
        )
        self._activation_runner = activation_runner or VectorActivationRunner(
            session_factory,
            port,
            repository=self._repository,
            lease_fence=self._lease_fence,
            max_active_polls=90,
            poll_delay_seconds=1.0,
        )

    def deliver(
        self,
        claimed: ClaimedJob,
        payload: AstraVectorDeliveryInput,
    ) -> AstraVectorDeliveryOutcome:
        validate_logical_blocks(payload.logical_blocks)

        job = self._load_owned_job(claimed)
        source_sha256 = resolve_verified_source_sha256(
            durable_hash=job.source_content_hash,
            payload_hash=payload.source_content_hash or None,
        )
        compatibility_sha256 = delivery_compatibility_sha256(payload.prepared_compatibility_sha256)
        self._persist_delivery_compatibility(claimed.token, compatibility_sha256)
        session_id = self._ensure_session(job, claimed, payload, source_sha256)

        self._advance_stage(claimed, "ASTRAVECTOR_APPEND")
        batches = self._planner.plan(payload.logical_blocks)
        batch_outcomes = self._append_runner.deliver(
            token=claimed.token,
            ingestion_session_id=session_id,
            batches=batches,
        )

        final_hash = self._planner.final_content_hash(payload.logical_blocks)
        self._persist_final_hash(claimed.token, session_id, final_hash)
        self._advance_stage(claimed, "ASTRAVECTOR_FINALIZE")

        self._assert_safe_mutating_rpc_window(claimed.token)
        try:
            finalize = self._finalize_runner.finalize(
                job_id=job.id,
                ingestion_session_id=session_id,
                final_content_hash=final_hash,
                access_zone_id=None,
                document_id=job.document_id,
                document_version=job.document_version,
            )
        except FinalizeReadinessIdentityUnavailable as exc:
            raise DeliveryRecoveryContractGap(str(exc)) from exc

        resolved_zone_id = (
            finalize.finalize_result.access_zone_id
            if finalize.finalize_result is not None
            else None
        )
        if resolved_zone_id is None:
            raise DeliveryRecoveryContractGap(
                "AstraVector delivery completed without the downstream DocumentRef UUID required "
                "by the finalized vector-status wire; producer accessZoneCode remains unchanged"
            )
        self._persist_resolved_zone(claimed.token, session_id, resolved_zone_id)

        self._advance_stage(claimed, "ASTRAVECTOR_READINESS")
        self._advance_stage(claimed, "ASTRAVECTOR_ACTIVATE")
        activation = self._activation_runner.activate_until_searchable(
            token=claimed.token,
            ingestion_session_id=session_id,
            access_zone_id=resolved_zone_id,
            document_id=job.document_id,
            document_version=job.document_version,
            initial_status=finalize.vector_status,
        )
        readiness = activation.readiness

        self._complete(claimed)
        return AstraVectorDeliveryOutcome(
            ingestion_session_id=session_id,
            access_zone_code=job.access_zone_code,
            resolved_access_zone_id=resolved_zone_id,
            batches=batch_outcomes,
            readiness=readiness,
        )

    def _load_owned_job(self, claimed: ClaimedJob) -> IndexationJob:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, claimed.token)
                job = session.get(IndexationJob, claimed.token.job_id)
                if job is None:
                    raise DeliveryCoordinatorError("claimed IndexationJob no longer exists")
                session.expunge(job)
                return job

    def _ensure_session(
        self,
        job: IndexationJob,
        claimed: ClaimedJob,
        payload: AstraVectorDeliveryInput,
        source_sha256: str,
    ) -> UUID:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, claimed.token)
                checkpoint = self._repository.checkpoint(session, job.id)
                if checkpoint is not None and checkpoint.ingestion_session_id is not None:
                    return checkpoint.ingestion_session_id

        self._advance_stage(claimed, "ASTRAVECTOR_START")
        command = StartIngestionCommand(
            access_zone_id=None,
            access_zone_code=job.access_zone_code,
            document_id=job.document_id,
            document_version=job.document_version,
            source_uri=job.source_uri,
            file_name=payload.source_file_name or job.source_file_name or "document",
            content_hash=source_sha256,
            idempotency_key=start_idempotency_key(
                document_id=job.document_id,
                document_version=job.document_version,
                source_sha256=source_sha256,
            ),
            total_bytes_estimate=job.source_size_bytes or 0,
            total_blocks_estimate=len(payload.logical_blocks),
            total_pages_estimate=0,
            metadata=payload.metadata or {},
            ttl_days=job.requested_ttl_days or 0,
        )

        self._assert_safe_mutating_rpc_window(claimed.token)
        started = self._port.start(command)

        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, claimed.token)
                self._repository.bind_session(
                    session,
                    job_id=job.id,
                    ingestion_session_id=started.ingestion_session_id,
                    session_status_raw=started.raw_status,
                )
        return started.ingestion_session_id

    def _persist_delivery_compatibility(self, token: LeaseToken, fingerprint: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, token)
                checkpoint = self._repository.ensure_checkpoint(session, token.job_id)
                if (
                    checkpoint.delivery_compatibility_sha256 is not None
                    and checkpoint.delivery_compatibility_sha256 != fingerprint
                ):
                    raise DeliveryIntegrityError(
                        "persisted M8 delivery compatibility fingerprint differs from current contract"
                    )
                checkpoint.delivery_compatibility_sha256 = fingerprint
                session.flush()

    def _persist_final_hash(
        self,
        token: LeaseToken,
        session_id: UUID,
        final_hash: str,
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, token)
                checkpoint = self._repository.checkpoint(session, token.job_id)
                if checkpoint is None or checkpoint.ingestion_session_id != session_id:
                    raise DeliveryIntegrityError("final hash checkpoint belongs to another session")
                if (
                    checkpoint.final_content_hash is not None
                    and checkpoint.final_content_hash != final_hash
                ):
                    raise DeliveryIntegrityError(
                        "reconstructed logical document has a different final_content_hash"
                    )
                checkpoint.final_content_hash = final_hash
                session.flush()

    def _persist_resolved_zone(
        self,
        token: LeaseToken,
        session_id: UUID,
        zone_id: UUID,
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, token)
                checkpoint = self._repository.checkpoint(session, token.job_id)
                if checkpoint is None or checkpoint.ingestion_session_id != session_id:
                    raise DeliveryIntegrityError(
                        "downstream zone checkpoint belongs to another session"
                    )
                if (
                    checkpoint.resolved_access_zone_id is not None
                    and checkpoint.resolved_access_zone_id != zone_id
                ):
                    raise DeliveryIntegrityError(
                        "AstraVector returned a different internal accessZoneId than persisted"
                    )
                checkpoint.resolved_access_zone_id = zone_id
                session.flush()

    def _assert_safe_mutating_rpc_window(self, token: LeaseToken) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_safe_rpc_window(
                    session,
                    token,
                    rpc_deadline_seconds=self._mutating_rpc_deadline_seconds,
                    safety_margin_seconds=self._rpc_safety_margin_seconds,
                )

    def _advance_stage(self, claimed: ClaimedJob, stage: str) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._job_coordinator.advance_stage(session, claimed.token, stage=stage)

    def _complete(self, claimed: ClaimedJob) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._job_coordinator.complete(session, claimed.token)
