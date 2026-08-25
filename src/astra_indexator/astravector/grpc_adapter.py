from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

import grpc

from .contracts import StartIngestionCommand, StartIngestionResult, map_session_state
from .generated_loader import GeneratedAstraVectorClient, load_generated_client
from .proto_mapper import AstraVectorProtoMapper


class AstraVectorGrpcError(RuntimeError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(f"AstraVector gRPC {code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AstraVectorGrpcConfig:
    target: str = "astravector:50051"
    deadline_seconds: float = 30.0
    secure: bool = False
    root_certificates: bytes | None = None
    authority: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValueError("AstraVector gRPC target must not be blank")
        if self.deadline_seconds <= 0:
            raise ValueError("AstraVector gRPC deadline_seconds must be positive")
        for key, value in self.metadata.items():
            if not key.strip() or not value.strip():
                raise ValueError("AstraVector gRPC metadata keys and values must not be blank")


def create_grpc_channel(config: AstraVectorGrpcConfig) -> grpc.Channel:
    options: list[tuple[str, str]] = []
    if config.authority:
        options.append(("grpc.default_authority", config.authority))

    if config.secure:
        credentials = grpc.ssl_channel_credentials(root_certificates=config.root_certificates)
        return grpc.secure_channel(config.target, credentials, options=options)
    return grpc.insecure_channel(config.target, options=options)


class AstraVectorGrpcAdapter:
    """Transport adapter for AstraVectorIngestionFacade.

    M8.2.4 intentionally wires Start first. Append/Finalize/Abort/status calls are added
    in the following slices while reusing the same channel, generated stub and mapper.
    """

    def __init__(
        self,
        config: AstraVectorGrpcConfig,
        *,
        generated: GeneratedAstraVectorClient | None = None,
        channel: grpc.Channel | None = None,
        stub: Any | None = None,
    ) -> None:
        self._config = config
        self._generated = generated or load_generated_client()
        self._channel = channel or create_grpc_channel(config)
        self._stub = stub or self._generated.pb_grpc.AstraVectorIngestionFacadeStub(self._channel)
        self._mapper = AstraVectorProtoMapper(self._generated.pb)

    def start(self, command: StartIngestionCommand) -> StartIngestionResult:
        request = self._mapper.start_request(command)
        try:
            response = self._stub.StartLogicalDocumentIngestion(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            status_code = exc.code()
            code = status_code.name if status_code is not None else "UNKNOWN"
            details = exc.details() or str(exc)
            raise AstraVectorGrpcError(code=code, message=details) from exc

        try:
            session_id = UUID(response.ingestion_session_id)
        except (AttributeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="StartLogicalDocumentIngestion returned invalid ingestion_session_id",
            ) from exc

        raw_status = str(getattr(response, "status", ""))
        warnings = tuple(self._warning_text(item) for item in getattr(response, "warnings", ()))
        return StartIngestionResult(
            ingestion_session_id=session_id,
            raw_status=raw_status,
            state=map_session_state(raw_status),
            expires_at=str(getattr(response, "expires_at", "")),
            warnings=warnings,
        )

    def close(self) -> None:
        self._channel.close()

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return tuple((key, value) for key, value in self._config.metadata.items())

    @staticmethod
    def _warning_text(warning: Any) -> str:
        code = str(getattr(warning, "code", "")).strip()
        message = str(getattr(warning, "message", "")).strip()
        if code and message:
            return f"{code}: {message}"
        return code or message
