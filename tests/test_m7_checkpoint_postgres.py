from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.coordinator import JobCoordinator, LeaseLostError
from astra_indexator.application.prepared_artifact_checkpoint import PreparedArtifactCheckpointService
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.prepared_artifacts import ArtifactCompatibility, ArtifactIdentity, PreparedArtifactPublisher

ROOT = Path(__file__).resolve().parents[1]


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = data
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://").replace("postgresql://", "postgresql+psycopg://")


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


def _claim(engine):
    document_id = uuid4()
    with Session(engine) as session:
        IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                access_zone_code="0600",
                knowledge_type="TECHNICAL",
                source_uri="seaweed://documents/m7.pdf",
                source_file_name="m7.pdf",
            ),
        )
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="m7-worker", lease_seconds=60)
        assert claimed is not None
        session.commit()
    return claimed, document_id


def _manifest(claimed, document_id):
    store = MemoryStore()
    publisher = PreparedArtifactPublisher(store)
    # Unit publication fencing is tested separately. Here PostgreSQL fencing
    # is exercised at authoritative checkpoint installation.
    return publisher.publish(
        token=claimed.token,
        assert_current_lease=lambda _: None,
        identity=ArtifactIdentity(document_id, 1, "a" * 64),
        compatibility=ArtifactCompatibility(
            schema_version="prepared-v1",
            parser_name="canonical",
            parser_version="m4-v1",
            parser_profile="default",
            normalizer_version="m6-v1",
            splitter_profile="multilingual-general-v1",
            splitter_version="logical-v1",
        ),
        elements=[{"elementId": "e1"}],
        fragments=[{"fragmentId": "f1"}],
    )


def test_checkpoint_is_authoritative_and_fenced(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, document_id = _claim(engine)
    manifest = _manifest(claimed, document_id)
    service = PreparedArtifactCheckpointService()
    uri = f"seaweed://prepared/{manifest.artifact_id}/manifest.json"
    with Session(engine) as session:
        installed = service.install(session, claimed.token, manifest=manifest, manifest_uri=uri)
        session.commit()
    assert installed.artifact_id == manifest.artifact_id

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT artifact_id, manifest_uri, lease_generation, element_count, fragment_count FROM astra_indexator.prepared_artifact_checkpoint WHERE job_id=:id"),
            {"id": claimed.token.job_id},
        ).one()
        assert row.artifact_id == manifest.artifact_id
        assert row.manifest_uri == uri
        assert row.lease_generation == claimed.token.lease_generation
        assert row.element_count == 1
        assert row.fragment_count == 1


def test_expired_lease_cannot_install_checkpoint(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, document_id = _claim(engine)
    manifest = _manifest(claimed, document_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE astra_indexator.indexation_job SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": claimed.token.job_id},
        )
    with Session(engine) as session:
        with pytest.raises(LeaseLostError):
            PreparedArtifactCheckpointService().install(
                session,
                claimed.token,
                manifest=manifest,
                manifest_uri="seaweed://prepared/stale/manifest.json",
            )
        session.rollback()
