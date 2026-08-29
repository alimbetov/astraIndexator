from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

import grpc

from .contracts import (
    AbortIngestionCommand,
    ActivateDocumentVersionCommand,
    ActivateDocumentVersionResult,
    AppendBlocksCommand,
    AppendBlocksResult,
    AstraVectorTransportError,
    DocumentVectorStatus,
    FinalizeIngestionCommand,
    FinalizeIngestionResult,
    IngestionStatus,
    StartIngestionCommand,
    StartIngestionResult,
    map_session_state,
)
from .generated_loader import GeneratedAstraVectorClient, load_generated_client
from .proto_mapper import AstraVectorProtoMapper


class AstraVectorGrpcError(AstraVectorTransportError):
    def __init__(self, *, code: str, message: str) -> None:
        RuntimeError.__init__(self, f"AstraVector gRPC {code}: {message}")
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
    """Transport adapter for the pinned AstraVectorIngestionFacade generated client."""

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
        control_stub_factory = getattr(self._generated.pb_grpc, "AstraVectorV004ControlStub", None)
        self._control_stub = (
            control_stub_factory(self._channel)
            if control_stub_factory and hasattr(self._channel, "unary_unary")
            else None
        )
        self._mapper = AstraVectorProtoMapper(self._generated.pb)

    @property
    def mapper(self) -> AstraVectorProtoMapper:
        return self._mapper

    def start(self, command: StartIngestionCommand) -> StartIngestionResult:
        request = self._mapper.start_request(command)
        try:
            response = self._stub.StartLogicalDocumentIngestion(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

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

    def append(self, command: AppendBlocksCommand) -> AppendBlocksResult:
        request = self._mapper.append_request(command)
        try:
            response = self._stub.AppendLogicalDocumentBlocks(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        try:
            session_id = UUID(response.ingestion_session_id)
            accepted_blocks = int(response.accepted_blocks)
            accepted_batch_index = int(response.accepted_batch_index)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="AppendLogicalDocumentBlocks returned malformed acknowledgement",
            ) from exc

        if session_id != command.ingestion_session_id:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="AppendLogicalDocumentBlocks returned a different ingestion_session_id",
            )
        if accepted_batch_index != command.batch_index:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message=(
                    "AppendLogicalDocumentBlocks acknowledged unexpected batch index "
                    f"{accepted_batch_index}; expected {command.batch_index}"
                ),
            )
        if accepted_blocks != len(command.blocks):
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message=(
                    "AppendLogicalDocumentBlocks acknowledged unexpected block count "
                    f"{accepted_blocks}; expected {len(command.blocks)}"
                ),
            )

        raw_status = str(getattr(response, "status", ""))
        warnings = tuple(self._warning_text(item) for item in getattr(response, "warnings", ()))
        return AppendBlocksResult(
            ingestion_session_id=session_id,
            raw_status=raw_status,
            state=map_session_state(raw_status),
            accepted_blocks=accepted_blocks,
            accepted_batch_index=accepted_batch_index,
            warnings=warnings,
        )

    def finalize(self, command: FinalizeIngestionCommand) -> FinalizeIngestionResult:
        request = self._mapper.finalize_request(command)
        try:
            response = self._stub.FinalizeLogicalDocumentIngestion(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        document = getattr(response, "document", None)
        if document is None:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="FinalizeLogicalDocumentIngestion returned no document identity",
            )
        operation = getattr(response, "operation", None)
        try:
            access_zone_id = UUID(str(document.access_zone_id))
            document_id = UUID(str(document.document_id))
            document_version = int(document.document_version)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="FinalizeLogicalDocumentIngestion returned malformed document identity",
            ) from exc
        if document_version <= 0:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="FinalizeLogicalDocumentIngestion returned non-positive document_version",
            )

        raw_operation_state = self._operation_state_name(getattr(operation, "state", 0))
        warnings = tuple(self._warning_text(item) for item in getattr(operation, "warnings", ()))
        return FinalizeIngestionResult(
            access_zone_id=access_zone_id,
            document_id=document_id,
            document_version=document_version,
            raw_operation_state=raw_operation_state,
            message=str(getattr(operation, "message", "")),
            operation_id=str(getattr(operation, "operation_id", "")),
            warnings=warnings,
        )

    def abort(self, command: AbortIngestionCommand) -> IngestionStatus:
        request = self._mapper.abort_request(command)
        try:
            response = self._stub.AbortLogicalDocumentIngestion(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        try:
            response_session_id = UUID(str(response.ingestion_session_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="AbortLogicalDocumentIngestion returned malformed ingestion_session_id",
            ) from exc
        if response_session_id != command.ingestion_session_id:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="AbortLogicalDocumentIngestion returned a different ingestion_session_id",
            )

        raw_status = str(getattr(response, "status", ""))
        return IngestionStatus(
            ingestion_session_id=response_session_id,
            raw_status=raw_status,
            state=map_session_state(raw_status),
            received_batches=0,
            received_blocks=0,
            received_bytes=0,
            expires_at="",
        )

    def activate_document_version(
        self, command: ActivateDocumentVersionCommand
    ) -> ActivateDocumentVersionResult:
        if self._control_stub is None:
            raise AstraVectorGrpcError(
                code="INVALID_CLIENT",
                message="generated AstraVector client does not expose ActivateDocumentVersion",
            )
        request = self._mapper.activate_document_version_request(command)
        try:
            response = self._control_stub.ActivateDocumentVersion(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        try:
            response_document_id = UUID(str(response.document_id))
            response_document_version = int(response.document_version)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="ActivateDocumentVersion returned malformed document identity",
            ) from exc
        if response_document_id != command.document_id:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="ActivateDocumentVersion returned a different document_id",
            )
        if response_document_version != command.document_version:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="ActivateDocumentVersion returned a different document_version",
            )
        return ActivateDocumentVersionResult(
            document_id=response_document_id,
            document_version=response_document_version,
            raw_status=str(getattr(response, "status", "")),
        )

    def get_ingestion_status(self, ingestion_session_id: UUID) -> IngestionStatus:
        request = self._mapper.ingestion_status_request(ingestion_session_id)
        try:
            response = self._stub.GetLogicalDocumentIngestionStatus(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        try:
            response_session_id = UUID(str(response.ingestion_session_id))
            received_batches = int(response.received_batches)
            received_blocks = int(response.received_blocks)
            received_bytes = int(response.received_bytes)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetLogicalDocumentIngestionStatus returned malformed response",
            ) from exc
        if response_session_id != ingestion_session_id:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetLogicalDocumentIngestionStatus returned a different ingestion_session_id",
            )
        if min(received_batches, received_blocks, received_bytes) < 0:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetLogicalDocumentIngestionStatus returned negative counters",
            )

        raw_status = str(getattr(response, "status", ""))
        return IngestionStatus(
            ingestion_session_id=response_session_id,
            raw_status=raw_status,
            state=map_session_state(raw_status),
            received_batches=received_batches,
            received_blocks=received_blocks,
            received_bytes=received_bytes,
            expires_at=str(getattr(response, "expires_at", "")),
            error_code=str(getattr(response, "error_code", "")),
            error_message=str(getattr(response, "error_message", "")),
        )

    def get_document_vector_status(
        self,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVectorStatus:
        request = self._mapper.document_vector_status_request(
            access_zone_id=access_zone_id,
            document_id=document_id,
            document_version=document_version,
        )
        try:
            response = self._stub.GetDocumentVectorStatus(
                request,
                timeout=self._config.deadline_seconds,
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        document = getattr(response, "document", None)
        status = getattr(response, "status", None)
        if document is None or status is None:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned no document/status",
            )
        try:
            response_access_zone_id = UUID(str(document.access_zone_id))
            response_document_id = UUID(str(document.document_id))
            response_document_version = int(document.document_version)
            progress = float(status.progress_percent)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned malformed document/status",
            ) from exc
        if (
            response_access_zone_id != access_zone_id
            or response_document_id != document_id
            or response_document_version != document_version
        ):
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned a different document identity",
            )
        if progress < 0.0 or progress > 100.0:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned progress outside 0..100",
            )

        sync = getattr(status, "sync", None)
        try:
            expected_bindings = int(getattr(sync, "expected_bindings", 0))
            synced_bindings = int(getattr(sync, "synced_bindings", 0))
            pending_bindings = int(getattr(sync, "pending_bindings", 0))
            failed_bindings = int(getattr(sync, "failed_bindings", 0))
            outbox_pending = int(getattr(sync, "outbox_pending", 0))
            outbox_retry_pending = int(getattr(sync, "outbox_retry_pending", 0))
            outbox_failed = int(getattr(sync, "outbox_failed", 0))
            qdrant_points_expected = int(getattr(sync, "qdrant_points_expected", 0))
            qdrant_points_found = int(getattr(sync, "qdrant_points_found", 0))
            qdrant_points_missing = int(getattr(sync, "qdrant_points_missing", 0))
            qdrant_points_extra = int(getattr(sync, "qdrant_points_extra", 0))
        except (TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned malformed sync counters",
            ) from exc
        counters = (
            expected_bindings,
            synced_bindings,
            pending_bindings,
            failed_bindings,
            outbox_pending,
            outbox_retry_pending,
            outbox_failed,
            qdrant_points_expected,
            qdrant_points_found,
            qdrant_points_missing,
            qdrant_points_extra,
        )
        if min(counters) < 0:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="GetDocumentVectorStatus returned negative sync counters",
            )

        return DocumentVectorStatus(
            raw_state=self._operation_state_name(getattr(status, "state", 0)),
            progress_percent=progress,
            searchable=bool(getattr(status, "searchable", False)),
            ready_to_activate=bool(getattr(status, "ready_to_activate", False)),
            message=str(getattr(status, "message", "")),
            document_status=str(getattr(sync, "document_status", "")),
            expected_bindings=expected_bindings,
            synced_bindings=synced_bindings,
            pending_bindings=pending_bindings,
            failed_bindings=failed_bindings,
            outbox_pending=outbox_pending,
            outbox_retry_pending=outbox_retry_pending,
            outbox_failed=outbox_failed,
            qdrant_collection_exists=bool(getattr(sync, "qdrant_collection_exists", False)),
            qdrant_points_expected=qdrant_points_expected,
            qdrant_points_found=qdrant_points_found,
            qdrant_points_missing=qdrant_points_missing,
            qdrant_points_extra=qdrant_points_extra,
        )

    def close(self) -> None:
        self._channel.close()

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return tuple((key, value) for key, value in self._config.metadata.items())

    def _operation_state_name(self, value: object) -> str:
        if isinstance(value, int):
            numeric_value = value
        elif isinstance(value, str):
            try:
                numeric_value = int(value)
            except ValueError:
                return value
        else:
            return str(value)

        enum_type = getattr(self._generated.pb, "OperationState", None)
        if enum_type is not None and hasattr(enum_type, "Name"):
            try:
                return str(enum_type.Name(numeric_value))
            except (TypeError, ValueError):
                pass
        return str(value)

    @staticmethod
    def _transport_error(exc: grpc.RpcError) -> AstraVectorGrpcError:
        status_code = exc.code()
        code = status_code.name if status_code is not None else "UNKNOWN"
        details = exc.details() or str(exc)
        return AstraVectorGrpcError(code=code, message=details)

    @staticmethod
    def _warning_text(warning: Any) -> str:
        code = str(getattr(warning, "code", "")).strip()
        message = str(getattr(warning, "message", "")).strip()
        if code and message:
            return f"{code}: {message}"
        return code or message
