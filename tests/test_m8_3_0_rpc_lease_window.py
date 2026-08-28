from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.durable_append_delivery import (
    DurableAppendDeliveryRunner,
    DurableAppendLeaseFence,
    LeaseRpcWindowTooShort,
)
from astra_indexator.astravector.batching import PlannedDeliveryBatch
from astra_indexator.astravector.contracts import LogicalBlock
from astra_indexator.persistence.models import DeliveryBatch
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob

ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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


def _claim(engine):
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://documents/m8-3-window.txt",
                access_zone_code="0600",
            ),
        )
        session.commit()
        job_id = job.id
    with Session(engine) as session:
        claimed = JobCoordinator().claim_next(
            session,
            worker_id="window-worker",
            lease_seconds=120,
        )
        assert claimed is not None
        assert claimed.token.job_id == job_id
        session.commit()
        return claimed


def _set_remaining_lease(engine, job_id, seconds: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE astra_indexator.indexation_job "
                "SET lease_until = now() + make_interval(secs => :seconds) "
                "WHERE id = :job_id"
            ),
            {"seconds": seconds, "job_id": job_id},
        )


def _batch() -> PlannedDeliveryBatch:
    block = LogicalBlock(
        block_id="b-0",
        parent_block_id="",
        block_type="PARAGRAPH",
        text="payload",
        order_index=0,
    )
    return PlannedDeliveryBatch(
        batch_index=0,
        blocks=(block,),
        is_last_batch=True,
        batch_content_hash="a" * 64,
        serialized_bytes=64,
    )


def test_safe_rpc_window_uses_postgresql_time(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claim(engine)
    fence = DurableAppendLeaseFence()

    _set_remaining_lease(engine, claimed.token.job_id, 20)
    with Session(engine) as session:
        with pytest.raises(LeaseRpcWindowTooShort) as error:
            with session.begin():
                fence.assert_safe_rpc_window(
                    session,
                    claimed.token,
                    rpc_deadline_seconds=30,
                    safety_margin_seconds=5,
                )
    assert error.value.required_seconds == 35
    assert 0 < error.value.remaining_seconds < 35

    _set_remaining_lease(engine, claimed.token.job_id, 60)
    with Session(engine) as session:
        with session.begin():
            remaining = fence.assert_safe_rpc_window(
                session,
                claimed.token,
                rpc_deadline_seconds=30,
                safety_margin_seconds=5,
            )
    assert remaining > 35
    engine.dispose()


def test_append_is_not_sent_when_deadline_does_not_fit_lease(database_url: str) -> None:
    engine = create_engine(database_url)
    claimed = _claim(engine)
    _set_remaining_lease(engine, claimed.token.job_id, 20)

    class Port:
        calls = 0

        def append(self, command):
            self.calls += 1
            raise AssertionError("Append must not start outside the safe lease window")

    port = Port()
    runner = DurableAppendDeliveryRunner(
        lambda: Session(engine),
        port,  # type: ignore[arg-type]
        rpc_deadline_seconds=30,
        rpc_safety_margin_seconds=5,
    )

    with pytest.raises(LeaseRpcWindowTooShort):
        runner.deliver(
            token=claimed.token,
            ingestion_session_id=SESSION_ID,
            batches=(_batch(),),
        )

    assert port.calls == 0
    with Session(engine) as session:
        persisted = session.get(DeliveryBatch, (claimed.token.job_id, 0))
        assert persisted is not None
        assert persisted.status == "PREPARED"
    engine.dispose()
