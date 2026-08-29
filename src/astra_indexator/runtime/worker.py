from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from types import FrameType
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryInput,
)
from astra_indexator.application.coordinator import ClaimedJob, JobCoordinator
from astra_indexator.application.delivery_execution import DeliveryExecutionResult
from astra_indexator.application.retry_policy import (
    DurableFailureHandler,
    FailureClass,
)


class JobPayloadProvider(Protocol):
    def payload_for(self, session: Session, claimed: ClaimedJob) -> AstraVectorDeliveryInput: ...


class DeliveryExecutor(Protocol):
    def execute(
        self, claimed: ClaimedJob, payload: AstraVectorDeliveryInput
    ) -> DeliveryExecutionResult: ...


class UnsupportedRuntimePath(RuntimeError):
    """Raised when the worker claims a job whose production path is not wired yet."""


class PreparedArtifactRuntimePayloadProvider:
    """Placeholder for the M7 replay composition boundary.

    The current repository has M7 replay primitives but no documented production object-store
    configuration tying a claimed inbox job to a prepared artifact. Failing through the durable
    handler keeps the runtime honest without inventing an in-memory or mock processing path.
    """

    def payload_for(self, session: Session, claimed: ClaimedJob) -> AstraVectorDeliveryInput:
        raise UnsupportedRuntimePath(
            "no production M7 prepared-artifact runtime payload provider is configured"
        )


class ShutdownController:
    def __init__(self) -> None:
        self._requested = False

    @property
    def requested(self) -> bool:
        return self._requested

    def request_shutdown(self) -> None:
        self._requested = True

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self.request_shutdown()


@dataclass(slots=True)
class RuntimeWorker:
    session_factory: sessionmaker
    coordinator: JobCoordinator
    payload_provider: JobPayloadProvider
    delivery_executor: DeliveryExecutor
    failure_handler: DurableFailureHandler
    worker_id: str
    lease_seconds: int
    poll_interval_seconds: float
    shutdown: ShutdownController
    logger: logging.Logger
    max_iterations: int | None = None

    def run(self) -> None:
        iterations = 0
        self.logger.info("worker loop started", extra={"worker_id": self.worker_id})
        while not self.shutdown.requested:
            if self.max_iterations is not None and iterations >= self.max_iterations:
                return
            iterations += 1
            claimed = self._claim_next()
            if claimed is None:
                self.logger.debug("no job available; sleeping", extra={"worker_id": self.worker_id})
                self._sleep()
                continue
            self._execute_claimed(claimed)

    def _claim_next(self) -> ClaimedJob | None:
        with self.session_factory() as session:
            with session.begin():
                claimed = self.coordinator.claim_next(
                    session,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if claimed is not None:
                    self.logger.info(
                        "claimed job",
                        extra={
                            "worker_id": self.worker_id,
                            "job_id": str(claimed.token.job_id),
                            "lease_generation": claimed.token.lease_generation,
                        },
                    )
                return claimed

    def _execute_claimed(self, claimed: ClaimedJob) -> None:
        try:
            with self.session_factory() as session:
                payload = self.payload_provider.payload_for(session, claimed)
        except Exception as exc:
            decision = self.failure_handler.handle(
                claimed.token,
                failure_class=FailureClass.PERMANENT_INPUT,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            self.logger.warning(
                "job failed before delivery",
                extra={
                    "worker_id": self.worker_id,
                    "job_id": str(claimed.token.job_id),
                    "failure_action": decision.action.value,
                    "error_code": decision.error_code,
                },
            )
            return

        result = self.delivery_executor.execute(claimed, payload)
        if result.succeeded:
            self.logger.info(
                "job delivery completed",
                extra={"worker_id": self.worker_id, "job_id": str(claimed.token.job_id)},
            )
            return
        assert result.failure is not None
        self.logger.warning(
            "job delivery failure decision",
            extra={
                "worker_id": self.worker_id,
                "job_id": str(claimed.token.job_id),
                "failure_action": result.failure.action.value,
                "error_code": result.failure.error_code,
            },
        )

    def _sleep(self) -> None:
        deadline = time.monotonic() + self.poll_interval_seconds
        while not self.shutdown.requested and time.monotonic() < deadline:
            time.sleep(min(0.1, deadline - time.monotonic()))
