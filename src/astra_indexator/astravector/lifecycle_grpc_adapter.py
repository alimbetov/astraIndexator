from __future__ import annotations

from uuid import UUID

import grpc

from .contracts import DeleteDocumentCommand, DeleteDocumentResult
from .grpc_adapter import AstraVectorGrpcAdapter, AstraVectorGrpcError


class AstraVectorLifecycleGrpcAdapter(AstraVectorGrpcAdapter):
    """M9 extension over the public AstraVectorIngestionFacade lifecycle RPCs."""

    def delete_document(self, command: DeleteDocumentCommand) -> DeleteDocumentResult:
        request = self.mapper.delete_document_request(command)
        try:
            response = self._stub.DeleteDocumentVectorsFacade(  # type: ignore[attr-defined]
                request,
                timeout=self._config.deadline_seconds,  # type: ignore[attr-defined]
                metadata=self._metadata(),
            )
        except grpc.RpcError as exc:
            raise self._transport_error(exc) from exc

        document = getattr(response, "document", None)
        operation = getattr(response, "operation", None)
        if document is None or operation is None:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="DeleteDocumentVectorsFacade returned no document/operation",
            )

        try:
            access_zone_id = UUID(str(document.access_zone_id))
            document_id = UUID(str(document.document_id))
            document_version = int(document.document_version)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="DeleteDocumentVectorsFacade returned malformed document identity",
            ) from exc

        if (
            access_zone_id != command.access_zone_id
            or document_id != command.document_id
            or document_version != command.document_version
        ):
            raise AstraVectorGrpcError(
                code="INVALID_RESPONSE",
                message="DeleteDocumentVectorsFacade returned a different document identity",
            )

        warnings = tuple(
            self._warning_text(item)
            for item in getattr(operation, "warnings", ())
        )
        errors = tuple(
            self._operation_error_text(item)
            for item in getattr(operation, "errors", ())
        )
        return DeleteDocumentResult(
            access_zone_id=access_zone_id,
            document_id=document_id,
            document_version=document_version,
            raw_operation_state=self._operation_state_name(
                getattr(operation, "state", 0)
            ),
            operation_id=str(getattr(operation, "operation_id", "")),
            message=str(getattr(operation, "message", "")),
            warnings=warnings,
            errors=errors,
        )

    @staticmethod
    def _operation_error_text(error: object) -> str:
        code = str(getattr(error, "code", "")).strip()
        message = str(getattr(error, "message", "")).strip()
        retryable = bool(getattr(error, "retryable", False))
        prefix = f"{code}: " if code else ""
        suffix = " [retryable]" if retryable else ""
        return f"{prefix}{message}{suffix}".strip()
