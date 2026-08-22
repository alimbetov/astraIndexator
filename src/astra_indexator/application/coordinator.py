from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from astra_indexator.persistence.models import IndexationJob, JobEvent, ProcessingAttempt


class LeaseLostError(RuntimeError):
    """Raised when a worker tries to mutate state with a stale or expired lease token."""


@dataclass(frozen=True, slots=True)
class LeaseToken:
    job_id: UUID
    worker_id: str
    lease_generation: int
    attempt_id: UUID


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    token: LeaseToken
    document_id: UUID
    document_version: int
    access_zone_code: str
    source_uri: str
    processing_stage: str | None


class JobCoordinator:
    """M2 coordinator implementing claim/lease/heartbeat/fencing semantics.

    PostgreSQL `now()` is the time authority. Every authoritative worker mutation
    is fenced by `(job_id, worker_id, lease_generation, non-expired lease)`.
    """

    def claim_next(self, session: Session, *, worker_id: str, lease_seconds: int) -> ClaimedJob | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        now = func.now()
        runnable_state = or_(
            IndexationJob.status == "PENDING",
            and_(
                IndexationJob.status == "RETRY_WAIT",
                or_(IndexationJob.next_retry_at.is_(None), IndexationJob.next_retry_at <= now),
            ),
            and_(
                IndexationJob.status == "PROCESSING",
                IndexationJob.lease_until.is_not(None),
                IndexationJob.lease_until < now,
            ),
        )

        job = session.execute(
            select(IndexationJob)
            .where(
                runnable_state,
                IndexationJob.cancel_requested.is_(False),
                IndexationJob.attempt_count < IndexationJob.max_attempts,
            )
            .order_by(IndexationJob.priority.desc(), IndexationJob.created_at, IndexationJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()

        if job is None:
            return None

        previous_status = job.status
        previous_generation = job.lease_generation
        new_generation = previous_generation + 1
        attempt_number = job.attempt_count + 1
        attempt_id = uuid4()

        if previous_status == "PROCESSING":
            previous_attempt = session.execute(
                select(ProcessingAttempt)
                .where(
                    ProcessingAttempt.job_id == job.id,
                    ProcessingAttempt.finished_at.is_(None),
                )
                .order_by(ProcessingAttempt.attempt_number.desc())
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if previous_attempt is not None:
                previous_attempt.finished_at = func.now()
                previous_attempt.result = "LEASE_EXPIRED"

        job.status = "PROCESSING"
        job.worker_id = worker_id
        job.lease_generation = new_generation
        job.lease_acquired_at = func.now()
        job.lease_until = func.now() + timedelta(seconds=lease_seconds)
        job.last_heartbeat_at = func.now()
        job.attempt_count = attempt_number
        job.next_retry_at = None
        job.updated_at = func.now()

        attempt = ProcessingAttempt(
            id=attempt_id,
            job_id=job.id,
            attempt_number=attempt_number,
            lease_generation=new_generation,
            worker_id=worker_id,
            started_stage=job.processing_stage,
            processing_fingerprint=job.processing_fingerprint,
        )
        session.add(attempt)
        # `job_event.attempt_id` is a real FK. Persist the attempt before its
        # audit event so ordering is deterministic even without ORM relationships.
        session.flush([attempt])

        session.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt_id,
                event_type="JOB_RECLAIMED" if previous_status == "PROCESSING" else "JOB_CLAIMED",
                from_status=previous_status,
                to_status="PROCESSING",
                processing_stage=job.processing_stage,
                lease_generation=new_generation,
                details={"workerId": worker_id, "previousLeaseGeneration": previous_generation},
            )
        )
        session.flush()

        return ClaimedJob(
            token=LeaseToken(job.id, worker_id, new_generation, attempt_id),
            document_id=job.document_id,
            document_version=job.document_version,
            access_zone_code=job.access_zone_code,
            source_uri=job.source_uri,
            processing_stage=job.processing_stage,
        )

    def heartbeat(self, session: Session, token: LeaseToken, *, lease_seconds: int) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        stmt = (
            update(IndexationJob)
            .where(*self._fencing_predicate(token), IndexationJob.status == "PROCESSING")
            .values(
                last_heartbeat_at=func.now(),
                lease_until=func.now() + timedelta(seconds=lease_seconds),
                updated_at=func.now(),
            )
        )
        self._require_one(session, stmt)

    def advance_stage(self, session: Session, token: LeaseToken, *, stage: str) -> None:
        if not stage.strip():
            raise ValueError("stage must not be blank")
        stmt = (
            update(IndexationJob)
            .where(*self._fencing_predicate(token), IndexationJob.status == "PROCESSING")
            .values(processing_stage=stage, updated_at=func.now())
        )
        self._require_one(session, stmt)
        session.add(
            JobEvent(
                job_id=token.job_id,
                attempt_id=token.attempt_id,
                event_type="PROCESSING_STAGE_CHANGED",
                processing_stage=stage,
                lease_generation=token.lease_generation,
                details={"workerId": token.worker_id},
            )
        )

    def complete(self, session: Session, token: LeaseToken) -> None:
        stmt = (
            update(IndexationJob)
            .where(*self._fencing_predicate(token), IndexationJob.status == "PROCESSING")
            .values(
                status="COMPLETED",
                worker_id=None,
                lease_acquired_at=None,
                lease_until=None,
                last_heartbeat_at=None,
                completed_at=func.now(),
                updated_at=func.now(),
            )
        )
        self._require_one(session, stmt)
        self._finish_attempt(session, token, result="COMPLETED")
        session.add(
            JobEvent(
                job_id=token.job_id,
                attempt_id=token.attempt_id,
                event_type="JOB_COMPLETED",
                from_status="PROCESSING",
                to_status="COMPLETED",
                lease_generation=token.lease_generation,
                details={"workerId": token.worker_id},
            )
        )

    def schedule_retry(
        self,
        session: Session,
        token: LeaseToken,
        *,
        retry_after_seconds: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be >= 0")
        stmt = (
            update(IndexationJob)
            .where(*self._fencing_predicate(token), IndexationJob.status == "PROCESSING")
            .values(
                status="RETRY_WAIT",
                worker_id=None,
                lease_acquired_at=None,
                lease_until=None,
                last_heartbeat_at=None,
                next_retry_at=func.now() + timedelta(seconds=retry_after_seconds),
                last_error_code=error_code,
                last_error_message=error_message,
                updated_at=func.now(),
            )
        )
        self._require_one(session, stmt)
        self._finish_attempt(session, token, result="RETRY_WAIT", error_code=error_code, error_message=error_message)
        session.add(
            JobEvent(
                job_id=token.job_id,
                attempt_id=token.attempt_id,
                event_type="RETRY_SCHEDULED",
                from_status="PROCESSING",
                to_status="RETRY_WAIT",
                lease_generation=token.lease_generation,
                details={"workerId": token.worker_id, "retryAfterSeconds": retry_after_seconds},
            )
        )

    @staticmethod
    def _fencing_predicate(token: LeaseToken):
        return (
            IndexationJob.id == token.job_id,
            IndexationJob.worker_id == token.worker_id,
            IndexationJob.lease_generation == token.lease_generation,
            IndexationJob.lease_until.is_not(None),
            IndexationJob.lease_until >= func.now(),
        )

    @staticmethod
    def _require_one(session: Session, stmt) -> None:
        result = session.execute(stmt)
        if result.rowcount != 1:
            raise LeaseLostError("lease token is stale, expired, or job is no longer owned by this worker")

    @staticmethod
    def _finish_attempt(
        session: Session,
        token: LeaseToken,
        *,
        result: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        stmt = (
            update(ProcessingAttempt)
            .where(
                ProcessingAttempt.id == token.attempt_id,
                ProcessingAttempt.job_id == token.job_id,
                ProcessingAttempt.lease_generation == token.lease_generation,
                ProcessingAttempt.finished_at.is_(None),
            )
            .values(
                finished_at=func.now(),
                result=result,
                error_code=error_code,
                error_message=error_message,
            )
        )
        update_result = session.execute(stmt)
        if update_result.rowcount != 1:
            raise LeaseLostError("processing attempt is stale or already finished")
