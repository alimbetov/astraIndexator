from __future__ import annotations

from uuid import UUID

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryInput,
    DeliveryRecoveryContractGap,
)
from astra_indexator.application.coordinator import ClaimedJob, LeaseLostError, LeaseToken
from astra_indexator.application.delivery_execution import AstraVectorDeliveryExecutor
from astra_indexator.application.retry_policy import (
    FailureAction,
    FailureClass,
    FailureDecision,
)

JOB_ID = UUID("11111111-1111-1111-1111-111111111111")
ATTEMPT_ID = UUID("22222222-2222-2222-2222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-3333-3333-333333333333")


def _claimed() -> ClaimedJob:
    return ClaimedJob(
        token=LeaseToken(
            job_id=JOB_ID,
            worker_id="worker-a",
            lease_generation=3,
            attempt_id=ATTEMPT_ID,
        ),
        document_id=DOCUMENT_ID,
        document_version=1,
        access_zone_code="0001",
        source_uri="seaweed://document",
        processing_stage="ASTRAVECTOR_FINALIZE",
    )


class _FailingCoordinator:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def deliver(self, claimed, payload):
        raise self.exc


class _RecordingFailureHandler:
    def __init__(
        self,
        *,
        classified: FailureClass = FailureClass.INTERNAL_BUG,
        action: FailureAction = FailureAction.FAILED,
        raise_lease_lost: bool = False,
    ) -> None:
        self.classified = classified
        self.action = action
        self.raise_lease_lost = raise_lease_lost
        self.handled: list[FailureClass] = []
        self.classified_exceptions: list[BaseException] = []

    def classify_exception(self, exc: BaseException):
        self.classified_exceptions.append(exc)
        return self.classified, type(exc).__name__, str(exc)

    def handle(self, token, *, failure_class, error_code, error_message):
        self.handled.append(failure_class)
        if self.raise_lease_lost:
            raise LeaseLostError("lease expired during failure persistence")
        return FailureDecision(
            failure_class=failure_class,
            action=self.action,
            error_code=error_code,
            error_message=error_message,
        )


def _payload() -> AstraVectorDeliveryInput:
    return AstraVectorDeliveryInput(logical_blocks=())


def test_ownership_loss_abandons_without_failure_handler_mutation() -> None:
    handler = _RecordingFailureHandler()
    executor = AstraVectorDeliveryExecutor(  # type: ignore[arg-type]
        _FailingCoordinator(LeaseLostError("stale lease")),
        handler,
    )

    result = executor.execute(_claimed(), _payload())

    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.OWNERSHIP_LOST
    assert result.failure.action is FailureAction.ABANDON
    assert handler.handled == []


def test_ambiguous_finalize_enters_reconcile_not_blind_retry() -> None:
    handler = _RecordingFailureHandler(action=FailureAction.RECONCILE)
    executor = AstraVectorDeliveryExecutor(  # type: ignore[arg-type]
        _FailingCoordinator(
            DeliveryRecoveryContractGap("completed session has no recoverable UUID")
        ),
        handler,
    )

    result = executor.execute(_claimed(), _payload())

    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.DOWNSTREAM_AMBIGUOUS
    assert result.failure.action is FailureAction.RECONCILE
    assert handler.handled == [FailureClass.DOWNSTREAM_AMBIGUOUS]


def test_unknown_exception_fails_closed_through_classifier() -> None:
    handler = _RecordingFailureHandler(
        classified=FailureClass.INTERNAL_BUG,
        action=FailureAction.FAILED,
    )
    error = RuntimeError("unexpected implementation defect")
    executor = AstraVectorDeliveryExecutor(  # type: ignore[arg-type]
        _FailingCoordinator(error),
        handler,
    )

    result = executor.execute(_claimed(), _payload())

    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.INTERNAL_BUG
    assert result.failure.action is FailureAction.FAILED
    assert handler.classified_exceptions == [error]
    assert handler.handled == [FailureClass.INTERNAL_BUG]


def test_lease_loss_during_failure_persistence_converts_to_abandon() -> None:
    handler = _RecordingFailureHandler(
        classified=FailureClass.TRANSIENT,
        action=FailureAction.RETRY_WAIT,
        raise_lease_lost=True,
    )
    executor = AstraVectorDeliveryExecutor(  # type: ignore[arg-type]
        _FailingCoordinator(RuntimeError("temporary failure")),
        handler,
    )

    result = executor.execute(_claimed(), _payload())

    assert result.failure is not None
    assert result.failure.failure_class is FailureClass.OWNERSHIP_LOST
    assert result.failure.action is FailureAction.ABANDON
    assert handler.handled == [FailureClass.TRANSIENT]
