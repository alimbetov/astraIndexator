from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astra_indexator.domain.contracts import AccessZoneCode
from astra_indexator.domain.lifecycle import (
    DocumentLifecycleState,
    LifecycleOperationStatus,
    LifecycleOperationType,
    require_lifecycle_transition,
)

from .knowledge_inventory import KnowledgeInventoryRepository
from .lifecycle_models import DocumentVersionLifecycle, LifecycleOperation
from .models import DeliveryCheckpoint, IndexationJob, JobEvent, KnowledgeInventory


class LifecycleIntegrityError(RuntimeError):
    pass


class LifecycleNotFoundError(RuntimeError):
    pass


class LifecycleReadinessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NewLifecycleOperation:
    producer_request_id: UUID
    operation_type: LifecycleOperationType
    document_id: UUID
    document_version: int | None = None
    job_id: UUID | None = None
    requested_access_zone_code: str | None = None
    requested_access_zone_id: UUID | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.document_version is not None and self.document_version <= 0:
            raise ValueError("document_version must be positive")
        if self.requested_access_zone_code is not None:
            AccessZoneCode(self.requested_access_zone_code)
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason must not be blank")


class DocumentLifecycleRepository:
    """Durable document-version lifecycle authority owned by AstraIndexator."""

    def __init__(
        self,
        *,
        inventory_repository: KnowledgeInventoryRepository | None = None,
    ) -> None:
        self._inventory = inventory_repository or KnowledgeInventoryRepository()

    def get_version(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
        for_update: bool = False,
    ) -> DocumentVersionLifecycle | None:
        stmt = select(DocumentVersionLifecycle).where(
            DocumentVersionLifecycle.document_id == document_id,
            DocumentVersionLifecycle.document_version == document_version,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return session.execute(stmt).scalar_one_or_none()

    def list_versions(
        self,
        session: Session,
        *,
        document_id: UUID,
        for_update: bool = False,
    ) -> list[DocumentVersionLifecycle]:
        stmt = (
            select(DocumentVersionLifecycle)
            .where(DocumentVersionLifecycle.document_id == document_id)
            .order_by(DocumentVersionLifecycle.document_version)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return list(session.execute(stmt).scalars())

    def ensure_building_for_job(
        self,
        session: Session,
        job: IndexationJob,
    ) -> DocumentVersionLifecycle:
        existing = self.get_version(
            session,
            document_id=job.document_id,
            document_version=job.document_version,
            for_update=True,
        )
        if existing is not None:
            self._assert_job_identity(existing, job)
            return existing

        row = DocumentVersionLifecycle(
            document_id=job.document_id,
            document_version=job.document_version,
            job_id=job.id,
            state=DocumentLifecycleState.BUILDING.value,
            is_current=False,
            requested_access_zone_code=job.requested_access_zone_code,
            requested_access_zone_id=job.requested_access_zone_id,
            requested_ttl_days=job.requested_ttl_days,
        )
        session.add(row)
        session.flush()
        self._emit_transition_event(
            session,
            row=row,
            previous=None,
            target=DocumentLifecycleState.BUILDING,
        )
        self._inventory.rebuild(
            session,
            document_id=row.document_id,
            document_version=row.document_version,
        )
        return row

    def mark_ready(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVersionLifecycle:
        row = self._require_version(
            session,
            document_id=document_id,
            document_version=document_version,
            for_update=True,
        )
        if DocumentLifecycleState(row.state) is DocumentLifecycleState.READY:
            return row
        checkpoint = session.get(DeliveryCheckpoint, row.job_id)
        if checkpoint is None or checkpoint.searchable is not True:
            raise LifecycleReadinessError(
                "document version cannot become READY without searchable=true evidence"
            )
        if checkpoint.resolved_access_zone_id is not None:
            row.resolved_access_zone_id = checkpoint.resolved_access_zone_id
        self._transition_row(session, row, DocumentLifecycleState.READY)
        self._inventory.rebuild(
            session,
            document_id=document_id,
            document_version=document_version,
        )
        return row

    def activate_version(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVersionLifecycle:
        # Lock both lifecycle and projection rows for the aggregate. The lifecycle
        # unique index is the final guard against two concurrent current versions.
        versions = self.list_versions(session, document_id=document_id, for_update=True)
        session.execute(
            select(KnowledgeInventory)
            .where(KnowledgeInventory.document_id == document_id)
            .with_for_update()
        ).scalars().all()

        candidate = next(
            (row for row in versions if row.document_version == document_version),
            None,
        )
        if candidate is None:
            raise LifecycleNotFoundError("candidate document version does not exist")
        if (
            DocumentLifecycleState(candidate.state) is DocumentLifecycleState.ACTIVE
            and candidate.is_current
        ):
            return candidate
        if DocumentLifecycleState(candidate.state) is not DocumentLifecycleState.READY:
            raise LifecycleReadinessError("only a READY document version may become ACTIVE")

        checkpoint = session.get(DeliveryCheckpoint, candidate.job_id)
        if checkpoint is None or checkpoint.searchable is not True:
            raise LifecycleReadinessError(
                "activation requires persisted AstraVector searchable=true evidence"
            )

        previous_active = [
            row
            for row in versions
            if row.document_version != document_version
            and DocumentLifecycleState(row.state) is DocumentLifecycleState.ACTIVE
            and row.is_current
        ]
        if len(previous_active) > 1:
            raise LifecycleIntegrityError("multiple locally ACTIVE versions already exist")

        for old in previous_active:
            self._transition_row(session, old, DocumentLifecycleState.SUPERSEDED)
        session.flush()

        self._transition_row(session, candidate, DocumentLifecycleState.ACTIVE)
        session.flush()

        for row in [*previous_active, candidate]:
            self._inventory.rebuild(
                session,
                document_id=row.document_id,
                document_version=row.document_version,
            )
        return candidate

    def transition(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
        target: DocumentLifecycleState,
    ) -> DocumentVersionLifecycle:
        row = self._require_version(
            session,
            document_id=document_id,
            document_version=document_version,
            for_update=True,
        )
        if DocumentLifecycleState(row.state) is target:
            return row
        self._transition_row(session, row, target)
        self._inventory.rebuild(
            session,
            document_id=document_id,
            document_version=document_version,
        )
        return row

    def record_resolved_access_zone(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
        resolved_access_zone_id: UUID,
    ) -> DocumentVersionLifecycle:
        row = self._require_version(
            session,
            document_id=document_id,
            document_version=document_version,
            for_update=True,
        )
        if (
            row.resolved_access_zone_id is not None
            and row.resolved_access_zone_id != resolved_access_zone_id
        ):
            raise LifecycleIntegrityError(
                "resolved accessZoneId changed for immutable document version"
            )
        row.resolved_access_zone_id = resolved_access_zone_id
        row.updated_at = self._db_now(session)
        session.flush()
        self._inventory.rebuild(
            session,
            document_id=document_id,
            document_version=document_version,
        )
        return row

    def _transition_row(
        self,
        session: Session,
        row: DocumentVersionLifecycle,
        target: DocumentLifecycleState,
    ) -> None:
        previous = DocumentLifecycleState(row.state)
        require_lifecycle_transition(previous, target)
        now = self._db_now(session)
        row.state = target.value
        row.is_current = target is DocumentLifecycleState.ACTIVE
        row.updated_at = now
        if target is DocumentLifecycleState.READY:
            row.ready_at = now
        elif target is DocumentLifecycleState.ACTIVE:
            row.activated_at = now
        elif target is DocumentLifecycleState.SUPERSEDED:
            row.superseded_at = now
        elif target is DocumentLifecycleState.CANCEL_PENDING:
            row.cancel_requested_at = now
        elif target is DocumentLifecycleState.CANCELLED:
            row.cancelled_at = now
        elif target is DocumentLifecycleState.DELETE_PENDING:
            row.delete_requested_at = now
        elif target is DocumentLifecycleState.DELETED:
            row.deleted_at = now
        elif target is DocumentLifecycleState.FAILED:
            row.failed_at = now
        self._emit_transition_event(session, row=row, previous=previous, target=target)
        session.flush()

    def _emit_transition_event(
        self,
        session: Session,
        *,
        row: DocumentVersionLifecycle,
        previous: DocumentLifecycleState | None,
        target: DocumentLifecycleState,
    ) -> None:
        session.add(
            JobEvent(
                job_id=row.job_id,
                event_type="DOCUMENT_LIFECYCLE_TRANSITION",
                from_status=previous.value if previous is not None else None,
                to_status=target.value,
                details={
                    "document_id": str(row.document_id),
                    "document_version": row.document_version,
                    "from_lifecycle_state": previous.value if previous is not None else None,
                    "to_lifecycle_state": target.value,
                },
            )
        )

    def _require_version(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
        for_update: bool,
    ) -> DocumentVersionLifecycle:
        row = self.get_version(
            session,
            document_id=document_id,
            document_version=document_version,
            for_update=for_update,
        )
        if row is None:
            raise LifecycleNotFoundError("document lifecycle row does not exist")
        return row

    @staticmethod
    def _assert_job_identity(row: DocumentVersionLifecycle, job: IndexationJob) -> None:
        immutable = (
            row.job_id == job.id
            and row.requested_access_zone_code == job.requested_access_zone_code
            and row.requested_access_zone_id == job.requested_access_zone_id
            and row.requested_ttl_days == job.requested_ttl_days
        )
        if not immutable:
            raise LifecycleIntegrityError(
                "persisted lifecycle intent differs from immutable IndexationJob intent"
            )

    @staticmethod
    def _db_now(session: Session):  # type: ignore[no-untyped-def]
        return session.execute(select(func.now())).scalar_one()


class LifecycleOperationRepository:
    """Durable idempotent mutation/reconciliation queue for M9."""

    def create_or_get(
        self,
        session: Session,
        command: NewLifecycleOperation,
    ) -> LifecycleOperation:
        operation_id = uuid4()
        stmt = (
            insert(LifecycleOperation)
            .values(
                id=operation_id,
                producer_request_id=command.producer_request_id,
                operation_type=command.operation_type.value,
                document_id=command.document_id,
                document_version=command.document_version,
                job_id=command.job_id,
                requested_access_zone_code=command.requested_access_zone_code,
                requested_access_zone_id=command.requested_access_zone_id,
                reason=command.reason,
                status=LifecycleOperationStatus.PENDING.value,
            )
            .on_conflict_do_nothing(
                index_elements=[LifecycleOperation.producer_request_id]
            )
            .returning(LifecycleOperation.id)
        )
        inserted = session.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return session.get(LifecycleOperation, inserted)  # type: ignore[return-value]

        existing = session.execute(
            select(LifecycleOperation).where(
                LifecycleOperation.producer_request_id == command.producer_request_id
            )
        ).scalar_one()
        self._assert_same_command(existing, command)
        return existing

    def get(self, session: Session, operation_id: UUID) -> LifecycleOperation | None:
        return session.get(LifecycleOperation, operation_id)

    def claim_next(self, session: Session) -> LifecycleOperation | None:
        now = self._db_now(session)
        stmt = (
            select(LifecycleOperation)
            .where(
                or_(
                    LifecycleOperation.status == LifecycleOperationStatus.PENDING.value,
                    (
                        LifecycleOperation.status
                        == LifecycleOperationStatus.RETRY_WAIT.value
                    )
                    & (LifecycleOperation.next_retry_at <= now),
                )
            )
            .order_by(LifecycleOperation.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        operation = session.execute(stmt).scalar_one_or_none()
        if operation is None:
            return None
        operation.status = LifecycleOperationStatus.RUNNING.value
        operation.attempt_count += 1
        operation.next_retry_at = None
        operation.updated_at = now
        session.flush()
        return operation

    def complete(self, session: Session, operation: LifecycleOperation) -> None:
        now = self._db_now(session)
        operation.status = LifecycleOperationStatus.COMPLETED.value
        operation.next_retry_at = None
        operation.last_error_code = None
        operation.last_error_message = None
        operation.updated_at = now
        operation.completed_at = now
        session.flush()

    def fail(
        self,
        session: Session,
        operation: LifecycleOperation,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        operation.status = LifecycleOperationStatus.FAILED.value
        operation.last_error_code = error_code
        operation.last_error_message = error_message
        operation.next_retry_at = None
        operation.updated_at = self._db_now(session)
        session.flush()

    def schedule_retry(
        self,
        session: Session,
        operation: LifecycleOperation,
        *,
        delay_seconds: int,
        error_code: str,
        error_message: str,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        now = self._db_now(session)
        operation.status = LifecycleOperationStatus.RETRY_WAIT.value
        operation.next_retry_at = now + timedelta(seconds=delay_seconds)
        operation.last_error_code = error_code
        operation.last_error_message = error_message
        operation.updated_at = now
        session.flush()

    @staticmethod
    def _assert_same_command(
        operation: LifecycleOperation,
        command: NewLifecycleOperation,
    ) -> None:
        same = (
            operation.operation_type == command.operation_type.value
            and operation.document_id == command.document_id
            and operation.document_version == command.document_version
            and operation.job_id == command.job_id
            and operation.requested_access_zone_code
            == command.requested_access_zone_code
            and operation.requested_access_zone_id == command.requested_access_zone_id
            and operation.reason == command.reason
        )
        if not same:
            raise LifecycleIntegrityError(
                "producer_request_id already belongs to a different lifecycle operation"
            )

    @staticmethod
    def _db_now(session: Session):  # type: ignore[no-untyped-def]
        return session.execute(select(func.now())).scalar_one()
