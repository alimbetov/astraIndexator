from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
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
from astra_indexator.persistence.models import DeliveryCheckpoint, IndexationJob
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]
ZONE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
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


@dataclass
class _Port:
    document_id: UUID
    start_calls: int = 0

    def start(self, command):
        self.start_calls += 1
        assert command.content_hash == SOURCE_SHA256
        assert command.access_zone_code == "0001"
        assert command.access_zone_id is None
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
            raw_operation_state="OPERATION_STATE_SYNCING",
        )

    def abort(self, command):
        raise AssertionError("abort not expected")

    def get_ingestion_status(self, ingestion_session_id):
        raise AssertionError("status reconciliation not expected")

    def get_document_vector_status(self, *, access_zone_id, document_id, document_version):
        return DocumentVectorStatus(
            raw_state="OPERATION_STATE_ACTIVE",
            progress_percent=100.0,
            searchable=True,
            ready_to_activate=False,
            qdrant_collection_exists=True,
        )


def _blocks() -> tuple[LogicalBlock, ...]:
    return (
        LogicalBlock("root", "", "DOCUMENT", "Document", 0),
        LogicalBlock("p", "root", "PARAGRAPH", "text", 1),
    )


def _payload() -> AstraVectorDeliveryInput:
    return AstraVectorDeliveryInput(
        logical_blocks=_blocks(),
        source_content_hash=SOURCE_SHA256,
        prepared_compatibility_sha256=PREPARED_COMPATIBILITY_SHA256,
    )


def _create_claim(database_url: str):
    engine = create_engine(database_url)
    document_id = uuid4()
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=document_id,
                document_version=1,
                source_uri="seaweed://source",
                access_zone_code="0001",
                source_content_hash=SOURCE_SHA256,
            ),
        )
        job_id = job.id
        session.commit()
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="worker-a", lease_seconds=120)
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
    return engine, document_id, claimed


def test_coordinator_mutations_are_fenced_by_current_lease(database_url: str) -> None:
    engine, document_id, claimed = _create_claim(database_url)
    port = _Port(document_id)
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=100)  # type: ignore[arg-type]
    coordinator = AstraVectorDeliveryCoordinator(lambda: Session(engine), port, planner)  # type: ignore[arg-type]

    outcome = coordinator.deliver(claimed, _payload())
    assert outcome.resolved_access_zone_id == ZONE_ID

    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        checkpoint = session.get(DeliveryCheckpoint, claimed.token.job_id)
        assert job is not None and job.status == "COMPLETED"
        assert checkpoint is not None and checkpoint.resolved_access_zone_id == ZONE_ID
    engine.dispose()


def test_stale_worker_cannot_mutate_resolved_zone(database_url: str) -> None:
    engine, document_id, claimed = _create_claim(database_url)
    coordinator = JobCoordinator()

    with Session(engine) as session:
        job = session.get(IndexationJob, claimed.token.job_id)
        assert job is not None
        job.lease_until = job.lease_acquired_at
        session.commit()

    with Session(engine) as session:
        reclaimed = coordinator.claim_next(session, worker_id="worker-b", lease_seconds=120)
        assert reclaimed is not None
        session.commit()

    port = _Port(document_id)
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=100)  # type: ignore[arg-type]
    delivery = AstraVectorDeliveryCoordinator(lambda: Session(engine), port, planner)  # type: ignore[arg-type]
    with pytest.raises(LeaseLostError):
        delivery.deliver(claimed, _payload())
    assert port.start_calls == 0
    engine.dispose()
