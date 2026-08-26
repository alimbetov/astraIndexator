from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.persistence.lifecycle_models import (
    DocumentVersionLifecycle,
    LifecycleOperation,
)
from astra_indexator.persistence.models import IndexationJob

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


def _job(*, document_id: UUID, version: int, code: str = "0001") -> IndexationJob:
    return IndexationJob(
        id=uuid4(),
        producer_request_id=uuid4(),
        document_id=document_id,
        document_version=version,
        access_zone_code=code,
        requested_access_zone_code=code,
        source_uri=f"seaweed://sources/{document_id}/{version}.pdf",
        status="COMPLETED",
    )


def test_m9_migration_creates_lifecycle_tables(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='astra_indexator'"
                )
            ).scalars()
        )

    assert {"document_version_lifecycle", "lifecycle_operation"}.issubset(tables)


def test_lifecycle_preserves_leading_zero_zone_and_ttl(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id = uuid4()
    job = _job(document_id=document_id, version=1, code="0001")

    with Session(engine) as session:
        session.add(job)
        session.flush()
        session.add(
            DocumentVersionLifecycle(
                document_id=document_id,
                document_version=1,
                job_id=job.id,
                state="BUILDING",
                is_current=False,
                requested_access_zone_code="0001",
                requested_ttl_days=0,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.execute(
            select(DocumentVersionLifecycle).where(
                DocumentVersionLifecycle.document_id == document_id,
                DocumentVersionLifecycle.document_version == 1,
            )
        ).scalar_one()
        assert row.requested_access_zone_code == "0001"
        assert row.requested_ttl_days == 0


def test_postgres_allows_only_one_current_active_version(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id = uuid4()
    first_job = _job(document_id=document_id, version=1)
    second_job = _job(document_id=document_id, version=2)

    with Session(engine) as session:
        session.add_all([first_job, second_job])
        session.flush()
        session.add_all(
            [
                DocumentVersionLifecycle(
                    document_id=document_id,
                    document_version=1,
                    job_id=first_job.id,
                    state="ACTIVE",
                    is_current=True,
                    requested_access_zone_code="0001",
                ),
                DocumentVersionLifecycle(
                    document_id=document_id,
                    document_version=2,
                    job_id=second_job.id,
                    state="ACTIVE",
                    is_current=True,
                    requested_access_zone_code="0001",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_postgres_rejects_state_current_mismatch_and_negative_ttl(database_url: str) -> None:
    engine = create_engine(database_url)

    for state, is_current, ttl in [
        ("ACTIVE", False, 0),
        ("READY", True, 0),
        ("BUILDING", False, -1),
    ]:
        document_id = uuid4()
        job = _job(document_id=document_id, version=1)
        with Session(engine) as session:
            session.add(job)
            session.flush()
            session.add(
                DocumentVersionLifecycle(
                    document_id=document_id,
                    document_version=1,
                    job_id=job.id,
                    state=state,
                    is_current=is_current,
                    requested_access_zone_code="0001",
                    requested_ttl_days=ttl,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_lifecycle_operation_idempotency_and_version_constraints(database_url: str) -> None:
    engine = create_engine(database_url)
    producer_request_id = uuid4()
    document_id = uuid4()

    with Session(engine) as session:
        session.add(
            LifecycleOperation(
                id=uuid4(),
                producer_request_id=producer_request_id,
                operation_type="REINDEX",
                document_id=document_id,
                document_version=2,
                status="PENDING",
            )
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            LifecycleOperation(
                id=uuid4(),
                producer_request_id=producer_request_id,
                operation_type="REINDEX",
                document_id=document_id,
                document_version=3,
                status="PENDING",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(
            LifecycleOperation(
                id=uuid4(),
                producer_request_id=uuid4(),
                operation_type="DELETE",
                document_id=document_id,
                document_version=0,
                status="PENDING",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
