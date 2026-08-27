from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import JobCoordinator, LeaseLostError, LeaseToken
from astra_indexator.astravector.contracts import AstraVectorTransportError
from astra_indexator.astravector.policy import GrpcFailure, RetryDecision, classify_grpc_failure
from astra_indexator.persistence.models import IndexationJob, JobEvent, ProcessingAttempt


class FailureClass(str, Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT_INPUT = "PERMANENT_INPUT"
    PERMANENT_POLICY = "PERMANENT_POLICY"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    DOWNSTREAM_AMBIGUOUS = "DOWNSTREAM_AMBIGUOUS"
    OWNERSHIP_LOST = "OWNERSHIP_LOST"
    CANCELLED = "CANCELLED"
    INTERNAL_BUG = "INTERNAL_BUG"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class FailureAction(str, Enum):
    RETRY_WAIT = "RETRY_WAIT"
    RECONCILE = "RECONCILE"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    ABANDON = "ABANDON"


@dataclass(frozen=True, slots=True)
class RetryBackoffConfig:
    base_delay_seconds: int = 5
    max_delay_seconds: int = 15 * 60
    jitter_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class FailureDecision:
    failure_class: FailureClass
    action: FailureAction
    error_code: str
    error_message: str
    retry_after_seconds: int | None = None


class RetryBackoffPolicy:
    """Bounded exponential backoff with deterministic per-job jitter.

    PostgreSQL remains the scheduling clock. This class computes only a delay; JobCoordinator
    persists ``next_retry_at = PostgreSQL now() + delay``.
    """

    def __init__(self, config: RetryBackoffConfig | None = None) -> None:
        self._config = config or RetryBackoffConfig()

    def delay_seconds(self, *, token: LeaseToken, attempt_count: int) -> int:
        exponent = max(attempt_count - 1, 0)
        raw = min(self._config.max_delay_seconds, self._config.base_delay_seconds * (2**exponent))
        if self._config.jitter_ratio == 0:
            return int(raw)
        digest = hashlib.sha256(
            f"{token.job_id}:{token.lease_generation}:{attempt_count}".encode("utf-8")
        ).digest()
        unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        signed = (unit * 2.0) - 1.0
        jittered = raw * (1.0 + signed * self._config.jitter_ratio)
        return max(0, min(self._config.max_delay_seconds, int(round(jittered))))


class DurableFailureHandler:
    """M8.3.1 stage-aware retry/dead-letter executor.

    Automatic retry is bounded by the job's durable ``max_attempts``. Permanent failures fail
    immediately. Retryable poison jobs become DEAD_LETTER when the current attempt exhausts the
    automatic budget. Ambiguous downstream mutations are never converted into blind retry here;
    callers must enter the qualified reconciliation path first.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        coordinator: JobCoordinator | None = None,
        backoff: RetryBackoffPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._coordinator = coordinator or JobCoordinator()
        self._backoff = backoff or RetryBackoffPolicy()

    def classify_exception(self, exc: BaseException) -> tuple[FailureClass, str, str]:
        if isinstance(exc, LeaseLostError):
            return FailureClass.OWNERSHIP_LOST, "OWNERSHIP_LOST", str(exc)
        if isinstance(exc, AstraVectorTransportError):
            decision = classify_grpc_failure(GrpcFailure(code=exc.code, message=exc.message))
            if decision is RetryDecision.BACKOFF_AND_RETRY:
                return FailureClass.DEPENDENCY_UNAVAILABLE, exc.code, exc.message
            if decision is RetryDecision.RECONCILE_STATUS:
                return FailureClass.DOWNSTREAM_AMBIGUOUS, exc.code, exc.message
            return FailureClass.PERMANENT_POLICY, exc.code, exc.message
        if isinstance(exc, (ValueError, TypeError)):
            return FailureClass.PERMANENT_INPUT, type(exc).__name__, str(exc)
        return FailureClass.INTERNAL_BUG, type(exc).__name__, str(exc)

    def handle(
        self,
        token: LeaseToken,
        *,
        failure_class: FailureClass,
        error_code: str,
        error_message: str,
    ) -> FailureDecision:
        if failure_class is FailureClass.OWNERSHIP_LOST:
            return FailureDecision(
                failure_class=failure_class,
                action=FailureAction.ABANDON,
                error_code=error_code,
                error_message=error_message,
            )
        if failure_class is FailureClass.DOWNSTREAM_AMBIGUOUS:
            return FailureDecision(
                failure_class=failure_class,
                action=FailureAction.RECONCILE,
                error_code=error_code,
                error_message=error_message,
            )

        retryable = failure_class in {
            FailureClass.TRANSIENT,
            FailureClass.DEPENDENCY_UNAVAILABLE,
        }
        with self._session_factory() as session:
            with session.begin():
                job = session.execute(
                    select(IndexationJob)
                    .where(
                        IndexationJob.id == token.job_id,
                        IndexationJob.worker_id == token.worker_id,
                        IndexationJob.lease_generation == token.lease_generation,
                        IndexationJob.status == "PROCESSING",
                        IndexationJob.lease_until.is_not(None),
                        IndexationJob.lease_until >= func.now(),
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if job is None:
                    raise LeaseLostError(
                        "cannot classify durable failure because the processing lease is stale"
                    )

                if retryable and job.attempt_count < job.max_attempts:
                    delay = self._backoff.delay_seconds(
                        token=token, attempt_count=job.attempt_count
                    )
                    self._coordinator.schedule_retry(
                        session,
                        token,
                        retry_after_seconds=delay,
                        error_code=error_code,
                        error_message=error_message,
                    )
                    return FailureDecision(
                        failure_class=failure_class,
                        action=FailureAction.RETRY_WAIT,
                        error_code=error_code,
                        error_message=error_message,
                        retry_after_seconds=delay,
                    )

                terminal = "DEAD_LETTER" if retryable else "FAILED"
                event_type = "JOB_DEAD_LETTERED" if retryable else "JOB_FAILED"
                attempt_result = terminal
                stage = job.processing_stage
                job.status = terminal
                job.worker_id = None
                job.lease_acquired_at = None
                job.lease_until = None
                job.last_heartbeat_at = None
                job.next_retry_at = None
                job.last_error_code = error_code
                job.last_error_message = error_message
                job.updated_at = func.now()

                attempt = session.execute(
                    select(ProcessingAttempt)
                    .where(
                        ProcessingAttempt.id == token.attempt_id,
                        ProcessingAttempt.job_id == token.job_id,
                        ProcessingAttempt.lease_generation == token.lease_generation,
                        ProcessingAttempt.finished_at.is_(None),
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if attempt is None:
                    raise LeaseLostError("processing attempt is stale or already finished")
                attempt.finished_at = func.now()
                attempt.finished_stage = stage
                attempt.result = attempt_result
                attempt.error_code = error_code
                attempt.error_message = error_message

                session.add(
                    JobEvent(
                        job_id=job.id,
                        attempt_id=token.attempt_id,
                        event_type=event_type,
                        from_status="PROCESSING",
                        to_status=terminal,
                        processing_stage=stage,
                        lease_generation=token.lease_generation,
                        details={
                            "workerId": token.worker_id,
                            "failureClass": failure_class.value,
                            "attemptCount": job.attempt_count,
                            "maxAttempts": job.max_attempts,
                            "errorCode": error_code,
                        },
                    )
                )
                return FailureDecision(
                    failure_class=failure_class,
                    action=(
                        FailureAction.DEAD_LETTER if retryable else FailureAction.FAILED
                    ),
                    error_code=error_code,
                    error_message=error_message,
                )
