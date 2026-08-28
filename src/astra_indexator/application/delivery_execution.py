from __future__ import annotations

from dataclasses import dataclass

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryCoordinator,
    AstraVectorDeliveryInput,
    AstraVectorDeliveryOutcome,
    DeliveryRecoveryContractGap,
)
from astra_indexator.application.coordinator import ClaimedJob, LeaseLostError
from astra_indexator.application.finalize_reconciliation import (
    FinalizeReconciliationPending,
    FinalizeTerminalError,
)
from astra_indexator.application.retry_policy import (
    DurableFailureHandler,
    FailureClass,
    FailureDecision,
)
from astra_indexator.application.vector_readiness import (
    VectorReadinessPending,
    VectorReadinessTerminalError,
)
from astra_indexator.persistence.delivery import DeliveryIntegrityError


@dataclass(frozen=True, slots=True)
class DeliveryExecutionResult:
    """Result of one currently-owned M8 delivery execution turn."""

    delivery: AstraVectorDeliveryOutcome | None = None
    failure: FailureDecision | None = None

    def __post_init__(self) -> None:
        if (self.delivery is None) == (self.failure is None):
            raise ValueError("exactly one of delivery or failure must be present")

    @property
    def succeeded(self) -> bool:
        return self.delivery is not None


class AstraVectorDeliveryExecutor:
    """Production exception boundary for one owned AstraVector delivery turn.

    The coordinator owns protocol execution. ``DurableFailureHandler`` owns durable state
    transitions. This layer never retries a downstream mutation itself.
    """

    def __init__(
        self,
        coordinator: AstraVectorDeliveryCoordinator,
        failure_handler: DurableFailureHandler,
    ) -> None:
        self._coordinator = coordinator
        self._failure_handler = failure_handler

    def execute(
        self,
        claimed: ClaimedJob,
        payload: AstraVectorDeliveryInput,
    ) -> DeliveryExecutionResult:
        try:
            outcome = self._coordinator.deliver(claimed, payload)
        except Exception as exc:
            failure_class, error_code, error_message = self._classify(exc)
            if failure_class is FailureClass.OWNERSHIP_LOST:
                return DeliveryExecutionResult(
                    failure=FailureDecision(
                        failure_class=failure_class,
                        action=self._failure_handler.handle(
                            claimed.token,
                            failure_class=failure_class,
                            error_code=error_code,
                            error_message=error_message,
                        ).action,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )
            try:
                decision = self._failure_handler.handle(
                    claimed.token,
                    failure_class=failure_class,
                    error_code=error_code,
                    error_message=error_message,
                )
            except LeaseLostError as lease_exc:
                decision = FailureDecision(
                    failure_class=FailureClass.OWNERSHIP_LOST,
                    action=self._failure_handler.handle(
                        claimed.token,
                        failure_class=FailureClass.OWNERSHIP_LOST,
                        error_code="OWNERSHIP_LOST",
                        error_message=str(lease_exc),
                    ).action,
                    error_code="OWNERSHIP_LOST",
                    error_message=str(lease_exc),
                )
            return DeliveryExecutionResult(failure=decision)
        return DeliveryExecutionResult(delivery=outcome)

    def _classify(self, exc: Exception) -> tuple[FailureClass, str, str]:
        if isinstance(exc, LeaseLostError):
            return FailureClass.OWNERSHIP_LOST, "OWNERSHIP_LOST", str(exc)
        if isinstance(exc, (DeliveryRecoveryContractGap, FinalizeReconciliationPending)):
            return FailureClass.DOWNSTREAM_AMBIGUOUS, type(exc).__name__, str(exc)
        if isinstance(exc, VectorReadinessPending):
            return FailureClass.TRANSIENT, type(exc).__name__, str(exc)
        if isinstance(
            exc,
            (FinalizeTerminalError, VectorReadinessTerminalError, DeliveryIntegrityError),
        ):
            return FailureClass.PERMANENT_POLICY, type(exc).__name__, str(exc)
        return self._failure_handler.classify_exception(exc)
