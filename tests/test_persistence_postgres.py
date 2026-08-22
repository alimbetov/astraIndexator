from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
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


def test_initial_migration_creates_foundational_tables(database_url: str) -> None:
    engine = create_engine(database_url)
    expected = {
        "indexation_job",
        "processing_attempt",
        "delivery_checkpoint",
        "delivery_batch",
        "job_event",
        "knowledge_inventory",
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='astra_indexator'"
            )
        ).scalars()
        assert expected.issubset(set(rows))


def test_durable_inbox_create_or_get_is_idempotent(database_url: str) -> None:
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


def test_database_rejects_malformed_access_zone(database_url: str) -> None:
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
    with Session(engine) as session:
        session.add_all(
            [
                IndexationJob(
                    id=uuid4(), producer_request_id=uuid4(), document_id=document_id,
                    document_version=7, access_zone_code="0300",
                    source_uri="seaweed://sources/a.pdf", status="PENDING",
                ),
                IndexationJob(
                    id=uuid4(), producer_request_id=uuid4(), document_id=document_id,
                    document_version=7, access_zone_code="0300",
                    source_uri="seaweed://sources/a-copy.pdf", status="PENDING",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
