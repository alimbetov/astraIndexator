from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application import JobCoordinator, LeaseLostError
from astra_indexator.persistence.models import IndexationJob, ProcessingAttempt
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
        command.downgrade(cfg, "base")


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


def _enqueue(engine, count: int) -> list:
    repo = IndexationJobRepository()
    ids = []
    with Session(engine) as session:
        for idx in range(count):
            job = repo.create_or_get(
                session,
                NewIndexationJob(
                    producer_request_id=uuid4(),
                    document_id=uuid4(),
                    document_version=1,
                    access_zone_code="0600",
                    knowledge_type="TECHNICAL",
                    source_uri=f"seaweed://sources/m2-{idx}.txt",
                ),
            )
            ids.append(job.id)
        session.commit()
    return ids


def test_three_replicas_claim_distinct_jobs(database_url: str) -> None:
    engine = create_engine(database_url)
    expected = set(_enqueue(engine, 3))
    barrier = Barrier(3)

    def claim(worker_id: str):
        with Session(engine) as session:
            barrier.wait()
            claimed = JobCoordinator().claim_next(session, worker_id=worker_id, lease_seconds=30)
            session.commit()
            return None if claimed is None else claimed.token.job_id

    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed_ids = list(pool.map(claim, ["indexator-1", "indexator-2", "indexator-3"]))

    assert None not in claimed_ids
    assert set(claimed_ids) == expected
    assert len(set(claimed_ids)) == 3
    engine.dispose()


def test_one_job_is_claimed_by_only_one_replica(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    barrier = Barrier(3)

    def claim(worker_id: str):
        with Session(engine) as session:
            barrier.wait()
            claimed = JobCoordinator().claim_next(session, worker_id=worker_id, lease_seconds=30)
            session.commit()
            return None if claimed is None else claimed.token.job_id

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(claim, ["indexator-1", "indexator-2", "indexator-3"]))

    assert results.count(job_id) == 1
    assert results.count(None) == 2
    engine.dispose()


def test_heartbeat_renews_current_lease(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    coordinator = JobCoordinator()

    with Session(engine) as session:
        claimed = coordinator.claim_next(session, worker_id="heartbeat-worker", lease_seconds=30)
        assert claimed is not None
        session.commit()
        first_until = session.get(IndexationJob, job_id).lease_until

    with Session(engine) as session:
        coordinator.heartbeat(session, claimed.token, lease_seconds=120)
        session.commit()
        renewed = session.get(IndexationJob, job_id)
        assert renewed.lease_until > first_until
        assert renewed.last_heartbeat_at is not None
    engine.dispose()


def test_expired_lease_is_reclaimed_and_generation_increments(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    coordinator = JobCoordinator()

    with Session(engine) as session:
        first = coordinator.claim_next(session, worker_id="worker-a", lease_seconds=30)
        assert first is not None
        session.commit()

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() - interval '1 second' WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )

    with Session(engine) as session:
        second = coordinator.claim_next(session, worker_id="worker-b", lease_seconds=30)
        assert second is not None
        session.commit()

    assert second.token.job_id == first.token.job_id
    assert second.token.lease_generation == first.token.lease_generation + 1

    with Session(engine) as session:
        with pytest.raises(LeaseLostError):
            coordinator.heartbeat(session, first.token, lease_seconds=30)
        session.rollback()

    with Session(engine) as session:
        attempts = session.execute(
            select(ProcessingAttempt)
            .where(ProcessingAttempt.job_id == job_id)
            .order_by(ProcessingAttempt.attempt_number)
        ).scalars().all()
        assert len(attempts) == 2
        assert attempts[0].result == "LEASE_EXPIRED"
        assert attempts[1].lease_generation == second.token.lease_generation
    engine.dispose()


def test_expired_worker_cannot_complete_before_reclaim(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    coordinator = JobCoordinator()

    with Session(engine) as session:
        claimed = coordinator.claim_next(session, worker_id="expiring-worker", lease_seconds=30)
        assert claimed is not None
        session.commit()

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() - interval '1 second' WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )

    with Session(engine) as session:
        with pytest.raises(LeaseLostError):
            coordinator.complete(session, claimed.token)
        session.rollback()

    with Session(engine) as session:
        job = session.get(IndexationJob, job_id)
        assert job.status == "PROCESSING"
        assert job.completed_at is None
    engine.dispose()


def test_stage_and_completion_are_fenced(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    coordinator = JobCoordinator()

    with Session(engine) as session:
        claimed = coordinator.claim_next(session, worker_id="owner", lease_seconds=30)
        assert claimed is not None
        coordinator.advance_stage(session, claimed.token, stage="ACQUIRING")
        coordinator.complete(session, claimed.token)
        session.commit()

    with Session(engine) as session:
        job = session.get(IndexationJob, job_id)
        assert job.status == "COMPLETED"
        assert job.processing_stage == "ACQUIRING"
        assert job.worker_id is None
        assert job.lease_until is None
    engine.dispose()


def test_retry_wait_is_not_claimed_before_due_time(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine, 1)[0]
    coordinator = JobCoordinator()

    with Session(engine) as session:
        claimed = coordinator.claim_next(session, worker_id="retry-owner", lease_seconds=30)
        assert claimed is not None
        coordinator.schedule_retry(
            session,
            claimed.token,
            retry_after_seconds=120,
            error_code="TEMPORARY",
            error_message="retry later",
        )
        session.commit()

    with Session(engine) as session:
        assert coordinator.claim_next(session, worker_id="other-worker", lease_seconds=30) is None
        job = session.get(IndexationJob, job_id)
        assert job.status == "RETRY_WAIT"
        assert job.next_retry_at is not None
    engine.dispose()
