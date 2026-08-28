from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.durable_prepared_resume import (
    DurablePreparedArtifactResumeService,
    PreparedArtifactLineageMismatch,
)
from astra_indexator.application.prepared_artifact_replay import PreparedArtifactReplayService
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.prepared_artifacts.model import (
    ArtifactCompatibility,
    ArtifactIdentity,
    ArtifactManifest,
    ArtifactPart,
    PreparedArtifact,
    ReplayDecision,
)
from astra_indexator.splitter.model import (
    FragmentSource,
    FragmentStatistics,
    FragmentType,
    LogicalFragment,
    SplitDecision,
)

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SOURCE_SHA = "a" * 64
COMPAT_SHA = "d" * 64
ARTIFACT_ID = "c" * 64
MANIFEST_SHA = "e" * 64
ZONE_CODE = "0001"


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


@pytest.fixture(autouse=True)
def clean_database(database_url: str):
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE "
                "astra_indexator.knowledge_inventory, "
                "astra_indexator.job_event, "
                "astra_indexator.delivery_batch, "
                "astra_indexator.delivery_checkpoint, "
                "astra_indexator.prepared_artifact_checkpoint, "
                "astra_indexator.processing_attempt, "
                "astra_indexator.indexation_job CASCADE"
            )
        )
    yield
    engine.dispose()


def _compatibility() -> ArtifactCompatibility:
    return ArtifactCompatibility(
        schema_version="prepared-v1",
        parser_name="canonical",
        parser_version="m4-v1",
        parser_profile="default",
        normalizer_version="text-normalizer-v1",
        splitter_profile="multilingual-general-v1",
        splitter_version="logical-v1",
    )


def _artifact() -> PreparedArtifact:
    fragment = LogicalFragment(
        fragment_id="fragment-1",
        document_id=DOCUMENT_ID,
        document_version=1,
        sequence=0,
        fragment_type=FragmentType.PARAGRAPH,
        normalized_text="durable replay payload",
        context_prefix="",
        hierarchy=("Recovery",),
        source=FragmentSource(
            element_ids=("element-1",),
            element_from="element-1",
            element_to="element-1",
            page_from=1,
            page_to=1,
        ),
        statistics=FragmentStatistics(char_count=22, word_count=3, sentence_count=1),
        split=SplitDecision(
            reason="STRUCTURE_BOUNDARY",
            forced=False,
            profile="multilingual-general-v1",
            splitter_version="logical-v1",
        ),
        primary_language="en",
        languages=("en",),
        metadata={"origin": "m7"},
    )
    part = ArtifactPart(
        kind="FRAGMENTS",
        path="parts/fragments-00000.jsonl",
        sha256="f" * 64,
        record_count=1,
        byte_count=100,
    )
    manifest = ArtifactManifest(
        identity=ArtifactIdentity(DOCUMENT_ID, 1, SOURCE_SHA),
        compatibility=_compatibility(),
        artifact_id=ARTIFACT_ID,
        compatibility_sha256=COMPAT_SHA,
        parts=(part,),
        total_element_count=0,
        total_fragment_count=1,
    )
    return PreparedArtifact(manifest=manifest, elements=(), fragments=(asdict(fragment),))


class _Replay:
    def __init__(self, artifact: PreparedArtifact) -> None:
        self.artifact = artifact
        self.calls = 0

    def replay(self, session, *, job_id, expected):  # type: ignore[no-untyped-def]
        del session, job_id
        self.calls += 1
        if expected != self.artifact.manifest.compatibility:
            return ReplayDecision.REPROCESS, None
        return ReplayDecision.REPLAY, self.artifact


def _seed_and_claim(engine):
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=DOCUMENT_ID,
                document_version=1,
                source_uri="seaweed://documents/recovery.pdf",
                access_zone_code=ZONE_CODE,
                requested_ttl_days=30,
                source_file_name="recovery.pdf",
                source_content_hash=SOURCE_SHA,
            ),
        )
        session.flush()
        session.add(
            PreparedArtifactCheckpoint(
                job_id=job.id,
                artifact_id=ARTIFACT_ID,
                manifest_uri="seaweed://prepared/manifest.json",
                manifest_sha256=MANIFEST_SHA,
                source_sha256=SOURCE_SHA,
                compatibility_sha256=COMPAT_SHA,
                element_count=0,
                fragment_count=1,
                lease_generation=1,
                access_zone_code=ZONE_CODE,
                requested_ttl_days=30,
            )
        )
        session.commit()
        job_id = job.id

    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="worker-a", lease_seconds=120)
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
    return claimed


def test_reclaimed_worker_resumes_from_m7_without_reprocessing(database_url: str) -> None:
    engine = create_engine(database_url)
    first = _seed_and_claim(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() - interval '1 second' WHERE id = :job_id"
            ),
            {"job_id": first.token.job_id},
        )
    with Session(engine) as session:
        reclaimed = JobCoordinator().claim_next(session, worker_id="worker-b", lease_seconds=120)
        assert reclaimed is not None
        assert reclaimed.token.lease_generation == first.token.lease_generation + 1
        session.commit()

    replay = _Replay(_artifact())
    service = DurablePreparedArtifactResumeService(
        lambda: Session(engine),
        cast(PreparedArtifactReplayService, replay),
    )
    payload = service.resume(reclaimed, expected=_compatibility())

    assert replay.calls == 1
    assert payload.source_content_hash == SOURCE_SHA
    assert payload.prepared_compatibility_sha256 == COMPAT_SHA
    assert payload.source_file_name == "recovery.pdf"
    assert [block.block_type for block in payload.logical_blocks] == ["DOCUMENT", "PARAGRAPH"]
    assert payload.logical_blocks[1].text == "durable replay payload"
    engine.dispose()


def test_resume_fails_closed_when_accesszone_ttl_lineage_changed(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _seed_and_claim(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.prepared_artifact_checkpoint "
                "SET requested_ttl_days = 31 WHERE job_id = :job_id"
            ),
            {"job_id": claimed.token.job_id},
        )

    replay = _Replay(_artifact())
    service = DurablePreparedArtifactResumeService(
        lambda: Session(engine),
        cast(PreparedArtifactReplayService, replay),
    )
    with pytest.raises(PreparedArtifactLineageMismatch, match="ttlDays"):
        service.resume(claimed, expected=_compatibility())
    assert replay.calls == 0
    engine.dispose()
