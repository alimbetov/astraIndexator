from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from astra_indexator.astravector.contracts import DeleteDocumentCommand
from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcConfig
from astra_indexator.astravector.lifecycle_grpc_adapter import (
    AstraVectorLifecycleGrpcAdapter,
)

ZONE_ID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _OperationState:
    @staticmethod
    def Name(value: int) -> str:
        return {11: "OPERATION_STATE_DELETE_SCHEDULED"}.get(value, str(value))


class _Channel:
    def close(self) -> None:
        return None


class _Stub:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def DeleteDocumentVectorsFacade(self, request: object, **kwargs: object) -> object:
        self.requests.append(request)
        return SimpleNamespace(
            document=SimpleNamespace(
                access_zone_id=str(ZONE_ID),
                document_id=str(DOCUMENT_ID),
                document_version=7,
            ),
            operation=SimpleNamespace(
                operation_id="delete-op",
                state=11,
                message="scheduled",
                warnings=[],
                errors=[],
            ),
        )


def test_delete_uses_pinned_public_ingestion_facade() -> None:
    pb = SimpleNamespace(
        RequestContext=_Message,
        DocumentRef=_Message,
        DeleteDocumentVectorsFacadeRequest=_Message,
        OperationState=_OperationState,
    )
    generated = GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]
    stub = _Stub()
    adapter = AstraVectorLifecycleGrpcAdapter(
        AstraVectorGrpcConfig(deadline_seconds=5.0),
        generated=generated,
        channel=_Channel(),  # type: ignore[arg-type]
        stub=stub,
    )

    result = adapter.delete_document(
        DeleteDocumentCommand(
            access_zone_id=ZONE_ID,
            document_id=DOCUMENT_ID,
            document_version=7,
            reason="retention cleanup",
            idempotency_key="delete-123",
            correlation_id="corr-123",
        )
    )

    assert result.document_id == DOCUMENT_ID
    assert result.raw_operation_state == "OPERATION_STATE_DELETE_SCHEDULED"
    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request.context.idempotency_key == "delete-123"
    assert request.context.correlation_id == "corr-123"
    assert request.context.caller_service == "astra-indexator"
    assert request.document.access_zone_id == str(ZONE_ID)
    assert request.document.document_id == str(DOCUMENT_ID)
    assert request.document.document_version == 7
    assert request.reason == "retention cleanup"
