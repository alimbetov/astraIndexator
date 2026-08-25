from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application import (
    JobCoordinator,
    LeaseLostError,
    PreparedArtifactCheckpointService,
    PreparedArtifactIdentityMismatch,
    PreparedArtifactReplayService,
)
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.prepared_artifacts import (
    ArtifactCompatibility,
    ArtifactIdentity,
    PreparedArtifactPublisher,
    PreparedArtifactReader,
    ReplayDecision,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 64


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = data
        return True

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def iter_bytes(self, key: str, *, chunk_size: int = 64 * 1024):
        payload = self.get(key)
        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    def exists(self, key: str) -> bool:
        return key in self.objects


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
                source_content_hash=SOURCE_SHA,
            ),
        )
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="m7-worker", lease_seconds=60)
        assert claimed is not None
        session.commit()
    return claimed, document_id


def _published(claimed, document_id, store: MemoryStore):
    return PreparedArtifactPublisher(store).publish(
        token=claimed.token,
        assert_current_lease=lambda _: None,
        identity=ArtifactIdentity(document_id, 1, SOURCE_SHA),
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


def test_checkpoint_is_authoritative_and_restart_replay_works(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, document_id = _claim(engine)
    store = MemoryStore()
    published = _published(claimed, document_id, store)
    service = PreparedArtifactCheckpointService()
    with Session(engine) as session:
        installed = service.install(session, claimed.token, published=published)
        session.commit()
    assert installed.artifact_id == published.manifest.artifact_id
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT artifact_id, manifest_uri, manifest_sha256, lease_generation, element_count, fragment_count "
                "FROM astra_indexator.prepared_artifact_checkpoint WHERE job_id=:id"
            ),
            {"id": claimed.token.job_id},
        ).one()
        assert row.artifact_id == published.manifest.artifact_id
        assert row.manifest_sha256 == published.manifest_sha256
        assert row.lease_generation == claimed.token.lease_generation
        assert row.element_count == 1
        assert row.fragment_count == 1
    with Session(engine) as restarted_session:
        decision, replay = PreparedArtifactReplayService(PreparedArtifactReader(store)).replay(
            restarted_session,
            job_id=claimed.token.job_id,
            expected=published.manifest.compatibility,
        )
    assert decision is ReplayDecision.REPLAY
    assert replay is not None
    assert replay.fragments[0]["fragmentId"] == "f1"


def test_expired_lease_cannot_install_checkpoint(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, document_id = _claim(engine)
    store = MemoryStore()
    published = _published(claimed, document_id, store)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE astra_indexator.indexation_job SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": claimed.token.job_id},
        )
    with Session(engine) as session:
        with pytest.raises(LeaseLostError):
            PreparedArtifactCheckpointService().install(session, claimed.token, published=published)
        session.rollback()


def test_checkpoint_rejects_foreign_artifact_identity(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, _ = _claim(engine)
    store = MemoryStore()
    foreign = PreparedArtifactPublisher(store).publish(
        token=claimed.token,
        assert_current_lease=lambda _: None,
        identity=ArtifactIdentity(uuid4(), 1, SOURCE_SHA),
        compatibility=ArtifactCompatibility(
            schema_version="prepared-v1",
            parser_name="canonical",
            parser_version="m4-v1",
            parser_profile="default",
            normalizer_version="m6-v1",
            splitter_profile="default",
            splitter_version="logical-v1",
        ),
        elements=[],
        fragments=[],
    )
    with Session(engine) as session:
        with pytest.raises(PreparedArtifactIdentityMismatch):
            PreparedArtifactCheckpointService().install(session, claimed.token, published=foreign)
        session.rollback()


def test_manifest_without_postgres_checkpoint_is_non_authoritative(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed, document_id = _claim(engine)
    store = MemoryStore()
    published = _published(claimed, document_id, store)
    assert store.exists(published.manifest_key)
    with Session(engine) as session:
        decision, replay = PreparedArtifactReplayService(PreparedArtifactReader(store)).replay(
            session,
            job_id=claimed.token.job_id,
            expected=published.manifest.compatibility,
        )
    assert decision is ReplayDecision.REPROCESS
    assert replay is None
