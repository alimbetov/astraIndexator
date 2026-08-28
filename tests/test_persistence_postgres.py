from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.persistence.models import IndexationJob
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


def test_schema_is_created_in_dedicated_namespace(database_url: str) -> None:
    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema="astra_indexator"))
    assert {
        "indexation_job",
        "processing_attempt",
        "delivery_checkpoint",
        "delivery_batch",
        "job_event",
        "knowledge_inventory",
        "prepared_artifact_checkpoint",
    }.issubset(tables)


def test_producer_request_is_idempotent(database_url: str) -> None:
    engine = create_engine(database_url)
    repo = IndexationJobRepository()
    producer_request_id = uuid4()
    command_data = NewIndexationJob(
        producer_request_id=producer_request_id,
        document_id=uuid4(),
        document_version=1,
        access_zone_code="0600",
        knowledge_type="TECHNICAL",
        source_uri="seaweed://sources/doc.pdf",
    )

    with Session(engine) as session:
        first = repo.create_or_get(session, command_data)
        session.commit()
        first_id = first.id

    with Session(engine) as session:
        second = repo.create_or_get(session, command_data)
        session.commit()
        assert second.id == first_id
        assert second.producer_request_id == producer_request_id


def test_leading_zero_access_zone_survives_round_trip(database_url: str) -> None:
    engine = create_engine(database_url)
    job = IndexationJob(
        id=uuid4(),
        producer_request_id=uuid4(),
        document_id=uuid4(),
        document_version=1,
        access_zone_code="0000",
        source_uri="seaweed://sources/general.txt",
        status="PENDING",
    )
    with Session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.access_zone_code == "0000"


def test_database_rejects_non_positive_document_version(database_url: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=0,
                access_zone_code="0100",
                source_uri="seaweed://sources/bad.txt",
                status="PENDING",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_rejects_invalid_access_zone_code(database_url: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                access_zone_code="600",
                source_uri="seaweed://sources/bad-zone.txt",
                status="PENDING",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_active_document_version_is_unique_per_zone(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id = uuid4()
    common = dict(
        document_id=document_id,
        document_version=1,
        access_zone_code="0600",
        source_uri="seaweed://sources/duplicate.txt",
        status="PENDING",
    )
    with Session(engine) as session:
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                **common,
            )
        )
        session.commit()
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                **common,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_completed_document_version_allows_new_job(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id = uuid4()
    with Session(engine) as session:
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                access_zone_code="0600",
                source_uri="seaweed://sources/completed.txt",
                status="COMPLETED",
            )
        )
        session.commit()
        session.add(
            IndexationJob(
                id=uuid4(),
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                access_zone_code="0600",
                source_uri="seaweed://sources/new.txt",
                status="PENDING",
            )
        )
        session.commit()


def test_postgres_time_is_available_for_coordination(database_url: str) -> None:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT now() IS NOT NULL")).scalar_one() is True
