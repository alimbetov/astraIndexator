from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.document_lifecycle import (
    DocumentLifecycleService,
    ReindexRequest,
)
from astra_indexator.application.lifecycle_reconciliation import (
    LifecycleReconciliationRunner,
)
from astra_indexator.domain.lifecycle import DocumentLifecycleState
from astra_indexator.persistence.lifecycle import DocumentLifecycleRepository
from astra_indexator.persistence.lifecycle_models import (
    DocumentVersionLifecycle,
    LifecycleOperation,
)
from astra_indexator.persistence.models import DeliveryCheckpoint, IndexationJob

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


class _UnusedPort:
    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected AstraVector call: {name}")


def _factory(database_url: str):  # type: ignore[no-untyped-def]
    engine = create_engine(database_url)
    return sessionmaker(engine, expire_on_commit=False)


def _insert_job(
    session: Session,
    *,
    document_id: UUID,
    version: int,
    status: str = "COMPLETED",
) -> IndexationJob:
    job = IndexationJob(
        id=uuid4(),
        producer_request_id=uuid4(),
        document_id=document_id,
        document_version=version,
        access_zone_code="0001",
        requested_access_zone_code="0001",
        source_uri=f"seaweed://documents/{document_id}/{version}.pdf",
        source_file_name="Публичное имя документа.pdf",
        storage_object_id=uuid4(),
        storage_object_name=f"{uuid4()}.pdf",
        status=status,
    )
    session.add(job)
    session.flush()
    return job


def _searchable_checkpoint(session: Session, job_id: UUID) -> None:
    session.add(
        DeliveryCheckpoint(
            job_id=job_id,
            resolved_access_zone_id=uuid4(),
            searchable=True,
            vector_state_raw="ACTIVE",
            expected_bindings=2,
            synced_bindings=2,
        )
    )


def test_atomic_activation_supersedes_previous_version(database_url: str) -> None:
    factory = _factory(database_url)
    document_id = uuid4()
    lifecycle_repo = DocumentLifecycleRepository()

    with factory() as session:
        with session.begin():
            v1_job = _insert_job(session, document_id=document_id, version=1)
            v2_job = _insert_job(session, document_id=document_id, version=2)
            _searchable_checkpoint(session, v1_job.id)
            _searchable_checkpoint(session, v2_job.id)
            v1 = lifecycle_repo.ensure_building_for_job(session, v1_job)
            lifecycle_repo.mark_ready(
                session,
                document_id=document_id,
                document_version=1,
            )
            lifecycle_repo.activate_version(
                session,
                document_id=document_id,
                document_version=1,
            )
            lifecycle_repo.ensure_building_for_job(session, v2_job)
            assert v1.state == "ACTIVE"

    # During v2 build, v1 remains current and searchable.
    with factory() as session:
        v1 = lifecycle_repo.get_version(
            session,
            document_id=document_id,
            document_version=1,
        )
        v2 = lifecycle_repo.get_version(
            session,
            document_id=document_id,
            document_version=2,
        )
        assert v1 is not None and v1.state == "ACTIVE" and v1.is_current
        assert v2 is not None and v2.state == "BUILDING" and not v2.is_current

    with factory() as session:
        with session.begin():
            lifecycle_repo.mark_ready(
                session,
                document_id=document_id,
                document_version=2,
            )
            lifecycle_repo.activate_version(
                session,
                document_id=document_id,
                document_version=2,
            )

    with factory() as session:
        rows = lifecycle_repo.list_versions(session, document_id=document_id)
        assert [(row.document_version, row.state, row.is_current) for row in rows] == [
            (1, "SUPERSEDED", False),
            (2, "ACTIVE", True),
        ]


def test_reindex_request_is_idempotent_and_preserves_provenance(database_url: str) -> None:
    factory = _factory(database_url)
    service = DocumentLifecycleService(factory, _UnusedPort())  # type: ignore[arg-type]
    document_id = uuid4()
    producer_request_id = uuid4()
    storage_id = uuid4()
    request = ReindexRequest(
        producer_request_id=producer_request_id,
        document_id=document_id,
        document_version=1,
        source_uri=f"seaweed://documents/{storage_id}.pdf",
        access_zone_code="0001",
        source_file_name="Регламент 2026.pdf",
        storage_object_id=storage_id,
        storage_object_name=f"{storage_id}.pdf",
    )

    first = service.request_reindex(request)
    second = service.request_reindex(request)

    assert first.operation_id == second.operation_id
    assert first.job_id == second.job_id
    with factory() as session:
        job = session.get(IndexationJob, first.job_id)
        assert job is not None
        assert job.requested_access_zone_code == "0001"
        assert job.source_file_name == "Регламент 2026.pdf"
        assert job.storage_object_id == storage_id
        assert job.storage_object_name == f"{storage_id}.pdf"


def test_failed_candidate_does_not_damage_active_version(database_url: str) -> None:
    factory = _factory(database_url)
    lifecycle_repo = DocumentLifecycleRepository()
    document_id = uuid4()

    with factory() as session:
        with session.begin():
            v1_job = _insert_job(session, document_id=document_id, version=1)
            _searchable_checkpoint(session, v1_job.id)
            lifecycle_repo.ensure_building_for_job(session, v1_job)
            lifecycle_repo.mark_ready(session, document_id=document_id, document_version=1)
            lifecycle_repo.activate_version(
                session,
                document_id=document_id,
                document_version=1,
            )
            v2_job = _insert_job(
                session,
                document_id=document_id,
                version=2,
                status="FAILED",
            )
            lifecycle_repo.ensure_building_for_job(session, v2_job)
            lifecycle_repo.transition(
                session,
                document_id=document_id,
                document_version=2,
                target=DocumentLifecycleState.FAILED,
            )

    with factory() as session:
        v1 = lifecycle_repo.get_version(
            session,
            document_id=document_id,
            document_version=1,
        )
        v2 = lifecycle_repo.get_version(
            session,
            document_id=document_id,
            document_version=2,
        )
        assert v1 is not None and v1.state == "ACTIVE" and v1.is_current
        assert v2 is not None and v2.state == "FAILED" and not v2.is_current


def test_expired_running_lifecycle_operation_is_reclaimable(database_url: str) -> None:
    factory = _factory(database_url)
    service = DocumentLifecycleService(factory, _UnusedPort())  # type: ignore[arg-type]
    request = ReindexRequest(
        producer_request_id=uuid4(),
        document_id=uuid4(),
        document_version=1,
        source_uri="seaweed://documents/reclaim.pdf",
        access_zone_code="0001",
    )
    outcome = service.request_reindex(request)

    with factory() as session:
        with session.begin():
            operation = session.get(LifecycleOperation, outcome.operation_id)
            assert operation is not None
            operation.status = "RUNNING"
            operation.next_retry_at = session.execute(select(func.now())).scalar_one() - timedelta(
                seconds=1
            )

    runner = LifecycleReconciliationRunner(
        factory,
        service,
        operation_lease_seconds=30,
    )
    claimed = runner.claim_next()

    assert claimed is not None
    assert claimed.operation_id == outcome.operation_id
    assert claimed.attempt_count >= 1
    with factory() as session:
        operation = session.get(LifecycleOperation, outcome.operation_id)
        assert operation is not None
        assert operation.status == "RUNNING"
        assert operation.next_retry_at is not None
