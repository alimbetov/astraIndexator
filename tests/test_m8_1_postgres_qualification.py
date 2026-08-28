from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.persistence.models import DeliveryCheckpoint
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
        yield _psycopg_url(postgres.get_connection_url())


def test_head_schema_is_code_only_and_has_delivery_compatibility(database_url: str) -> None:
    cfg = _config(database_url)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)

    job_columns = {
        column["name"]
        for column in inspector.get_columns("indexation_job", schema="astra_indexator")
    }
    assert "access_zone_code" in job_columns
    assert "access_zone_id" not in job_columns
    assert "requested_access_zone_id" not in job_columns
    assert "requested_access_zone_code" not in job_columns

    delivery_columns = {
        column["name"]
        for column in inspector.get_columns("delivery_checkpoint", schema="astra_indexator")
    }
    assert "resolved_access_zone_id" in delivery_columns
    assert "delivery_compatibility_sha256" in delivery_columns

    inventory_columns = {
        column["name"]
        for column in inspector.get_columns("knowledge_inventory", schema="astra_indexator")
    }
    assert "access_zone_code" in inventory_columns
    assert "access_zone_id" not in inventory_columns
    engine.dispose()


def test_repository_persists_code_and_ttl(database_url: str) -> None:
    cfg = _config(database_url)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE astra_indexator.knowledge_inventory, "
                "astra_indexator.job_event, astra_indexator.delivery_batch, "
                "astra_indexator.delivery_checkpoint, astra_indexator.prepared_artifact_checkpoint, "
                "astra_indexator.processing_attempt, astra_indexator.indexation_job CASCADE"
            )
        )
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://document",
                access_zone_code="0001",
                requested_ttl_days=30,
            ),
        )
        session.commit()
        assert job.access_zone_code == "0001"
        assert job.requested_ttl_days == 30
    engine.dispose()


def test_migration_0007_preserves_existing_delivery_checkpoint(database_url: str) -> None:
    cfg = _config(database_url)
    command.downgrade(cfg, "0006_access_zone_code_only")
    engine = create_engine(database_url)
    job_id = uuid4()
    producer_request_id = uuid4()
    document_id = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO astra_indexator.indexation_job "
                "(id, producer_request_id, document_id, document_version, access_zone_code, "
                "source_uri, status, max_attempts) "
                "VALUES (:id, :producer_request_id, :document_id, 1, '0001', "
                "'seaweed://legacy', 'PENDING', 5)"
            ),
            {
                "id": job_id,
                "producer_request_id": producer_request_id,
                "document_id": document_id,
            },
        )
        conn.execute(
            text(
                "INSERT INTO astra_indexator.delivery_checkpoint (job_id, next_batch_index) "
                "VALUES (:job_id, 0)"
            ),
            {"job_id": job_id},
        )

    command.upgrade(cfg, "head")
    with Session(engine) as session:
        checkpoint = session.get(DeliveryCheckpoint, job_id)
        assert checkpoint is not None
        assert checkpoint.delivery_compatibility_sha256 is None
        assert checkpoint.next_batch_index == 0
    engine.dispose()
