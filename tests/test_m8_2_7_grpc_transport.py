from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.astravector.contracts import AbortIngestionCommand, IngestionSessionState
from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_SESSION_ID = UUID("99999999-9999-9999-9999-999999999999")


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _Channel:
    def close(self) -> None:
        return None


class _Stub:
    def __init__(self, response_session_id: UUID = SESSION_ID) -> None:
        self.response_session_id = response_session_id
        self.abort_calls: list[object] = []

    def AbortLogicalDocumentIngestion(self, request: object, **kwargs: object) -> object:
        self.abort_calls.append(request)
        return SimpleNamespace(
            ingestion_session_id=str(self.response_session_id),
            status="ABORTED",
        )


def _adapter(stub: _Stub) -> AstraVectorGrpcAdapter:
    pb = SimpleNamespace(AbortLogicalDocumentIngestionRequest=_Message)
    generated = GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]
    return AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(deadline_seconds=5.0),
        generated=generated,
        channel=_Channel(),  # type: ignore[arg-type]
        stub=stub,
    )


def test_abort_uses_real_facade_rpc_and_preserves_session_identity() -> None:
    stub = _Stub()
    adapter = _adapter(stub)

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


def test_abort_rejects_ack_for_different_ingestion_session() -> None:
    adapter = _adapter(_Stub(response_session_id=OTHER_SESSION_ID))

    with pytest.raises(AstraVectorGrpcError, match="different ingestion_session_id"):
        adapter.abort(
            AbortIngestionCommand(
                ingestion_session_id=SESSION_ID,
                reason="worker recovery",
            )
        )
