from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.application import AcquisitionCheckpoint, JobCoordinator, LeaseLostError
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.storage import StorageRef

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


def _enqueue_and_claim(engine):
    repo = IndexationJobRepository()
    with Session(engine) as session:
        job = repo.create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                access_zone_code="0600",
                knowledge_type="TECHNICAL",
                source_uri="seaweed://documents/source.pdf",
                source_file_name="source.pdf",
            ),
        )
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="m3-worker", lease_seconds=60)
        assert claimed is not None
        session.commit()
        return claimed


def _source() -> AcquiredSource:
    return AcquiredSource(
        source_ref=StorageRef.parse("seaweed://documents/source.pdf"),
        local_path=Path("/tmp/source.validated"),
        original_file_name="source.pdf",
        detected_format="PDF",
        detected_content_type="application/pdf",
        size_bytes=123,
        sha256="a" * 64,
        etag='"etag"',
        version_id="v1",
        validation_profile="default-v1",
        warnings=(),
        acquired_at=datetime.now(timezone.utc),
    )


def test_acquisition_checkpoint_is_fenced_and_persists_evidence(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _enqueue_and_claim(engine)
    with Session(engine) as session:
        AcquisitionCheckpoint().record(session, claimed.token, _source())
        session.commit()

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT source_content_hash, source_size_bytes, source_detected_format, source_validation_profile, processing_stage FROM astra_indexator.indexation_job WHERE id=:id"
            ),
            {"id": claimed.token.job_id},
        ).one()
        assert row.source_content_hash == "a" * 64
        assert row.source_size_bytes == 123
        assert row.source_detected_format == "PDF"
        assert row.source_validation_profile == "default-v1"
        assert row.processing_stage == "ACQUIRED"


def test_expired_lease_cannot_install_acquisition_checkpoint(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _enqueue_and_claim(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job SET lease_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": claimed.token.job_id},
        )

    with Session(engine) as session:
        with pytest.raises(LeaseLostError):
            AcquisitionCheckpoint().record(session, claimed.token, _source())
        session.rollback()
