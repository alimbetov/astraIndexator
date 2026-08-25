from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://"
    )


def _config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def database_url() -> str:
    with PostgresContainer("postgres:16") as postgres:
        url = _psycopg_url(postgres.get_connection_url())
        command.upgrade(_config(url), "head")
        yield url
        command.downgrade(_config(url), "base")


def test_repository_persists_code_and_ttl_as_requested_intent(database_url: str) -> None:
    engine = create_engine(database_url)
    request_id = uuid4()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=request_id,
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://m8/code.pdf",
                access_zone_code="0001",
                requested_ttl_days=30,
            ),
        )
        session.commit()
        job_id = job.id

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT requested_access_zone_id, requested_access_zone_code, "
                "requested_ttl_days FROM astra_indexator.indexation_job WHERE id=:id"
            ),
            {"id": job_id},
        ).one()
    assert row.requested_access_zone_id is None
    assert row.requested_access_zone_code == "0001"
    assert row.requested_ttl_days == 30


def test_repository_supports_uuid_only_requested_selector(database_url: str) -> None:
    engine = create_engine(database_url)
    zone_id = uuid4()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://m8/id-only.pdf",
                requested_access_zone_id=zone_id,
                requested_ttl_days=0,
            ),
        )
        session.commit()
        job_id = job.id

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT requested_access_zone_id, requested_access_zone_code, "
                "requested_ttl_days FROM astra_indexator.indexation_job WHERE id=:id"
            ),
            {"id": job_id},
        ).one()
    assert row.requested_access_zone_id == zone_id
    assert row.requested_access_zone_code is None
    assert row.requested_ttl_days == 0


def test_m8_migration_backfills_non_empty_pre_m8_database() -> None:
    with PostgresContainer("postgres:16") as postgres:
        url = _psycopg_url(postgres.get_connection_url())
        cfg = _config(url)
        command.upgrade(cfg, "0003_prepared_artifact_checkpoint")
        engine = create_engine(url)
        legacy_job_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO astra_indexator.indexation_job "
                    "(id, producer_request_id, document_id, document_version, "
                    "access_zone_code, source_uri, status) "
                    "VALUES (:id, :request_id, :document_id, 1, '0600', "
                    "'seaweed://legacy.pdf', 'PENDING')"
                ),
                {
                    "id": legacy_job_id,
                    "request_id": uuid4(),
                    "document_id": uuid4(),
                },
            )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT requested_access_zone_code "
                    "FROM astra_indexator.indexation_job WHERE id=:id"
                ),
                {"id": legacy_job_id},
            ).one()
        assert row.requested_access_zone_code == "0600"
        command.downgrade(cfg, "base")
