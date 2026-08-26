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

from astra_indexator.application.append_delivery import AppendDeliveryRunner
from astra_indexator.astravector.batching import (
    DeterministicBatchPlanner,
    PlannedDeliveryBatch,
)
from astra_indexator.astravector.contracts import (
    AppendBlocksResult,
    IngestionSessionState,
    LogicalBlock,
)
from astra_indexator.persistence.delivery import (
    BatchReplayDisposition,
    DeliveryBatchRepository,
    DeliveryIntegrityError,
)
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


def _block(index: int, text_value: str | None = None) -> LogicalBlock:
    return LogicalBlock(
        block_id=f"b-{index}",
        parent_block_id="",
        block_type="PARAGRAPH",
        text=text_value or f"block {index}",
        order_index=index,
    )


def test_deterministic_batching_is_stable_across_input_order() -> None:
    planner = DeterministicBatchPlanner(_HashMapper(), max_blocks_per_batch=2)  # type: ignore[arg-type]
    first = planner.plan([_block(3), _block(1), _block(2), _block(0)])
    second = planner.plan([_block(0), _block(2), _block(1), _block(3)])

    assert [batch.batch_index for batch in first] == [0, 1]
    assert [[block.order_index for block in batch.blocks] for batch in first] == [[0, 1], [2, 3]]
    assert [batch.batch_content_hash for batch in first] == [
        batch.batch_content_hash for batch in second
    ]
    assert first[-1].is_last_batch is True
    assert first[0].is_last_batch is False


def _enqueue(engine) -> UUID:
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                access_zone_code="0600",
                source_uri="minio://documents/m8-2-5.txt",
            ),
        )
        session.commit()
        return job.id


def _planned(index: int, *, content_hash: str, is_last: bool = True) -> PlannedDeliveryBatch:
    return PlannedDeliveryBatch(
        batch_index=index,
        blocks=(_block(index),),
        is_last_batch=is_last,
        batch_content_hash=content_hash,
        serialized_bytes=32,
    )


class _CrashAfterRemoteAcceptPort:
    def __init__(self, ingestion_session_id: UUID) -> None:
        self.ingestion_session_id = ingestion_session_id
        self.calls = 0
        self.accepted_hashes: list[str] = []

    def append(self, command):
        self.calls += 1
        self.accepted_hashes.append(command.batch_content_hash)
        if self.calls == 1:
            raise RuntimeError("simulated worker crash after remote acceptance")
        return AppendBlocksResult(
            ingestion_session_id=self.ingestion_session_id,
            raw_status="ACTIVE",
            state=IngestionSessionState.ACTIVE,
            accepted_blocks=len(command.blocks),
            accepted_batch_index=command.batch_index,
        )


def test_crash_after_remote_accept_replays_same_index_and_hash(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine)
    ingestion_session_id = uuid4()
    batch = _planned(0, content_hash="a" * 64)
    port = _CrashAfterRemoteAcceptPort(ingestion_session_id)
    runner = AppendDeliveryRunner(lambda: Session(engine), port)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="simulated worker crash"):
        runner.deliver(
            job_id=job_id,
            ingestion_session_id=ingestion_session_id,
            batches=(batch,),
            initial_session_status_raw="ACTIVE",
        )

    with Session(engine) as session:
        persisted = session.get(DeliveryBatch, (job_id, 0))
        checkpoint = session.get(DeliveryCheckpoint, job_id)
        assert persisted is not None
        assert persisted.status == "PREPARED"
        assert checkpoint is not None
        assert checkpoint.next_batch_index == 0
        assert checkpoint.last_accepted_batch_index is None

    outcomes = runner.deliver(
        job_id=job_id,
        ingestion_session_id=ingestion_session_id,
        batches=(batch,),
    )

    assert port.calls == 2
    assert port.accepted_hashes == ["a" * 64, "a" * 64]
    assert outcomes[0].disposition is BatchReplayDisposition.REPLAY_PENDING

    with Session(engine) as session:
        persisted = session.get(DeliveryBatch, (job_id, 0))
        checkpoint = session.get(DeliveryCheckpoint, job_id)
        assert persisted is not None
        assert persisted.status == "ACCEPTED"
        assert persisted.accepted_at is not None
        assert checkpoint is not None
        assert checkpoint.next_batch_index == 1
        assert checkpoint.last_accepted_batch_index == 0
    engine.dispose()


def test_same_index_different_hash_is_integrity_failure(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine)
    ingestion_session_id = uuid4()
    repo = DeliveryBatchRepository()
    original = _planned(0, content_hash="b" * 64)
    conflicting = _planned(0, content_hash="c" * 64)

    with Session(engine) as session:
        with session.begin():
            repo.bind_session(
                session,
                job_id=job_id,
                ingestion_session_id=ingestion_session_id,
            )
            repo.prepare_batch(session, job_id=job_id, batch=original)

    with Session(engine) as session:
        with pytest.raises(DeliveryIntegrityError, match="different batch_content_hash"):
            with session.begin():
                repo.prepare_batch(session, job_id=job_id, batch=conflicting)
    engine.dispose()


def test_accepted_batch_is_skipped_without_second_network_call(database_url: str) -> None:
    engine = create_engine(database_url)
    job_id = _enqueue(engine)
    ingestion_session_id = uuid4()
    batch = _planned(0, content_hash="d" * 64)

    class Port:
        calls = 0

        def append(self, command):
            self.calls += 1
            return AppendBlocksResult(
                ingestion_session_id=ingestion_session_id,
                raw_status="ACTIVE",
                state=IngestionSessionState.ACTIVE,
                accepted_blocks=1,
                accepted_batch_index=0,
            )

    port = Port()
    runner = AppendDeliveryRunner(lambda: Session(engine), port)  # type: ignore[arg-type]
    runner.deliver(
        job_id=job_id,
        ingestion_session_id=ingestion_session_id,
        batches=(batch,),
    )
    outcomes = runner.deliver(
        job_id=job_id,
        ingestion_session_id=ingestion_session_id,
        batches=(batch,),
    )

    assert port.calls == 1
    assert outcomes[0].disposition is BatchReplayDisposition.ALREADY_ACCEPTED
    assert outcomes[0].remote_result is None
    engine.dispose()
