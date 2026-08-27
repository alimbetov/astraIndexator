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
from astra_indexator.application.coordinator import JobCoordinator, LeaseLostError
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import (
    AppendBlocksResult,
    DocumentVectorStatus,
    FinalizeIngestionResult,
    IngestionSessionState,
    LogicalBlock,
    StartIngestionResult,
)
from astra_indexator.persistence.delivery import DeliveryBatchRepository
from astra_indexator.persistence.models import DeliveryCheckpoint
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]
ZONE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
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


def _block() -> LogicalBlock:
    return LogicalBlock("b-0", "", "PARAGRAPH", "payload", 0)


def _enqueue_and_claim(engine):
    document_id = uuid4()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                source_uri="seaweed://documents/m8-3.txt",
                access_zone_id=ZONE_ID,
                access_zone_code=ZONE_CODE,
                source_content_hash="a" * 64,
            ),
        )
        session.commit()
        job_id = job.id
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="worker-a", lease_seconds=120)
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
    return document_id, claimed


def _expire(engine, job_id) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() - interval '1 second' WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )


class _StartLosesLeasePort:
    def __init__(self, engine, job_id, document_id: UUID) -> None:
        self.engine = engine
        self.job_id = job_id
        self.document_id = document_id
        self.start_calls = 0

    def start(self, command):
        self.start_calls += 1
        _expire(self.engine, self.job_id)
        return StartIngestionResult(
            ingestion_session_id=SESSION_ID,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            expires_at="",
        )

    def append(self, command):
        return AppendBlocksResult(
            ingestion_session_id=SESSION_ID,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            accepted_blocks=len(command.blocks),
            accepted_batch_index=command.batch_index,
        )

    def finalize(self, command):
        return FinalizeIngestionResult(
            access_zone_id=ZONE_ID,
            document_id=self.document_id,
            document_version=1,
            raw_operation_state="OPERATION_STATE_ACTIVE",
        )

    def abort(self, command):
        raise AssertionError("abort not expected")

    def get_ingestion_status(self, ingestion_session_id):
        raise AssertionError("status not expected")

    def get_document_vector_status(self, *, access_zone_id, document_id, document_version):
        return DocumentVectorStatus(raw_state="ACTIVE", progress_percent=100, searchable=True)


def _coordinator(engine, port) -> AstraVectorDeliveryCoordinator:
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=1)  # type: ignore[arg-type]
    return AstraVectorDeliveryCoordinator(lambda: Session(engine), port, planner)  # type: ignore[arg-type]


def test_start_ack_is_not_bound_when_lease_is_lost_during_rpc(database_url: str) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine)
    port = _StartLosesLeasePort(engine, claimed.token.job_id, document_id)

    with pytest.raises(LeaseLostError):
        _coordinator(engine, port).deliver(
            claimed,
            AstraVectorDeliveryInput(logical_blocks=(_block(),)),
        )

    assert port.start_calls == 1
    with Session(engine) as session:
        assert session.get(DeliveryCheckpoint, claimed.token.job_id) is None
    engine.dispose()


def test_expired_worker_cannot_mutate_final_hash_or_resolved_zone_checkpoint(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    document_id, claimed = _enqueue_and_claim(engine)
    port = _StartLosesLeasePort(engine, claimed.token.job_id, document_id)
    coordinator = _coordinator(engine, port)

    with Session(engine) as session:
        with session.begin():
            DeliveryBatchRepository().bind_session(
                session,
                job_id=claimed.token.job_id,
                ingestion_session_id=SESSION_ID,
                session_status_raw="ACTIVE",
            )

    _expire(engine, claimed.token.job_id)

    with pytest.raises(LeaseLostError):
        coordinator._persist_final_hash(claimed.token, SESSION_ID, "f" * 64)
    with pytest.raises(LeaseLostError):
        coordinator._persist_resolved_zone(claimed.token, SESSION_ID, ZONE_ID)

    with Session(engine) as session:
        checkpoint = session.get(DeliveryCheckpoint, claimed.token.job_id)
        assert checkpoint is not None
        assert checkpoint.final_content_hash is None
        assert checkpoint.resolved_access_zone_id is None
    engine.dispose()
