from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from astra_indexator.astravector.contracts import (
    FinalizeIngestionCommand,
    IngestionSessionState,
)
from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcAdapter, AstraVectorGrpcConfig

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _OperationState:
    @staticmethod
    def Name(value: int) -> str:
        return {
            3: "OPERATION_STATE_VECTORING",
            7: "OPERATION_STATE_ACTIVE",
        }[value]


class _Channel:
    def close(self) -> None:
        return None


class _Stub:
    def __init__(self) -> None:
        self.finalize_calls: list[object] = []
        self.status_calls: list[object] = []
        self.vector_calls: list[object] = []

    def FinalizeLogicalDocumentIngestion(self, request: object, **kwargs: object) -> object:
        self.finalize_calls.append(request)
        return SimpleNamespace(
            document=SimpleNamespace(
                access_zone_id=str(ZONE_ID),
                document_id=str(DOCUMENT_ID),
                document_version=7,
            ),
            operation=SimpleNamespace(
                operation_id="op-1",
                state=3,
                message="vectoring",
                warnings=(SimpleNamespace(code="W", message="accepted"),),
            ),
        )

    def GetLogicalDocumentIngestionStatus(self, request: object, **kwargs: object) -> object:
        self.status_calls.append(request)
        return SimpleNamespace(
            ingestion_session_id=str(SESSION_ID),
            status="FINALIZING",
            received_batches=4,
            received_blocks=123,
            received_bytes=4567,
            expires_at="2026-08-26T00:00:00Z",
            error_code="",
            error_message="",
        )

    def GetDocumentVectorStatus(self, request: object, **kwargs: object) -> object:
        self.vector_calls.append(request)
        return SimpleNamespace(
            document=SimpleNamespace(
                access_zone_id=str(ZONE_ID),
                document_id=str(DOCUMENT_ID),
                document_version=7,
            ),
            status=SimpleNamespace(
                state=7,
                progress_percent=100.0,
                searchable=True,
                ready_to_activate=True,
                message="ready",
            ),
        )


def _adapter(stub: _Stub) -> AstraVectorGrpcAdapter:
    pb = SimpleNamespace(
        FinalizeLogicalDocumentIngestionRequest=_Message,
        GetLogicalDocumentIngestionStatusRequest=_Message,
        GetDocumentVectorStatusRequest=_Message,
        RequestContext=_Message,
        DocumentRef=_Message,
        OperationState=_OperationState,
    )
    generated = GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]
    return AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(deadline_seconds=5.0),
        generated=generated,
        channel=_Channel(),  # type: ignore[arg-type]
        stub=stub,
    )


def test_finalize_uses_real_facade_rpc_and_maps_index_response() -> None:
    stub = _Stub()
    adapter = _adapter(stub)

    result = adapter.finalize(
        FinalizeIngestionCommand(
            ingestion_session_id=SESSION_ID,
            final_content_hash="ab" * 32,
        )
    )

    assert result.access_zone_id == ZONE_ID
    assert result.document_id == DOCUMENT_ID
    assert result.document_version == 7
    assert result.raw_operation_state == "OPERATION_STATE_VECTORING"
    assert result.warnings == ("W: accepted",)
    assert len(stub.finalize_calls) == 1
    request = stub.finalize_calls[0]
    assert request.ingestion_session_id == str(SESSION_ID)
    assert request.final_content_hash == "ab" * 32


def test_ingestion_status_rpc_preserves_reconciliation_state_and_counters() -> None:
    stub = _Stub()
    adapter = _adapter(stub)

    status = adapter.get_ingestion_status(SESSION_ID)

    assert status.state is IngestionSessionState.FINALIZING
    assert status.received_batches == 4
    assert status.received_blocks == 123
    assert status.received_bytes == 4567
    assert len(stub.status_calls) == 1


def test_vector_status_rpc_is_separate_from_finalize_ack() -> None:
    stub = _Stub()
    adapter = _adapter(stub)

    status = adapter.get_document_vector_status(
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=7,
    )

    assert status.raw_state == "OPERATION_STATE_ACTIVE"
    assert status.progress_percent == 100.0
    assert status.searchable is True
    assert status.ready_to_activate is True
    assert len(stub.vector_calls) == 1
