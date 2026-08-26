from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.domain.lifecycle import DocumentLifecycleState
from astra_indexator.persistence.knowledge_inventory import KnowledgeInventoryRepository
from astra_indexator.persistence.lifecycle_models import DocumentVersionLifecycle
from astra_indexator.persistence.models import DeliveryCheckpoint, IndexationJob

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


def _job(
    *,
    document_id: UUID,
    version: int,
    code: str | None = None,
    zone_id: UUID | None = None,
    source_file_name: str = "doc.pdf",
    storage_object_id: UUID | None = None,
    storage_object_name: str | None = None,
) -> IndexationJob:
    return IndexationJob(
        id=uuid4(),
        producer_request_id=uuid4(),
        document_id=document_id,
        document_version=version,
        access_zone_code=code,
        access_zone_id=zone_id,
        requested_access_zone_code=code,
        requested_access_zone_id=zone_id,
        requested_ttl_days=0,
        source_uri=f"seaweed://sources/{document_id}/{version}.pdf",
        source_file_name=source_file_name,
        storage_object_id=storage_object_id,
        storage_object_name=storage_object_name,
        source_content_hash="a" * 64,
        processing_fingerprint="pipeline-v1",
        knowledge_type="TECHNICAL",
        status="COMPLETED",
    )


def test_code_only_projection_preserves_zone_and_dual_source_provenance(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    repo = KnowledgeInventoryRepository()
    document_id = uuid4()
    resolved_zone_id = uuid4()
    session_id = uuid4()
    storage_object_id = uuid4()
    public_name = "Технический регламент 2026.pdf"
    internal_name = f"{storage_object_id}.pdf"
    job = _job(
        document_id=document_id,
        version=1,
        code="0001",
        source_file_name=public_name,
        storage_object_id=storage_object_id,
        storage_object_name=internal_name,
    )

    with Session(engine) as session:
        session.add(job)
        session.flush()
        session.add(
            DocumentVersionLifecycle(
                document_id=document_id,
                document_version=1,
                job_id=job.id,
                state="ACTIVE",
                is_current=True,
                requested_access_zone_code="0001",
                requested_ttl_days=0,
            )
        )
        session.add(
            DeliveryCheckpoint(
                job_id=job.id,
                resolved_access_zone_id=resolved_zone_id,
                ingestion_session_id=session_id,
                vector_state_raw="ACTIVE",
                searchable=True,
                expected_bindings=12,
                synced_bindings=12,
            )
        )
        session.flush()

        projection = repo.rebuild(
            session,
            document_id=document_id,
            document_version=1,
        )
        row = repo.get(session, document_id=document_id, document_version=1)
        session.commit()

    assert projection.lifecycle_state is DocumentLifecycleState.ACTIVE
    assert projection.requested_access_zone_code == "0001"
    assert projection.requested_access_zone_id is None
    assert projection.resolved_access_zone_id == resolved_zone_id
    assert projection.ingestion_session_id == session_id
    assert projection.searchable is True
    assert projection.source_file_name == public_name
    assert projection.storage_object_id == storage_object_id
    assert projection.storage_object_name == internal_name
    assert projection.source_file_name != projection.storage_object_name
    assert row is not None
    assert row["requested_access_zone_code"] == "0001"
    assert row["resolved_access_zone_id"] == resolved_zone_id
    assert row["access_zone_code"] == "0001"
    assert row["source_file_name"] == public_name
    assert row["storage_object_id"] == storage_object_id
    assert row["storage_object_name"] == internal_name
    assert row["source_uri"] == job.source_uri


def test_uuid_only_projection_does_not_invent_access_zone_code(database_url: str) -> None:
    engine = create_engine(database_url)
    repo = KnowledgeInventoryRepository()
    document_id = uuid4()
    requested_zone_id = uuid4()
    job = _job(document_id=document_id, version=1, zone_id=requested_zone_id)

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
                requested_access_zone_id=requested_zone_id,
                requested_ttl_days=0,
            )
        )
        session.flush()
        projection = repo.rebuild(session, document_id=document_id, document_version=1)
        row = repo.get(session, document_id=document_id, document_version=1)
        session.commit()

    assert projection.requested_access_zone_code is None
    assert projection.requested_access_zone_id == requested_zone_id
    assert projection.resolved_access_zone_id is None
    assert row is not None
    assert row["requested_access_zone_code"] is None
    assert row["access_zone_code"] is None


def test_projection_rebuild_updates_same_version_in_place(database_url: str) -> None:
    engine = create_engine(database_url)
    repo = KnowledgeInventoryRepository()
    document_id = uuid4()
    job = _job(document_id=document_id, version=7, code="0600")

    with Session(engine) as session:
        session.add(job)
        session.flush()
        lifecycle = DocumentVersionLifecycle(
            document_id=document_id,
            document_version=7,
            job_id=job.id,
            state="BUILDING",
            is_current=False,
            requested_access_zone_code="0600",
            requested_ttl_days=0,
        )
        session.add(lifecycle)
        session.flush()
        first = repo.rebuild(session, document_id=document_id, document_version=7)
        assert first.lifecycle_state is DocumentLifecycleState.BUILDING
        assert first.searchable is False

        lifecycle.state = "ACTIVE"
        lifecycle.is_current = True
        session.add(
            DeliveryCheckpoint(
                job_id=job.id,
                vector_state_raw="ACTIVE",
                searchable=True,
                expected_bindings=3,
                synced_bindings=3,
            )
        )
        session.flush()
        second = repo.rebuild(session, document_id=document_id, document_version=7)
        row = repo.get(session, document_id=document_id, document_version=7)
        session.commit()

    assert second.lifecycle_state is DocumentLifecycleState.ACTIVE
    assert second.is_current is True
    assert second.searchable is True
    assert row is not None
    assert row["lifecycle_state"] == "ACTIVE"
    assert row["is_current"] is True
    assert row["searchable"] is True
