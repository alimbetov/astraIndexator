from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.retry_policy import (
    DurableFailureHandler,
    FailureAction,
    FailureClass,
    RetryBackoffConfig,
    RetryBackoffPolicy,
)
from astra_indexator.persistence.models import IndexationJob, JobEvent, ProcessingAttempt
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )


@pytest.fixture(scope="module")
def database_url() -> str:
    with PostgresContainer("postgres:16") as postgres:
        url = _psycopg_url(postgres.get_connection_url())
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


@pytest.fixture(autouse=True)
def clean_database(database_url: str):
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE "
                "astra_indexator.knowledge_inventory, "
                "astra_indexator.job_event, "
                "astra_indexator.delivery_batch, "
                "astra_indexator.delivery_checkpoint, "
                "astra_indexator.processing_attempt, "
                "astra_indexator.indexation_job CASCADE"
            )
        )
    yield
    engine.dispose()


def _claimed(engine, *, max_attempts: int = 3):
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://documents/retry.txt",
                access_zone_code="0600",
                source_content_hash="a" * 64,
            ),
        )
        job.max_attempts = max_attempts
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="worker-a", lease_seconds=120)
        assert claimed is not None
        session.commit()
    return claimed


def test_transient_failure_schedules_retry_from_postgres_time(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claimed(engine, max_attempts=3)
    handler = DurableFailureHandler(
        lambda: Session(engine),
        backoff=RetryBackoffPolicy(
            RetryBackoffConfig(base_delay_seconds=5, max_delay_seconds=60, jitter_ratio=0)
        ),
    )

    decision = handler.handle(
        claimed.token,
        failure_class=FailureClass.DEPENDENCY_UNAVAILABLE,
        error_code="UNAVAILABLE",
        error_message="temporary downstream outage",
    )

    assert decision.action is FailureAction.RETRY_WAIT
    assert decision.retry_after_seconds == 5
    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        assert job is not None
        assert job.status == "RETRY_WAIT"
        assert job.next_retry_at is not None
        assert job.worker_id is None
        attempt = session.get(ProcessingAttempt, claimed.token.attempt_id)
        assert attempt is not None and attempt.result == "RETRY_WAIT"
        events = session.query(JobEvent).filter_by(job_id=claimed.token.job_id).all()
        assert any(event.event_type == "RETRY_SCHEDULED" for event in events)
    engine.dispose()


def test_retry_budget_exhaustion_moves_poison_job_to_dead_letter(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claimed(engine, max_attempts=1)
    handler = DurableFailureHandler(lambda: Session(engine))

    decision = handler.handle(
        claimed.token,
        failure_class=FailureClass.TRANSIENT,
        error_code="TEMPORARY",
        error_message="still failing",
    )

    assert decision.action is FailureAction.DEAD_LETTER
    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        assert job is not None
        assert job.status == "DEAD_LETTER"
        assert job.next_retry_at is None
        assert job.last_error_code == "TEMPORARY"
        attempt = session.get(ProcessingAttempt, claimed.token.attempt_id)
        assert attempt is not None and attempt.result == "DEAD_LETTER"
        event = (
            session.query(JobEvent)
            .filter_by(job_id=claimed.token.job_id, event_type="JOB_DEAD_LETTERED")
            .one()
        )
        assert event.details["failureClass"] == "TRANSIENT"
        assert event.details["attemptCount"] == 1
        assert event.details["maxAttempts"] == 1
    engine.dispose()


def test_permanent_failure_fails_immediately_without_retry(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claimed(engine, max_attempts=8)
    handler = DurableFailureHandler(lambda: Session(engine))

    decision = handler.handle(
        claimed.token,
        failure_class=FailureClass.PERMANENT_INPUT,
        error_code="SOURCE_CONTENT_MISMATCH",
        error_message="source bytes changed under the same job identity",
    )

    assert decision.action is FailureAction.FAILED
    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        assert job is not None
        assert job.status == "FAILED"
        assert job.next_retry_at is None
    engine.dispose()


def test_ambiguous_and_ownership_failures_do_not_mutate_retry_state(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claimed(engine, max_attempts=3)
    handler = DurableFailureHandler(lambda: Session(engine))

    ambiguous = handler.handle(
        claimed.token,
        failure_class=FailureClass.DOWNSTREAM_AMBIGUOUS,
        error_code="DEADLINE_EXCEEDED",
        error_message="append outcome unknown",
    )
    ownership = handler.handle(
        claimed.token,
        failure_class=FailureClass.OWNERSHIP_LOST,
        error_code="OWNERSHIP_LOST",
        error_message="lease expired",
    )

    assert ambiguous.action is FailureAction.RECONCILE
    assert ownership.action is FailureAction.ABANDON
    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        assert job is not None
        assert job.status == "PROCESSING"
        assert job.next_retry_at is None
    engine.dispose()


def test_backoff_is_bounded_and_deterministic() -> None:
    token = type(
        "Token",
        (),
        {
            "job_id": uuid4(),
            "worker_id": "worker-a",
            "lease_generation": 7,
            "attempt_id": uuid4(),
        },
    )()
    policy = RetryBackoffPolicy(
        RetryBackoffConfig(base_delay_seconds=5, max_delay_seconds=30, jitter_ratio=0.2)
    )

    first = policy.delay_seconds(token=token, attempt_count=5)  # type: ignore[arg-type]
    second = policy.delay_seconds(token=token, attempt_count=5)  # type: ignore[arg-type]

    assert first == second
    assert 0 <= first <= 30
