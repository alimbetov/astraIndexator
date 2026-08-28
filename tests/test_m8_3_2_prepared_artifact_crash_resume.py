from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.durable_prepared_resume import DurablePreparedArtifactResume
from astra_indexator.application.prepared_artifact_wiring import PreparedArtifactDeliveryInputFactory
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpointRepository
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.prepared_artifacts.model import (
    ArtifactIdentity,
    ArtifactManifest,
    PreparedArtifact,
    PreparedArtifactPart,
    PreparedLogicalFragment,
)

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_SHA256 = "a" * 64
COMPATIBILITY_SHA256 = "b" * 64
MANIFEST_SHA256 = "c" * 64
PART_SHA256 = "d" * 64
ZONE_CODE = "0600"


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


def _artifact() -> PreparedArtifact:
    fragments = (
        PreparedLogicalFragment(
            fragment_id="root",
            parent_fragment_id=None,
            kind="DOCUMENT",
            text="Document",
            order_index=0,
            metadata={},
            source_links=(),
        ),
        PreparedLogicalFragment(
            fragment_id="p",
            parent_fragment_id="root",
            kind="PARAGRAPH",
            text="text",
            order_index=1,
            metadata={},
            source_links=(),
        ),
    )
    return PreparedArtifact(
        manifest=ArtifactManifest(
            schema_version="prepared-artifact-v1",
            identity=ArtifactIdentity(
                document_id=DOCUMENT_ID,
                document_version=1,
                source_sha256=SOURCE_SHA256,
            ),
            compatibility_sha256=COMPATIBILITY_SHA256,
            parts=(
                PreparedArtifactPart(
                    part_index=0,
                    uri="seaweed://prepared/part-0000.json",
                    sha256=PART_SHA256,
                    byte_size=100,
                    fragment_count=len(fragments),
                ),
            ),
            manifest_sha256=MANIFEST_SHA256,
        ),
        fragments=fragments,
    )


class _ReplayService:
    def __init__(self, artifact: PreparedArtifact) -> None:
        self.artifact = artifact
        self.calls = 0

    def replay(self, checkpoint):
        self.calls += 1
        return self.artifact


def test_reclaimed_worker_resumes_from_verified_m7_without_expensive_rerun(database_url: str) -> None:
    engine = create_engine(database_url)
    artifact = _artifact()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=DOCUMENT_ID,
                document_version=1,
                source_uri="seaweed://source",
                access_zone_code=ZONE_CODE,
                requested_ttl_days=30,
                source_content_hash=SOURCE_SHA256,
            ),
        )
        job_id = job.id
        PreparedArtifactCheckpointRepository().install(
            session,
            job_id=job_id,
            manifest_uri="seaweed://prepared/manifest.json",
            manifest_sha256=MANIFEST_SHA256,
            artifact_schema_version="prepared-artifact-v1",
            compatibility_sha256=COMPATIBILITY_SHA256,
            source_sha256=SOURCE_SHA256,
            document_id=DOCUMENT_ID,
            document_version=1,
            access_zone_code=ZONE_CODE,
            requested_ttl_days=30,
        )
        session.commit()

    with Session(engine) as session:
        first = JobCoordinator().claim_next(session, worker_id="worker-a", lease_seconds=120)
        assert first is not None
        session.commit()

    with Session(engine) as session:
        job = session.get(type(first), job_id)  # type: ignore[arg-type]
        _ = job

    # Simulate recovery through the durable prepared checkpoint; the resume service must use M7
    # evidence rather than calling parser/OCR/splitter again.
    replay = _ReplayService(artifact)
    resume = DurablePreparedArtifactResume(
        lambda: Session(engine),
        replay,  # type: ignore[arg-type]
        delivery_input_factory=PreparedArtifactDeliveryInputFactory(),
    )
    delivery = resume.load(
        first,
        document_id=DOCUMENT_ID,
        document_version=1,
    )

    assert replay.calls == 1
    assert delivery.source_content_hash == SOURCE_SHA256
    assert delivery.prepared_compatibility_sha256 == COMPATIBILITY_SHA256
    engine.dispose()
