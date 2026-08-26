from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.abort_reconciliation import (
    AbortConflictError,
    AbortReconciliationPending,
    AbortReconciliationRunner,
)
from astra_indexator.application.delete_reconciliation import (
    DeleteReconciliationFailed,
    DeleteReconciliationPending,
    DeleteReconciliationRunner,
)
from astra_indexator.astravector.contracts import (
    AstraVectorIngestionPort,
    DeleteDocumentCommand,
)
from astra_indexator.domain.lifecycle import (
    DocumentLifecycleState,
    LifecycleOperationType,
)
from astra_indexator.persistence.knowledge_inventory import KnowledgeInventoryRepository
from astra_indexator.persistence.lifecycle import (
    DocumentLifecycleRepository,
    LifecycleIntegrityError,
    LifecycleOperationRepository,
    NewLifecycleOperation,
)
from astra_indexator.persistence.lifecycle_models import LifecycleOperation
from astra_indexator.persistence.models import (
    DeliveryCheckpoint,
    IndexationJob,
    JobEvent,
    ProcessingAttempt,
)
from astra_indexator.persistence.repository import (
    IndexationJobRepository,
    NewIndexationJob,
)


class LifecycleSemanticConflict(RuntimeError):
    pass


class LifecycleRecoveryPending(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReindexRequest:
    producer_request_id: UUID
    document_id: UUID
    document_version: int
    source_uri: str
    access_zone_code: str | None = None
    access_zone_id: UUID | None = None
    requested_ttl_days: int | None = None
    source_file_name: str | None = None
    storage_object_id: UUID | None = None
    storage_object_name: str | None = None
    source_content_hash: str | None = None
    source_size_bytes: int | None = None
    knowledge_type: str | None = None
    external_revision: str | None = None
    priority: int = 0
    max_attempts: int = 5


@dataclass(frozen=True, slots=True)
class LifecycleRequestOutcome:
    operation_id: UUID
    document_id: UUID
    document_version: int
    lifecycle_state: DocumentLifecycleState
    job_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _OperationSnapshot:
    operation_id: UUID
    producer_request_id: UUID
    document_id: UUID
    document_version: int
    job_id: UUID
    reason: str
    state: DocumentLifecycleState
    ingestion_session_id: UUID | None
    resolved_access_zone_id: UUID | None


class DocumentLifecycleService:
    """M9 application service for version/reindex/cancel/delete semantics.

    Database phases are intentionally short. Mutating AstraVector RPCs are always
    executed outside PostgreSQL transactions; their result is persisted in a new
    transaction. This keeps crash windows explicit and reconcilable.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        lifecycle_repository: DocumentLifecycleRepository | None = None,
        operation_repository: LifecycleOperationRepository | None = None,
        job_repository: IndexationJobRepository | None = None,
        inventory_repository: KnowledgeInventoryRepository | None = None,
        abort_runner: AbortReconciliationRunner | None = None,
        delete_runner: DeleteReconciliationRunner | None = None,
        retry_delay_seconds: int = 5,
    ) -> None:
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self._session_factory = session_factory
        self._port = port
        self._inventory = inventory_repository or KnowledgeInventoryRepository()
        self._lifecycle = lifecycle_repository or DocumentLifecycleRepository(
            inventory_repository=self._inventory
        )
        self._operations = operation_repository or LifecycleOperationRepository()
        self._jobs = job_repository or IndexationJobRepository()
        self._abort = abort_runner or AbortReconciliationRunner(session_factory, port)
        self._delete = delete_runner or DeleteReconciliationRunner(port)
        self._retry_delay_seconds = retry_delay_seconds

    def request_reindex(self, request: ReindexRequest) -> LifecycleRequestOutcome:
        command = NewIndexationJob(
            producer_request_id=request.producer_request_id,
            document_id=request.document_id,
            document_version=request.document_version,
            source_uri=request.source_uri,
            requested_access_zone_code=request.access_zone_code,
            requested_access_zone_id=request.access_zone_id,
            requested_ttl_days=request.requested_ttl_days,
            source_file_name=request.source_file_name,
            storage_object_id=request.storage_object_id,
            storage_object_name=request.storage_object_name,
            source_content_hash=request.source_content_hash,
            source_size_bytes=request.source_size_bytes,
            knowledge_type=request.knowledge_type,
            external_revision=request.external_revision,
            priority=request.priority,
            max_attempts=request.max_attempts,
        )
        with self._session_factory() as session:
            with session.begin():
                self._assert_version_monotonic(session, request)
                job = self._jobs.create_or_get(session, command)
                self._assert_job_matches_reindex(job, request)
                lifecycle = self._lifecycle.ensure_building_for_job(session, job)
                operation = self._operations.create_or_get(
                    session,
                    NewLifecycleOperation(
                        producer_request_id=request.producer_request_id,
                        operation_type=LifecycleOperationType.REINDEX,
                        document_id=request.document_id,
                        document_version=request.document_version,
                        job_id=job.id,
                        requested_access_zone_code=request.access_zone_code,
                        requested_access_zone_id=request.access_zone_id,
                        reason="reindex document version",
                    ),
                )
                return self._outcome(operation, lifecycle)

    def activate_ready_job(self, job_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                job = self._require_job(session, job_id)
                lifecycle = self._lifecycle.get_version(
                    session,
                    document_id=job.document_id,
                    document_version=job.document_version,
                    for_update=True,
                )
                if lifecycle is None:
                    lifecycle = self._lifecycle.ensure_building_for_job(session, job)

                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.BUILDING:
                    lifecycle = self._lifecycle.mark_ready(
                        session,
                        document_id=job.document_id,
                        document_version=job.document_version,
                    )
                    state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.READY:
                    lifecycle = self._lifecycle.activate_version(
                        session,
                        document_id=job.document_id,
                        document_version=job.document_version,
                    )
                elif state is not DocumentLifecycleState.ACTIVE:
                    raise LifecycleSemanticConflict(
                        f"cannot activate lifecycle state {state.value}"
                    )

                operation = self._find_reindex_operation(session, job.id)
                if operation is not None:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                return LifecycleRequestOutcome(
                    operation_id=uuid5(NAMESPACE_URL, f"m9:activation:{job.id}"),
                    document_id=job.document_id,
                    document_version=job.document_version,
                    lifecycle_state=DocumentLifecycleState(lifecycle.state),
                    job_id=job.id,
                )

    def request_cancel(
        self,
        *,
        producer_request_id: UUID,
        document_id: UUID,
        document_version: int,
        reason: str,
    ) -> LifecycleRequestOutcome:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("cancel reason must not be blank")

        with self._session_factory() as session:
            with session.begin():
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=document_id,
                    document_version=document_version,
                    for_update=True,
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.ACTIVE:
                    raise LifecycleSemanticConflict(
                        "cancel does not delete an already ACTIVE document version"
                    )
                operation = self._operations.create_or_get(
                    session,
                    NewLifecycleOperation(
                        producer_request_id=producer_request_id,
                        operation_type=LifecycleOperationType.CANCEL,
                        document_id=document_id,
                        document_version=document_version,
                        job_id=lifecycle.job_id,
                        requested_access_zone_code=lifecycle.requested_access_zone_code,
                        requested_access_zone_id=lifecycle.requested_access_zone_id,
                        reason=normalized_reason,
                    ),
                )
                if state in {
                    DocumentLifecycleState.CANCELLED,
                    DocumentLifecycleState.DELETED,
                }:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                if state not in {
                    DocumentLifecycleState.CANCEL_PENDING,
                    DocumentLifecycleState.DELETE_PENDING,
                }:
                    lifecycle = self._lifecycle.transition(
                        session,
                        document_id=document_id,
                        document_version=document_version,
                        target=DocumentLifecycleState.CANCEL_PENDING,
                    )
                job = self._require_job(session, lifecycle.job_id)
                job.cancel_requested = True
                checkpoint = session.get(DeliveryCheckpoint, job.id)
                snapshot = self._snapshot(
                    operation,
                    lifecycle,
                    checkpoint,
                    normalized_reason,
                )

        return self._continue_cancel(snapshot)

    def request_delete(
        self,
        *,
        producer_request_id: UUID,
        document_id: UUID,
        document_version: int,
        reason: str,
    ) -> LifecycleRequestOutcome:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("delete reason must not be blank")

        with self._session_factory() as session:
            with session.begin():
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=document_id,
                    document_version=document_version,
                    for_update=True,
                )
                operation = self._operations.create_or_get(
                    session,
                    NewLifecycleOperation(
                        producer_request_id=producer_request_id,
                        operation_type=LifecycleOperationType.DELETE,
                        document_id=document_id,
                        document_version=document_version,
                        job_id=lifecycle.job_id,
                        requested_access_zone_code=lifecycle.requested_access_zone_code,
                        requested_access_zone_id=lifecycle.requested_access_zone_id,
                        reason=normalized_reason,
                    ),
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.DELETED:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                if state is DocumentLifecycleState.BUILDING:
                    raise LifecycleSemanticConflict(
                        "delete of BUILDING version must use cancel semantics first"
                    )
                if state is DocumentLifecycleState.CANCELLED:
                    raise LifecycleSemanticConflict(
                        "cancelled version has no approved delete transition"
                    )
                if state is not DocumentLifecycleState.DELETE_PENDING:
                    lifecycle = self._lifecycle.transition(
                        session,
                        document_id=document_id,
                        document_version=document_version,
                        target=DocumentLifecycleState.DELETE_PENDING,
                    )
                checkpoint = session.get(DeliveryCheckpoint, lifecycle.job_id)
                snapshot = self._snapshot(
                    operation,
                    lifecycle,
                    checkpoint,
                    normalized_reason,
                )

        return self._continue_delete(snapshot)

    def reconcile_reindex_operation(self, operation_id: UUID) -> LifecycleRequestOutcome:
        activate_job_id: UUID | None = None
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                self._require_operation_type(operation, LifecycleOperationType.REINDEX)
                if operation.job_id is None or operation.document_version is None:
                    raise LifecycleIntegrityError("REINDEX operation is missing job/version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.ACTIVE:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                job = self._require_job(session, operation.job_id)
                if state in {
                    DocumentLifecycleState.CANCELLED,
                    DocumentLifecycleState.DELETED,
                }:
                    self._operations.fail(
                        session,
                        operation,
                        error_code="REINDEX_TERMINATED",
                        error_message=f"candidate lifecycle ended as {state.value}",
                    )
                    return self._outcome(operation, lifecycle)
                if state is DocumentLifecycleState.FAILED:
                    self._operations.fail(
                        session,
                        operation,
                        error_code="REINDEX_FAILED",
                        error_message=job.last_error_message or "candidate version failed",
                    )
                    return self._outcome(operation, lifecycle)
                if job.status in {"FAILED", "DEAD_LETTER"}:
                    lifecycle = self._lifecycle.transition(
                        session,
                        document_id=job.document_id,
                        document_version=job.document_version,
                        target=DocumentLifecycleState.FAILED,
                    )
                    self._operations.fail(
                        session,
                        operation,
                        error_code=job.last_error_code or "INDEXATION_FAILED",
                        error_message=job.last_error_message or "indexation job failed",
                    )
                    return self._outcome(operation, lifecycle)
                checkpoint = session.get(DeliveryCheckpoint, job.id)
                if checkpoint is None or checkpoint.searchable is not True:
                    self._operations.schedule_retry(
                        session,
                        operation,
                        delay_seconds=self._retry_delay_seconds,
                        error_code="WAITING_FOR_SEARCHABLE",
                        error_message="candidate version is not searchable yet",
                    )
                    return self._outcome(operation, lifecycle)
                activate_job_id = job.id

        if activate_job_id is None:
            raise LifecycleIntegrityError("reindex activation target was not resolved")
        return self.activate_ready_job(activate_job_id)

    def reconcile_cancel_operation(self, operation_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                self._require_operation_type(operation, LifecycleOperationType.CANCEL)
                if operation.job_id is None or operation.document_version is None:
                    raise LifecycleIntegrityError("CANCEL operation is missing job/version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state in {
                    DocumentLifecycleState.CANCELLED,
                    DocumentLifecycleState.DELETED,
                }:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                if state is DocumentLifecycleState.ACTIVE:
                    self._operations.fail(
                        session,
                        operation,
                        error_code="CANCEL_ACTIVE_CONFLICT",
                        error_message="cancel cannot remove ACTIVE document version",
                    )
                    conflict = True
                else:
                    conflict = False
                checkpoint = session.get(DeliveryCheckpoint, operation.job_id)
                snapshot = self._snapshot(
                    operation,
                    lifecycle,
                    checkpoint,
                    operation.reason or "cancel document indexing",
                )

        if conflict:
            raise LifecycleSemanticConflict(
                "cancel cannot remove an ACTIVE document version"
            )
        return self._continue_cancel(snapshot)

    def reconcile_delete_operation(self, operation_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                self._require_operation_type(operation, LifecycleOperationType.DELETE)
                if operation.document_version is None:
                    raise LifecycleIntegrityError("DELETE operation is missing document_version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                if DocumentLifecycleState(lifecycle.state) is DocumentLifecycleState.DELETED:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                if DocumentLifecycleState(lifecycle.state) is not DocumentLifecycleState.DELETE_PENDING:
                    raise LifecycleIntegrityError(
                        "DELETE operation requires DELETE_PENDING lifecycle state"
                    )
                checkpoint = session.get(DeliveryCheckpoint, lifecycle.job_id)
                snapshot = self._snapshot(
                    operation,
                    lifecycle,
                    checkpoint,
                    operation.reason or "delete document version",
                )
        return self._continue_delete(snapshot)

    def reconcile_projection_operation(self, operation_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                self._require_operation_type(operation, LifecycleOperationType.RECONCILE)
                if operation.document_version is None:
                    raise LifecycleIntegrityError(
                        "RECONCILE operation is missing document_version"
                    )
                projection = self._inventory.rebuild(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                )
                self._operations.complete(session, operation)
                return LifecycleRequestOutcome(
                    operation_id=operation.id,
                    document_id=projection.document_id,
                    document_version=projection.document_version,
                    lifecycle_state=projection.lifecycle_state,
                    job_id=projection.job_id,
                )

    def rebuild_inventory(
        self,
        *,
        document_id: UUID,
        document_version: int,
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                self._inventory.rebuild(
                    session,
                    document_id=document_id,
                    document_version=document_version,
                )

    def _continue_cancel(self, snapshot: _OperationSnapshot) -> LifecycleRequestOutcome:
        if snapshot.state is DocumentLifecycleState.DELETE_PENDING:
            return self._cancel_finalize_won(snapshot)
        if snapshot.ingestion_session_id is None:
            return self._finish_local_cancel(snapshot.operation_id)
        try:
            self._abort.abort(
                job_id=snapshot.job_id,
                ingestion_session_id=snapshot.ingestion_session_id,
                reason=snapshot.reason,
            )
        except AbortConflictError:
            return self._cancel_finalize_won(snapshot)
        except AbortReconciliationPending as exc:
            self._schedule_operation_retry(
                snapshot.operation_id,
                error_code="ABORT_RECONCILIATION_PENDING",
                error_message=str(exc),
            )
            raise LifecycleRecoveryPending(str(exc)) from exc
        return self._finish_local_cancel(snapshot.operation_id)

    def _continue_delete(self, snapshot: _OperationSnapshot) -> LifecycleRequestOutcome:
        if snapshot.resolved_access_zone_id is None:
            if snapshot.ingestion_session_id is None:
                return self._finish_confirmed_delete(snapshot.operation_id)
            self._schedule_operation_retry(
                snapshot.operation_id,
                error_code="DELETE_IDENTITY_UNAVAILABLE",
                error_message=(
                    "downstream session exists but resolved accessZoneId is unavailable"
                ),
            )
            raise LifecycleRecoveryPending(
                "delete requires downstream DocumentRef identity that is not yet available"
            )

        command = DeleteDocumentCommand(
            access_zone_id=snapshot.resolved_access_zone_id,
            document_id=snapshot.document_id,
            document_version=snapshot.document_version,
            reason=snapshot.reason,
            idempotency_key=f"astra-indexator:delete:{snapshot.operation_id}",
            correlation_id=str(snapshot.operation_id),
        )
        try:
            self._delete.delete(command)
        except DeleteReconciliationPending as exc:
            self._schedule_operation_retry(
                snapshot.operation_id,
                error_code=exc.classification.value,
                error_message=str(exc),
            )
            raise LifecycleRecoveryPending(str(exc)) from exc
        except DeleteReconciliationFailed as exc:
            return self._finish_failed_delete(snapshot.operation_id, exc)
        return self._finish_confirmed_delete(snapshot.operation_id)

    def _finish_local_cancel(self, operation_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                if operation.job_id is None or operation.document_version is None:
                    raise LifecycleIntegrityError("CANCEL operation is missing job/version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.CANCELLED:
                    self._operations.complete(session, operation)
                    return self._outcome(operation, lifecycle)
                if state is not DocumentLifecycleState.CANCEL_PENDING:
                    raise LifecycleSemanticConflict(
                        f"cannot finish cancel from lifecycle state {state.value}"
                    )
                job = self._require_job(session, operation.job_id)
                if job.status == "COMPLETED":
                    raise LifecycleSemanticConflict(
                        "job completed before local cancellation could commit"
                    )
                lifecycle = self._lifecycle.transition(
                    session,
                    document_id=lifecycle.document_id,
                    document_version=lifecycle.document_version,
                    target=DocumentLifecycleState.CANCELLED,
                )
                self._cancel_job(session, job)
                self._operations.complete(session, operation)
                return self._outcome(operation, lifecycle)

    def _cancel_finalize_won(
        self,
        snapshot: _OperationSnapshot,
    ) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, snapshot.operation_id)
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=snapshot.document_id,
                    document_version=snapshot.document_version,
                    for_update=True,
                )
                state = DocumentLifecycleState(lifecycle.state)
                if state is DocumentLifecycleState.CANCEL_PENDING:
                    lifecycle = self._lifecycle.transition(
                        session,
                        document_id=lifecycle.document_id,
                        document_version=lifecycle.document_version,
                        target=DocumentLifecycleState.DELETE_PENDING,
                    )
                elif state not in {
                    DocumentLifecycleState.DELETE_PENDING,
                    DocumentLifecycleState.DELETED,
                }:
                    raise LifecycleSemanticConflict(
                        f"finalize-wins cancel cannot continue from {state.value}"
                    )
                cancel_request_id = operation.producer_request_id

        delete_request_id = self._cancel_delete_request_id(cancel_request_id)
        try:
            delete_outcome = self.request_delete(
                producer_request_id=delete_request_id,
                document_id=snapshot.document_id,
                document_version=snapshot.document_version,
                reason="cancel reconciliation: finalize completed before abort",
            )
        except LifecycleRecoveryPending:
            self._schedule_operation_retry(
                snapshot.operation_id,
                error_code="CANCEL_DELETE_PENDING",
                error_message="finalize won; derived delete still reconciling",
            )
            raise

        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, snapshot.operation_id)
                self._operations.complete(session, operation)
        return LifecycleRequestOutcome(
            operation_id=snapshot.operation_id,
            document_id=delete_outcome.document_id,
            document_version=delete_outcome.document_version,
            lifecycle_state=delete_outcome.lifecycle_state,
            job_id=delete_outcome.job_id,
        )

    def _finish_confirmed_delete(self, operation_id: UUID) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                if operation.document_version is None:
                    raise LifecycleIntegrityError("DELETE operation is missing version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                if DocumentLifecycleState(lifecycle.state) is not DocumentLifecycleState.DELETED:
                    lifecycle = self._lifecycle.transition(
                        session,
                        document_id=lifecycle.document_id,
                        document_version=lifecycle.document_version,
                        target=DocumentLifecycleState.DELETED,
                    )
                self._operations.complete(session, operation)
                return self._outcome(operation, lifecycle)

    def _finish_failed_delete(
        self,
        operation_id: UUID,
        exc: DeleteReconciliationFailed,
    ) -> LifecycleRequestOutcome:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                if operation.document_version is None:
                    raise LifecycleIntegrityError("DELETE operation is missing version")
                lifecycle = self._require_lifecycle(
                    session,
                    document_id=operation.document_id,
                    document_version=operation.document_version,
                    for_update=True,
                )
                lifecycle = self._lifecycle.transition(
                    session,
                    document_id=lifecycle.document_id,
                    document_version=lifecycle.document_version,
                    target=DocumentLifecycleState.FAILED,
                )
                self._operations.fail(
                    session,
                    operation,
                    error_code="DELETE_FAILED",
                    error_message=str(exc),
                )
                return self._outcome(operation, lifecycle)

    def _schedule_operation_retry(
        self,
        operation_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        with self._session_factory() as session:
            with session.begin():
                operation = self._require_operation(session, operation_id)
                self._operations.schedule_retry(
                    session,
                    operation,
                    delay_seconds=self._retry_delay_seconds,
                    error_code=error_code,
                    error_message=error_message,
                )

    def _assert_version_monotonic(
        self,
        session: Session,
        request: ReindexRequest,
    ) -> None:
        versions = self._lifecycle.list_versions(
            session,
            document_id=request.document_id,
            for_update=True,
        )
        if not versions:
            return
        if any(row.document_version == request.document_version for row in versions):
            return
        max_version = max(row.document_version for row in versions)
        if request.document_version <= max_version:
            raise LifecycleSemanticConflict(
                "reindex must create a new numeric version greater than existing versions"
            )

    @staticmethod
    def _assert_job_matches_reindex(job: IndexationJob, request: ReindexRequest) -> None:
        same = (
            job.document_id == request.document_id
            and job.document_version == request.document_version
            and job.source_uri == request.source_uri
            and job.requested_access_zone_code == request.access_zone_code
            and job.requested_access_zone_id == request.access_zone_id
            and job.requested_ttl_days == request.requested_ttl_days
            and job.source_file_name == request.source_file_name
            and job.storage_object_id == request.storage_object_id
            and job.storage_object_name == request.storage_object_name
            and job.source_content_hash == request.source_content_hash
        )
        if not same:
            raise LifecycleIntegrityError(
                "producer_request_id already belongs to different immutable reindex intent"
            )

    @staticmethod
    def _cancel_job(session: Session, job: IndexationJob) -> None:
        previous = job.status
        now = session.execute(select(func.now())).scalar_one()
        job.status = "CANCELLED"
        job.cancel_requested = True
        job.worker_id = None
        job.lease_acquired_at = None
        job.lease_until = None
        job.last_heartbeat_at = None
        job.updated_at = now
        open_attempts = session.execute(
            select(ProcessingAttempt).where(
                ProcessingAttempt.job_id == job.id,
                ProcessingAttempt.finished_at.is_(None),
            )
        ).scalars()
        for attempt in open_attempts:
            attempt.finished_at = now
            attempt.result = "CANCELLED"
        session.add(
            JobEvent(
                job_id=job.id,
                event_type="JOB_CANCELLED",
                from_status=previous,
                to_status="CANCELLED",
                details={"source": "M9_DOCUMENT_LIFECYCLE"},
            )
        )

    def _find_reindex_operation(
        self,
        session: Session,
        job_id: UUID,
    ) -> LifecycleOperation | None:
        return session.execute(
            select(LifecycleOperation)
            .where(
                LifecycleOperation.job_id == job_id,
                LifecycleOperation.operation_type == LifecycleOperationType.REINDEX.value,
            )
            .order_by(LifecycleOperation.created_at.desc())
            .with_for_update()
            .limit(1)
        ).scalar_one_or_none()

    def _snapshot(
        self,
        operation: LifecycleOperation,
        lifecycle,
        checkpoint: DeliveryCheckpoint | None,
        reason: str,
    ) -> _OperationSnapshot:
        if operation.document_version is None or operation.job_id is None:
            raise LifecycleIntegrityError("lifecycle operation is missing job/version")
        resolved_zone_id = lifecycle.resolved_access_zone_id
        if resolved_zone_id is None and checkpoint is not None:
            resolved_zone_id = checkpoint.resolved_access_zone_id
        return _OperationSnapshot(
            operation_id=operation.id,
            producer_request_id=operation.producer_request_id,
            document_id=operation.document_id,
            document_version=operation.document_version,
            job_id=operation.job_id,
            reason=reason,
            state=DocumentLifecycleState(lifecycle.state),
            ingestion_session_id=(
                checkpoint.ingestion_session_id
                if checkpoint is not None
                else None
            ),
            resolved_access_zone_id=resolved_zone_id,
        )

    @staticmethod
    def _require_operation_type(
        operation: LifecycleOperation,
        expected: LifecycleOperationType,
    ) -> None:
        if operation.operation_type != expected.value:
            raise LifecycleIntegrityError(
                f"operation is {operation.operation_type}, expected {expected.value}"
            )

    @staticmethod
    def _require_job(session: Session, job_id: UUID) -> IndexationJob:
        job = session.get(IndexationJob, job_id)
        if job is None:
            raise LifecycleIntegrityError("IndexationJob does not exist")
        return job

    def _require_lifecycle(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
        for_update: bool,
    ):
        lifecycle = self._lifecycle.get_version(
            session,
            document_id=document_id,
            document_version=document_version,
            for_update=for_update,
        )
        if lifecycle is None:
            raise LifecycleIntegrityError("document lifecycle row does not exist")
        return lifecycle

    def _require_operation(
        self,
        session: Session,
        operation_id: UUID,
    ) -> LifecycleOperation:
        operation = self._operations.get(session, operation_id)
        if operation is None:
            raise LifecycleIntegrityError("lifecycle operation does not exist")
        return operation

    @staticmethod
    def _outcome(
        operation: LifecycleOperation,
        lifecycle,
    ) -> LifecycleRequestOutcome:
        return LifecycleRequestOutcome(
            operation_id=operation.id,
            document_id=lifecycle.document_id,
            document_version=lifecycle.document_version,
            lifecycle_state=DocumentLifecycleState(lifecycle.state),
            job_id=lifecycle.job_id,
        )

    @staticmethod
    def _cancel_delete_request_id(cancel_request_id: UUID) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"astra-indexator:m9:cancel-delete:{cancel_request_id}",
        )
