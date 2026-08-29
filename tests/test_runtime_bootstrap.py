from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from astra_indexator.__main__ import main
from astra_indexator.application.astravector_delivery_coordinator import AstraVectorDeliveryInput
from astra_indexator.application.coordinator import ClaimedJob
from astra_indexator.application.delivery_execution import DeliveryExecutionResult
from astra_indexator.application.retry_policy import FailureAction
from astra_indexator.persistence.db import create_session_factory
from astra_indexator.persistence.models import IndexationJob
from astra_indexator.persistence.repository import IndexationJobRepository, NewIndexationJob
from astra_indexator.runtime.composition import build_runtime
from astra_indexator.runtime.config import RuntimeConfig, RuntimeConfigError
from astra_indexator.runtime.db import DatabaseValidationError, validate_database_ready
from astra_indexator.runtime.worker import RuntimeWorker, ShutdownController, UnsupportedRuntimePath

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
                "astra_indexator.prepared_artifact_checkpoint, "
                "astra_indexator.processing_attempt, "
                "astra_indexator.indexation_job CASCADE"
            )
        )
    yield
    engine.dispose()


def test_runtime_config_validates_required_and_invalid_values() -> None:
    with pytest.raises(RuntimeConfigError, match="ASTRA_INDEXATOR_DATABASE_URL is required"):
        RuntimeConfig.from_env({})
    with pytest.raises(RuntimeConfigError, match="PostgreSQL DSN"):
        RuntimeConfig(database_url="sqlite:///local.db")
    with pytest.raises(RuntimeConfigError, match="host:port"):
        RuntimeConfig(
            database_url="postgresql+psycopg://u:p@localhost/db",
            astravector_grpc_target="https://astravector:50051",
        )
    with pytest.raises(RuntimeConfigError, match="must exceed RPC timeout"):
        RuntimeConfig(
            database_url="postgresql+psycopg://u:p@localhost/db",
            lease_seconds=10,
            mutating_rpc_deadline_seconds=9,
            rpc_safety_margin_seconds=1,
        )

    config = RuntimeConfig.from_env(
        {
            "ASTRA_INDEXATOR_DATABASE_URL": "postgresql+psycopg://user:secret@localhost:5432/db",
            "ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET": "astravector:50051",
            "ASTRA_INDEXATOR_WORKER_ID": "worker-runtime-test",
            "ASTRA_INDEXATOR_POLL_INTERVAL_SECONDS": "0.01",
        }
    )

    assert config.worker_id == "worker-runtime-test"
    assert config.sanitized_database_target == "postgresql+psycopg://localhost:5432/db"


def test_database_validation_requires_current_alembic_head(database_url: str) -> None:
    engine = create_engine(database_url)
    assert validate_database_ready(engine) == "0007_m8_delivery_compatibility"
    with pytest.raises(DatabaseValidationError, match="expected other-head"):
        validate_database_ready(engine, expected_revision="other-head")
    engine.dispose()


def test_database_validation_fails_when_database_is_unavailable() -> None:
    engine = create_engine("postgresql+psycopg://user:password@127.0.0.1:1/missing")
    with pytest.raises(DatabaseValidationError, match="PostgreSQL/Alembic validation failed"):
        validate_database_ready(engine)
    engine.dispose()


def test_module_main_fails_without_database_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTRA_INDEXATOR_DATABASE_URL", raising=False)
    assert main() == 2


def test_composition_root_constructs_after_database_validation(database_url: str) -> None:
    runtime = build_runtime(
        RuntimeConfig(
            database_url=database_url,
            worker_id="worker-composition-test",
            poll_interval_seconds=0.01,
        ),
        max_iterations=1,
    )

    try:
        assert runtime.config.worker_id == "worker-composition-test"
        assert runtime.run() == 0
    finally:
        runtime.shutdown.request_shutdown()


class _NoJobCoordinator:
    def __init__(self) -> None:
        self.claims = 0

    def claim_next(self, session, *, worker_id, lease_seconds):
        del session, worker_id, lease_seconds
        self.claims += 1
        return None


class _UnusedPayloadProvider:
    def payload_for(self, session: Session, claimed: ClaimedJob) -> AstraVectorDeliveryInput:
        raise AssertionError("payload must not be requested without a claimed job")


class _UnusedDeliveryExecutor:
    def execute(self, claimed: ClaimedJob, payload: AstraVectorDeliveryInput):
        raise AssertionError("delivery must not execute without a claimed job")


def test_worker_no_job_polling_respects_iteration_limit(database_url: str) -> None:
    engine = create_engine(database_url)
    coordinator = _NoJobCoordinator()
    shutdown = ShutdownController()
    worker = RuntimeWorker(
        session_factory=create_session_factory(engine),
        coordinator=coordinator,  # type: ignore[arg-type]
        payload_provider=_UnusedPayloadProvider(),
        delivery_executor=_UnusedDeliveryExecutor(),  # type: ignore[arg-type]
        failure_handler=None,  # type: ignore[arg-type]
        worker_id="worker-no-job-test",
        lease_seconds=120,
        poll_interval_seconds=0.01,
        shutdown=shutdown,
        logger=__import__("logging").getLogger("test"),
        max_iterations=2,
    )

    worker.run()

    assert coordinator.claims == 2
    engine.dispose()


def test_shutdown_request_stops_worker_before_claim(database_url: str) -> None:
    engine = create_engine(database_url)
    coordinator = _NoJobCoordinator()
    shutdown = ShutdownController()
    shutdown.request_shutdown()
    worker = RuntimeWorker(
        session_factory=create_session_factory(engine),
        coordinator=coordinator,  # type: ignore[arg-type]
        payload_provider=_UnusedPayloadProvider(),
        delivery_executor=_UnusedDeliveryExecutor(),  # type: ignore[arg-type]
        failure_handler=None,  # type: ignore[arg-type]
        worker_id="worker-shutdown-test",
        lease_seconds=120,
        poll_interval_seconds=0.01,
        shutdown=shutdown,
        logger=__import__("logging").getLogger("test"),
    )

    worker.run()

    assert coordinator.claims == 0
    engine.dispose()


class _UnsupportedPayloadProvider:
    def payload_for(self, session: Session, claimed: ClaimedJob) -> AstraVectorDeliveryInput:
        del session, claimed
        raise UnsupportedRuntimePath("no production M7 prepared-artifact runtime payload provider")


class _RecordingDeliveryExecutor:
    def __init__(self) -> None:
        self.executed = False

    def execute(
        self, claimed: ClaimedJob, payload: AstraVectorDeliveryInput
    ) -> DeliveryExecutionResult:
        del claimed, payload
        self.executed = True
        raise AssertionError("unsupported payload must fail before delivery")


def test_failure_path_uses_durable_handler_for_unsupported_runtime_path(database_url: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        job = IndexationJobRepository().create_or_get(
            session,
            NewIndexationJob(
                producer_request_id=uuid4(),
                document_id=uuid4(),
                document_version=1,
                source_uri="seaweed://documents/runtime.txt",
                access_zone_code="0001",
                source_content_hash="a" * 64,
                max_attempts=1,
            ),
        )
        session.commit()
        job_id = job.id

    delivery_executor = _RecordingDeliveryExecutor()
    runtime = build_runtime(
        RuntimeConfig(
            database_url=database_url,
            worker_id="worker-failure-path-test",
            poll_interval_seconds=0.01,
        ),
        max_iterations=1,
    )
    runtime.worker.payload_provider = _UnsupportedPayloadProvider()
    runtime.worker.delivery_executor = delivery_executor

    try:
        runtime.worker.run()
        with Session(engine) as session:
            job = session.get(IndexationJob, job_id)
            assert job is not None
            assert job.status == FailureAction.FAILED.value
            assert job.last_error_code == "UnsupportedRuntimePath"
            assert delivery_executor.executed is False
    finally:
        runtime.shutdown.request_shutdown()
        engine.dispose()
