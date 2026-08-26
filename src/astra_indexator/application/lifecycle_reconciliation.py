from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from astra_indexator.application.document_lifecycle import (
    DocumentLifecycleService,
    LifecycleRecoveryPending,
    LifecycleRequestOutcome,
)
from astra_indexator.domain.lifecycle import (
    LifecycleOperationStatus,
    LifecycleOperationType,
)
from astra_indexator.persistence.lifecycle import (
    LifecycleOperationRepository,
)
from astra_indexator.persistence.lifecycle_models import LifecycleOperation


@dataclass(frozen=True, slots=True)
class ClaimedLifecycleOperation:
    operation_id: UUID
    operation_type: LifecycleOperationType
    attempt_count: int


class LifecycleReconciliationRunner:
    """Crash-recoverable M9 operation worker using PostgreSQL time and row locking."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        service: DocumentLifecycleService,
        *,
        operation_repository: LifecycleOperationRepository | None = None,
        operation_lease_seconds: int = 30,
        retry_delay_seconds: int = 5,
    ) -> None:
        if operation_lease_seconds <= 0:
            raise ValueError("operation_lease_seconds must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self._session_factory = session_factory
        self._service = service
        self._operations = operation_repository or LifecycleOperationRepository()
        self._operation_lease_seconds = operation_lease_seconds
        self._retry_delay_seconds = retry_delay_seconds

    def claim_next(self) -> ClaimedLifecycleOperation | None:
        with self._session_factory() as session:
            with session.begin():
                now = session.execute(select(func.now())).scalar_one()
                operation = session.execute(
                    select(LifecycleOperation)
                    .where(
                        or_(
                            LifecycleOperation.status
                            == LifecycleOperationStatus.PENDING.value,
                            and_(
                                LifecycleOperation.status
                                == LifecycleOperationStatus.RETRY_WAIT.value,
                                or_(
                                    LifecycleOperation.next_retry_at.is_(None),
                                    LifecycleOperation.next_retry_at <= now,
                                ),
                            ),
                            and_(
                                LifecycleOperation.status
                                == LifecycleOperationStatus.RUNNING.value,
                                LifecycleOperation.next_retry_at.is_not(None),
                                LifecycleOperation.next_retry_at <= now,
                            ),
                        )
                    )
                    .order_by(LifecycleOperation.created_at, LifecycleOperation.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                ).scalar_one_or_none()
                if operation is None:
                    return None
                operation.status = LifecycleOperationStatus.RUNNING.value
                operation.attempt_count += 1
                operation.updated_at = now
                operation.next_retry_at = now + timedelta(
                    seconds=self._operation_lease_seconds
                )
                session.flush()
                return ClaimedLifecycleOperation(
                    operation_id=operation.id,
                    operation_type=LifecycleOperationType(operation.operation_type),
                    attempt_count=operation.attempt_count,
                )

    def run_once(self) -> LifecycleRequestOutcome | None:
        claimed = self.claim_next()
        if claimed is None:
            return None
        try:
            if claimed.operation_type is LifecycleOperationType.REINDEX:
                return self._service.reconcile_reindex_operation(claimed.operation_id)
            if claimed.operation_type is LifecycleOperationType.CANCEL:
                return self._service.reconcile_cancel_operation(claimed.operation_id)
            if claimed.operation_type is LifecycleOperationType.DELETE:
                return self._service.reconcile_delete_operation(claimed.operation_id)
            if claimed.operation_type is LifecycleOperationType.RECONCILE:
                return self._service.reconcile_projection_operation(claimed.operation_id)
            raise RuntimeError(
                f"unsupported lifecycle operation {claimed.operation_type.value}"
            )
        except LifecycleRecoveryPending:
            # The application service has already persisted RETRY_WAIT and the
            # reconciliation reason. Nothing else is mutated here.
            return None
        except Exception as exc:
            self._schedule_unexpected_retry(claimed.operation_id, exc)
            raise

    def _schedule_unexpected_retry(self, operation_id: UUID, exc: Exception) -> None:
        with self._session_factory() as session:
            with session.begin():
                operation = self._operations.get(session, operation_id)
                if operation is None:
                    return
                if operation.status in {
                    LifecycleOperationStatus.COMPLETED.value,
                    LifecycleOperationStatus.FAILED.value,
                    LifecycleOperationStatus.CANCELLED.value,
                }:
                    return
                self._operations.schedule_retry(
                    session,
                    operation,
                    delay_seconds=self._retry_delay_seconds,
                    error_code="RECONCILIATION_EXCEPTION",
                    error_message=str(exc),
                )
