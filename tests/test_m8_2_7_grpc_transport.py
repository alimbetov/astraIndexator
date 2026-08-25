from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from astra_indexator.astravector.contracts import AbortIngestionCommand, IngestionSessionState
from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcAdapter, AstraVectorGrpcConfig

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _Channel:
    def close(self) -> None:
        return None


class _Stub:
    def __init__(self) -> None:
        self.abort_calls: list[object] = []

    def AbortLogicalDocumentIngestion(self, request: object, **kwargs: object) -> object:
        self.abort_calls.append(request)
        return SimpleNamespace(
            ingestion_session_id=str(SESSION_ID),
            status="ABORTED",
        )


def test_abort_uses_real_facade_rpc_and_preserves_session_identity() -> None:
    stub = _Stub()
    pb = SimpleNamespace(AbortLogicalDocumentIngestionRequest=_Message)
    generated = GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]
    adapter = AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(deadline_seconds=5.0),
        generated=generated,
        channel=_Channel(),  # type: ignore[arg-type]
        stub=stub,
    )

    status = adapter.abort(
        AbortIngestionCommand(
            ingestion_session_id=SESSION_ID,
            reason="worker recovery",
        )
    )

    assert status.ingestion_session_id == SESSION_ID
    assert status.state is IngestionSessionState.ABORTED
    assert len(stub.abort_calls) == 1
    request = stub.abort_calls[0]
    assert request.ingestion_session_id == str(SESSION_ID)
    assert request.reason == "worker recovery"
