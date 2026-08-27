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

from astra_indexator.application.coordinator import JobCoordinator, LeaseLostError
from astra_indexator.application.durable_append_delivery import DurableAppendDeliveryRunner
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.contracts import (
    AppendBlocksResult,
    IngestionSessionState,
    LogicalBlock,
)
from astra_indexator.persistence.delivery import BatchReplayDisposition
from astra_indexator.persistence.models import DeliveryBatch, DeliveryCheckpoint
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

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


def _enqueue_and_claim(engine, *, worker_id: str):
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                access_zone_code="0600",
                source_uri="seaweed://documents/m8-3-0.txt",
            ),
        )
        session.commit()
        job_id = job.id

    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(session, worker_id=worker_id, lease_seconds=120)
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
    return claimed


def _batch():
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=10)  # type: ignore[arg-type]
    blocks = (
        LogicalBlock("b-0", "", "PARAGRAPH", "first", 0),
        LogicalBlock("b-1", "", "PARAGRAPH", "second", 1),
    )
    return planner.plan(blocks)


def _expire(engine, job_id: UUID) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() - interval '1 second' WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )


class _CountingPort:
    def __init__(self, ingestion_session_id: UUID) -> None:
        self.ingestion_session_id = ingestion_session_id
        self.calls = 0
        self.hashes: list[str] = []

    def append(self, command):
        self.calls += 1
        self.hashes.append(command.batch_content_hash)
        return AppendBlocksResult(
            ingestion_session_id=self.ingestion_session_id,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            accepted_blocks=len(command.blocks),
            accepted_batch_index=command.batch_index,
        )


def test_expired_worker_cannot_create_delivery_checkpoint_or_append(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _enqueue_and_claim(engine, worker_id="worker-stale")
    session_id = uuid4()
    port = _CountingPort(session_id)
    runner = DurableAppendDeliveryRunner(lambda: Session(engine), port)  # type: ignore[arg-type]

    _expire(engine, claimed.token.job_id)

    with pytest.raises(LeaseLostError, match="delivery lease token"):
        runner.deliver(
            token=claimed.token,
            ingestion_session_id=session_id,
            batches=_batch(),
            initial_session_status_raw="ACTIVE",
        )

    assert port.calls == 0
    with Session(engine) as session:
        assert session.get(DeliveryCheckpoint, claimed.token.job_id) is None
        assert session.get(DeliveryBatch, (claimed.token.job_id, 0)) is None
    engine.dispose()


def test_ownership_loss_after_remote_accept_keeps_prepared_for_same_hash_replay(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    first = _enqueue_and_claim(engine, worker_id="worker-a")
    session_id = uuid4()
    batches = _batch()

    class LoseLeaseAfterAcceptPort(_CountingPort):
        def append(self, command):
            result = super().append(command)
            if self.calls == 1:
                _expire(engine, first.token.job_id)
            return result

    port = LoseLeaseAfterAcceptPort(session_id)
    runner = DurableAppendDeliveryRunner(lambda: Session(engine), port)  # type: ignore[arg-type]

    with pytest.raises(LeaseLostError, match="delivery lease token"):
        runner.deliver(
            token=first.token,
            ingestion_session_id=session_id,
            batches=batches,
            initial_session_status_raw="ACTIVE",
        )

    with Session(engine) as session:
        prepared = session.get(DeliveryBatch, (first.token.job_id, 0))
        checkpoint = session.get(DeliveryCheckpoint, first.token.job_id)
        assert prepared is not None and prepared.status == "PREPARED"
        assert checkpoint is not None and checkpoint.next_batch_index == 0

    with Session(engine) as session:
        second = JobCoordinator().claim_next(session, worker_id="worker-b", lease_seconds=120)
        assert second is not None
        assert second.token.job_id == first.token.job_id
        assert second.token.lease_generation == first.token.lease_generation + 1
        session.commit()

    outcomes = runner.deliver(
        token=second.token,
        ingestion_session_id=session_id,
        batches=batches,
    )

    assert port.calls == 2
    assert port.hashes == [batches[0].batch_content_hash, batches[0].batch_content_hash]
    assert outcomes[0].disposition is BatchReplayDisposition.REPLAY_PENDING

    with Session(engine) as session:
        accepted = session.get(DeliveryBatch, (first.token.job_id, 0))
        checkpoint = session.get(DeliveryCheckpoint, first.token.job_id)
        assert accepted is not None and accepted.status == "ACCEPTED"
        assert checkpoint is not None and checkpoint.next_batch_index == 1
    engine.dispose()
