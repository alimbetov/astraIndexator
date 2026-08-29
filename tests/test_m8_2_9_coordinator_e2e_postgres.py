from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryCoordinator,
    AstraVectorDeliveryInput,
)
from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.delivery_compatibility import delivery_compatibility_sha256
from astra_indexator.application.delivery_identity import DeliveryIdentityError
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import (
    ActivateDocumentVersionResult,
    AppendBlocksResult,
    DocumentVectorStatus,
    FinalizeIngestionResult,
    IngestionSessionState,
    IngestionStatus,
    LogicalBlock,
    StartIngestionCommand,
    StartIngestionResult,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository
from astra_indexator.persistence.models import DeliveryBatch, DeliveryCheckpoint, IndexationJob
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]
ZONE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ZONE_CODE = "0001"
SOURCE_SHA256 = "a" * 64
PREPARED_COMPATIBILITY_SHA256 = "b" * 64


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


class _HashMapper:
    def logical_block(self, block: LogicalBlock):
        return SimpleNamespace(
            block_id=block.block_id,
            parent_block_id=block.parent_block_id,
            block_type=4,
            text=block.text,
            order_index=block.order_index,
            metadata=block.metadata,
            source_links=(),
            HasField=lambda name: False,
        )


class _Port:
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        self.start_calls = 0
        self.start_commands: list[StartIngestionCommand] = []
        self.append_calls: list[int] = []
        self.finalize_calls = 0
        self.activation_calls = 0
        self.vector_calls = 0
        self.statuses = [
            DocumentVectorStatus(
                raw_state="OPERATION_STATE_READY_TO_ACTIVATE",
                progress_percent=100.0,
                searchable=True,
                ready_to_activate=True,
                expected_bindings=2,
                synced_bindings=2,
                pending_bindings=0,
                qdrant_collection_exists=True,
                qdrant_points_expected=2,
                qdrant_points_found=2,
                qdrant_points_missing=0,
            ),
            DocumentVectorStatus(
                raw_state="OPERATION_STATE_ACTIVE",
                progress_percent=100.0,
                searchable=True,
                ready_to_activate=False,
                expected_bindings=2,
                synced_bindings=2,
                pending_bindings=0,
                qdrant_collection_exists=True,
                qdrant_points_expected=2,
                qdrant_points_found=2,
                qdrant_points_missing=0,
            ),
        ]

    def start(self, command: StartIngestionCommand):
        self.start_calls += 1
        self.start_commands.append(command)
        return StartIngestionResult(
            ingestion_session_id=SESSION_ID,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            expires_at="",
        )

    def append(self, command):
        self.append_calls.append(command.batch_index)
        return AppendBlocksResult(
            ingestion_session_id=SESSION_ID,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            accepted_blocks=len(command.blocks),
            accepted_batch_index=command.batch_index,
        )

    def finalize(self, command):
        self.finalize_calls += 1
        return FinalizeIngestionResult(
            access_zone_id=ZONE_ID,
            document_id=self.document_id,
            document_version=1,
            raw_operation_state="OPERATION_STATE_SYNCING",
        )

    def abort(self, command):
        raise AssertionError("abort must not be used in successful delivery")

    def get_ingestion_status(self, ingestion_session_id):
        return IngestionStatus(
            ingestion_session_id=ingestion_session_id,
            raw_status="COMPLETED",
            state=IngestionSessionState.COMPLETED,
            received_batches=2,
            received_blocks=4,
            received_bytes=100,
            expires_at="",
        )

    def activate_document_version(self, command):
        self.activation_calls += 1
        assert command.access_zone_id == ZONE_ID
        assert command.document_id == self.document_id
        assert command.document_version == 1
        return ActivateDocumentVersionResult(
            document_id=self.document_id,
            document_version=1,
            raw_status="ACTIVE",
        )

    def get_document_vector_status(self, *, access_zone_id, document_id, document_version):
        self.vector_calls += 1
        assert access_zone_id == ZONE_ID
        assert document_id == self.document_id
        assert document_version == 1
        return self.statuses.pop(0)


def _blocks() -> tuple[LogicalBlock, ...]:
    return (
        LogicalBlock("root", "", "DOCUMENT", "Document", 0),
        LogicalBlock("b-0", "root", "PARAGRAPH", "first", 1),
        LogicalBlock("b-1", "root", "PARAGRAPH", "second", 2),
        LogicalBlock("b-2", "root", "PARAGRAPH", "third", 3),
    )


def _payload(*, source_hash: str = SOURCE_SHA256) -> AstraVectorDeliveryInput:
    return AstraVectorDeliveryInput(
        logical_blocks=_blocks(),
        source_content_hash=source_hash,
        prepared_compatibility_sha256=PREPARED_COMPATIBILITY_SHA256,
    )


def _enqueue_and_claim(
    engine,
    *,
    worker_id: str = "worker-a",
    source_hash: str | None = SOURCE_SHA256,
):
    document_id = uuid4()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                source_uri="seaweed://documents/m8-2-9.txt",
                access_zone_code=ZONE_CODE,
                requested_ttl_days=0,
                source_file_name="m8-2-9.txt",
                source_content_hash=source_hash,
                source_size_bytes=123,
            ),
        )
        session.commit()
        job_id = job.id
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id=worker_id, lease_seconds=120)
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
    return document_id, claimed


def _coordinator(engine, port: _Port) -> AstraVectorDeliveryCoordinator:
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=2)  # type: ignore[arg-type]
    return AstraVectorDeliveryCoordinator(lambda: Session(engine), port, planner)  # type: ignore[arg-type]


def test_full_coordinator_delivery_completes_only_after_searchable(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine)
    port = _Port(document_id)

    outcome = _coordinator(engine, port).deliver(claimed, _payload())

    assert outcome.ingestion_session_id == SESSION_ID
    assert outcome.access_zone_code == ZONE_CODE
    assert outcome.resolved_access_zone_id == ZONE_ID
    assert port.start_calls == 1
    assert port.start_commands[0].access_zone_code == ZONE_CODE
    assert port.start_commands[0].access_zone_id is None
    assert port.start_commands[0].content_hash == SOURCE_SHA256
    assert port.start_commands[0].idempotency_key == (
        f"astra-indexator:{document_id}:1:{SOURCE_SHA256}"
    )
    assert port.append_calls == [0, 1]
    assert port.finalize_calls == 1
    assert port.activation_calls == 1
    assert outcome.readiness.status.searchable is True

    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        checkpoint = session.get(DeliveryCheckpoint, claimed.token.job_id)
        batches = (
            session.query(DeliveryBatch)
            .filter(DeliveryBatch.job_id == claimed.token.job_id)
            .order_by(DeliveryBatch.batch_index)
            .all()
        )
        assert job is not None and job.status == "COMPLETED"
        assert job.access_zone_code == ZONE_CODE
        assert job.processing_stage == "ASTRAVECTOR_ACTIVATE"
        assert checkpoint is not None
        assert checkpoint.ingestion_session_id == SESSION_ID
        assert checkpoint.resolved_access_zone_id == ZONE_ID
        assert checkpoint.next_batch_index == 2
        assert checkpoint.final_content_hash is not None
        assert checkpoint.delivery_compatibility_sha256 == delivery_compatibility_sha256(
            PREPARED_COMPATIBILITY_SHA256
        )
        assert checkpoint.searchable is True
        assert [batch.status for batch in batches] == ["ACCEPTED", "ACCEPTED"]
    engine.dispose()


def test_restart_reuses_bound_session_and_does_not_start_again(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine, worker_id="worker-restart")
    with Session(engine) as session:
        with session.begin():
            DeliveryBatchRepository().bind_session(
                session,
                job_id=claimed.token.job_id,
                ingestion_session_id=SESSION_ID,
                session_status_raw="ACTIVE",
            )

    port = _Port(document_id)
    _coordinator(engine, port).deliver(claimed, _payload())

    assert port.start_calls == 0
    assert port.append_calls == [0, 1]
    assert port.finalize_calls == 1
    engine.dispose()


def test_code_only_delivery_preserves_access_zone_code_end_to_end(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine, worker_id="worker-code")
    port = _Port(document_id)

    outcome = _coordinator(engine, port).deliver(claimed, _payload())

    assert outcome.access_zone_code == ZONE_CODE
    assert outcome.resolved_access_zone_id == ZONE_ID
    assert len(port.start_commands) == 1
    start = port.start_commands[0]
    assert start.access_zone_code == ZONE_CODE
    assert start.access_zone_id is None

    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        checkpoint = session.get(DeliveryCheckpoint, claimed.token.job_id)
        assert job is not None and job.access_zone_code == ZONE_CODE
        assert checkpoint is not None and checkpoint.resolved_access_zone_id == ZONE_ID
    engine.dispose()


def test_missing_durable_source_hash_blocks_start(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(
        engine,
        worker_id="worker-missing-hash",
        source_hash=None,
    )
    port = _Port(document_id)

    with pytest.raises(DeliveryIdentityError, match="durable source_content_hash"):
        _coordinator(engine, port).deliver(claimed, _payload())
    assert port.start_calls == 0
    engine.dispose()


def test_m7_payload_source_hash_conflict_blocks_start(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine, worker_id="worker-conflict-hash")
    port = _Port(document_id)

    with pytest.raises(DeliveryIdentityError, match="differs from durable"):
        _coordinator(engine, port).deliver(claimed, _payload(source_hash="c" * 64))
    assert port.start_calls == 0
    engine.dispose()
