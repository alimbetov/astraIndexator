from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class RuntimeConfigError(ValueError):
    """Raised when runtime configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    database_url: str
    astravector_grpc_target: str = "astravector:50051"
    worker_id: str = ""
    lease_seconds: int = 120
    poll_interval_seconds: float = 1.0
    mutating_rpc_deadline_seconds: float = 30.0
    rpc_safety_margin_seconds: float = 5.0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise RuntimeConfigError("ASTRA_INDEXATOR_DATABASE_URL is required")
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeConfigError("ASTRA_INDEXATOR_DATABASE_URL must be a PostgreSQL DSN")
        _validate_grpc_target(self.astravector_grpc_target)
        if not self.worker_id.strip():
            object.__setattr__(self, "worker_id", _default_worker_id())
        if self.lease_seconds <= 0:
            raise RuntimeConfigError("ASTRA_INDEXATOR_LEASE_SECONDS must be positive")
        if self.poll_interval_seconds <= 0:
            raise RuntimeConfigError("ASTRA_INDEXATOR_POLL_INTERVAL_SECONDS must be positive")
        if self.mutating_rpc_deadline_seconds <= 0:
            raise RuntimeConfigError("ASTRA_INDEXATOR_RPC_TIMEOUT_SECONDS must be positive")
        if self.rpc_safety_margin_seconds < 0:
            raise RuntimeConfigError(
                "ASTRA_INDEXATOR_RPC_SAFETY_MARGIN_SECONDS must be non-negative"
            )
        if (
            self.lease_seconds
            <= self.mutating_rpc_deadline_seconds + self.rpc_safety_margin_seconds
        ):
            raise RuntimeConfigError(
                "ASTRA_INDEXATOR_LEASE_SECONDS must exceed RPC timeout plus safety margin"
            )
        if _log_level(self.log_level) is None:
            raise RuntimeConfigError("ASTRA_INDEXATOR_LOG_LEVEL must be a valid logging level")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "RuntimeConfig":
        env = environ if environ is not None else os.environ
        return cls(
            database_url=_required(env, "ASTRA_INDEXATOR_DATABASE_URL"),
            astravector_grpc_target=env.get(
                "ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET", "astravector:50051"
            ),
            worker_id=env.get("ASTRA_INDEXATOR_WORKER_ID", ""),
            lease_seconds=_int(env, "ASTRA_INDEXATOR_LEASE_SECONDS", 120),
            poll_interval_seconds=_float(env, "ASTRA_INDEXATOR_POLL_INTERVAL_SECONDS", 1.0),
            mutating_rpc_deadline_seconds=_float(env, "ASTRA_INDEXATOR_RPC_TIMEOUT_SECONDS", 30.0),
            rpc_safety_margin_seconds=_float(env, "ASTRA_INDEXATOR_RPC_SAFETY_MARGIN_SECONDS", 5.0),
            log_level=env.get("ASTRA_INDEXATOR_LOG_LEVEL", "INFO"),
        )

    @property
    def sanitized_database_target(self) -> str:
        parsed = urlsplit(self.database_url)
        host = parsed.hostname or "localhost"
        port = f":{parsed.port}" if parsed.port is not None else ""
        path = parsed.path or ""
        return f"{parsed.scheme}://{host}{port}{path}"

    @property
    def numeric_log_level(self) -> int:
        level = _log_level(self.log_level)
        if level is None:
            raise RuntimeConfigError("ASTRA_INDEXATOR_LOG_LEVEL must be a valid logging level")
        return level


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise RuntimeConfigError(f"{name} is required")
    return value


def _int(env: dict[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name} must be an integer") from exc


def _float(env: dict[str, str], name: str, default: float) -> float:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name} must be numeric") from exc


def _log_level(value: str) -> int | None:
    level = logging.getLevelName(value.strip().upper())
    return level if isinstance(level, int) else None


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _validate_grpc_target(value: str) -> None:
    target = value.strip()
    if not target:
        raise RuntimeConfigError("ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET must not be blank")
    if "://" in target:
        raise RuntimeConfigError(
            "ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET must be host:port, not a URL"
        )
    host, separator, port = target.rpartition(":")
    if not separator or not host.strip() or not port.strip():
        raise RuntimeConfigError("ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET must be host:port")
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise RuntimeConfigError("AstraVector gRPC target port must be an integer") from exc
    if not 0 < parsed_port < 65536:
        raise RuntimeConfigError("AstraVector gRPC target port must be between 1 and 65535")
