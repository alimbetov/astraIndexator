from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, update
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryCoordinator,
    AstraVectorDeliveryInput,
)
from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.durable_append_delivery import InsufficientLeaseWindowError
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import LogicalBlock
from astra_indexator.persistence.models import IndexationJob
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]
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


class _NoCallPort:
    def __init__(self) -> None:
        self.start_calls = 0

    def start(self, command):
        self.start_calls += 1
        raise AssertionError("Start must not run with insufficient lease window")

    def append(self, command):
        raise AssertionError("Append must not run")

    def finalize(self, command):
        raise AssertionError("Finalize must not run")

    def abort(self, command):
        raise AssertionError("Abort must not run")

    def get_ingestion_status(self, ingestion_session_id):
        raise AssertionError("status must not run")

    def get_document_vector_status(self, **kwargs):
        raise AssertionError("vector status must not run")


def _payload() -> AstraVectorDeliveryInput:
    return AstraVectorDeliveryInput(
        logical_blocks=(
            LogicalBlock("root", "", "DOCUMENT", "Document", 0),
            LogicalBlock("p", "root", "PARAGRAPH", "text", 1),
        ),
        source_content_hash=SOURCE_SHA256,
        prepared_compatibility_sha256=PREPARED_COMPATIBILITY_SHA256,
    )


def test_start_is_blocked_when_remaining_lease_is_below_rpc_window(database_url: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=UUID("11111111-1111-1111-1111-111111111111"),
                document_version=1,
                source_uri="seaweed://source",
                access_zone_code="0600",
                source_content_hash=SOURCE_SHA256,
            ),
        )
        job_id = job.id
        session.commit()

    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id="worker-short", lease_seconds=120)
        assert claimed is not None
        session.commit()

    with Session(engine) as session:
        session.execute(
            update(IndexationJob)
            .where(IndexationJob.id == job_id)
            .values(lease_until=func.now() + timedelta(seconds=5))
        )
        session.commit()

    port = _NoCallPort()
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=100)  # type: ignore[arg-type]
    coordinator = AstraVectorDeliveryCoordinator(
        lambda: Session(engine),
        port,  # type: ignore[arg-type]
        planner,
        mutating_rpc_deadline_seconds=10,
        rpc_safety_margin_seconds=2,
    )

    with pytest.raises(InsufficientLeaseWindowError):
        coordinator.deliver(claimed, _payload())
    assert port.start_calls == 0
    engine.dispose()
