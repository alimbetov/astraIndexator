from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from astra_indexator.application.astravector_delivery_coordinator import (
    AstraVectorDeliveryCoordinator,
)
from astra_indexator.application.coordinator import JobCoordinator
from astra_indexator.application.delivery_execution import AstraVectorDeliveryExecutor
from astra_indexator.application.retry_policy import DurableFailureHandler
from astra_indexator.astravector.batching import DeterministicBatchPlanner
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcAdapter, AstraVectorGrpcConfig
from astra_indexator.persistence.db import create_database_engine, create_session_factory
from astra_indexator.runtime.config import RuntimeConfig
from astra_indexator.runtime.db import validate_database_ready
from astra_indexator.runtime.worker import (
    PreparedArtifactRuntimePayloadProvider,
    RuntimeWorker,
    ShutdownController,
)


@dataclass(slots=True)
class AstraIndexatorRuntime:
    config: RuntimeConfig
    worker: RuntimeWorker
    shutdown: ShutdownController
    logger: logging.Logger
    engine_dispose: object

    def run(self) -> int:
        self.shutdown.install_signal_handlers()
        self.logger.info(
            "AstraIndexator runtime starting",
            extra={
                "worker_id": self.config.worker_id,
                "database": self.config.sanitized_database_target,
                "astravector_grpc_target": self.config.astravector_grpc_target,
            },
        )
        try:
            self.worker.run()
        finally:
            dispose = getattr(self.engine_dispose, "dispose", None)
            if dispose is not None:
                dispose()
            self.logger.info("AstraIndexator runtime shutdown complete")
        return 0


def build_runtime_from_env() -> AstraIndexatorRuntime:
    return build_runtime(RuntimeConfig.from_env())


def build_runtime(
    config: RuntimeConfig,
    *,
    logger: logging.Logger | None = None,
    max_iterations: int | None = None,
) -> AstraIndexatorRuntime:
    logging.basicConfig(
        level=config.numeric_log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime_logger = logger or logging.getLogger("astra_indexator.runtime")
    engine = create_database_engine(config.database_url)
    revision = validate_database_ready(engine)
    runtime_logger.info(
        "database validation succeeded",
        extra={"database": config.sanitized_database_target, "alembic_revision": revision},
    )

    session_factory = create_session_factory(engine)
    coordinator = JobCoordinator()
    failure_handler = DurableFailureHandler(session_factory, coordinator=coordinator)
    delivery_executor = _LazyDeliveryExecutor(
        lambda: _build_delivery_executor(config, engine, coordinator, failure_handler)
    )
    shutdown = ShutdownController()
    worker = RuntimeWorker(
        session_factory=session_factory,
        coordinator=coordinator,
        payload_provider=PreparedArtifactRuntimePayloadProvider(),
        delivery_executor=delivery_executor,
        failure_handler=failure_handler,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
        shutdown=shutdown,
        logger=runtime_logger,
        max_iterations=max_iterations,
    )
    return AstraIndexatorRuntime(
        config=config,
        worker=worker,
        shutdown=shutdown,
        logger=runtime_logger,
        engine_dispose=engine,
    )


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


class _LazyDeliveryExecutor:
    def __init__(self, factory: Callable[[], AstraVectorDeliveryExecutor]) -> None:
        self._factory = factory
        self._executor: AstraVectorDeliveryExecutor | None = None

    def execute(self, claimed, payload):  # type: ignore[no-untyped-def]
        if self._executor is None:
            self._executor = self._factory()
        return self._executor.execute(claimed, payload)


def _build_delivery_executor(
    config: RuntimeConfig,
    engine: Engine,
    coordinator: JobCoordinator,
    failure_handler: DurableFailureHandler,
) -> AstraVectorDeliveryExecutor:
    grpc_config = AstraVectorGrpcConfig(
        target=config.astravector_grpc_target,
        deadline_seconds=config.mutating_rpc_deadline_seconds,
    )
    port = AstraVectorGrpcAdapter(grpc_config)
    planner = DeterministicBatchPlanner(port.mapper)
    delivery_coordinator = AstraVectorDeliveryCoordinator(
        lambda: Session(engine),
        port,
        planner,
        job_coordinator=coordinator,
        mutating_rpc_deadline_seconds=config.mutating_rpc_deadline_seconds,
        rpc_safety_margin_seconds=config.rpc_safety_margin_seconds,
    )
    return AstraVectorDeliveryExecutor(delivery_coordinator, failure_handler)
