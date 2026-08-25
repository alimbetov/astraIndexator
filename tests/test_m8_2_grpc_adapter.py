from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import grpc
import pytest

from astra_indexator.astravector.contracts import IngestionSessionState, StartIngestionCommand
from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _Channel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Stub:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[object, float, tuple[tuple[str, str], ...]]] = []

    def StartLogicalDocumentIngestion(
        self,
        request: object,
        *,
        timeout: float,
        metadata: tuple[tuple[str, str], ...],
    ) -> object:
        self.calls.append((request, timeout, metadata))
        return self.response


def _generated() -> GeneratedAstraVectorClient:
    pb = SimpleNamespace(StartLogicalDocumentIngestionRequest=_Message)
    return GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]


def _command() -> StartIngestionCommand:
    return StartIngestionCommand(
        access_zone_id=None,
        access_zone_code="1500",
        document_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        document_version=3,
        source_uri="minio://docs/a.pdf",
        file_name="a.pdf",
        content_hash="ab" * 32,
        idempotency_key="astraindexator:a:v3",
        total_bytes_estimate=100,
        total_blocks_estimate=2,
        total_pages_estimate=1,
        metadata={"source": "test"},
        ttl_days=0,
    )


def test_start_calls_generated_stub_with_deadline_and_metadata() -> None:
    response = SimpleNamespace(
        ingestion_session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        status="ACTIVE",
        expires_at="2026-08-26T00:00:00Z",
        warnings=(SimpleNamespace(code="W1", message="warning"),),
    )
    stub = _Stub(response)
    channel = _Channel()
    config = AstraVectorGrpcConfig(
        target="astravector:50051",
        deadline_seconds=12.5,
        metadata={"x-astra-service": "astra-indexator"},
    )
    adapter = AstraVectorGrpcAdapter(
        config,
        generated=_generated(),
        channel=channel,  # type: ignore[arg-type]
        stub=stub,
    )

    result = adapter.start(_command())

    assert result.ingestion_session_id == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert result.state == IngestionSessionState.ACTIVE
    assert result.warnings == ("W1: warning",)
    request, timeout, metadata = stub.calls[0]
    assert request.access_zone_code == "1500"
    assert request.ttl_days == 0
    assert timeout == 12.5
    assert metadata == (("x-astra-service", "astra-indexator"),)

    adapter.close()
    assert channel.closed is True


def test_start_unknown_server_state_is_forward_compatible() -> None:
    response = SimpleNamespace(
        ingestion_session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        status="PAUSED",
        expires_at="",
        warnings=(),
    )
    adapter = AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(),
        generated=_generated(),
        channel=_Channel(),  # type: ignore[arg-type]
        stub=_Stub(response),
    )
    assert adapter.start(_command()).state == IngestionSessionState.UNKNOWN


def test_start_rejects_invalid_session_id_from_server() -> None:
    response = SimpleNamespace(
        ingestion_session_id="not-a-uuid",
        status="ACTIVE",
        expires_at="",
        warnings=(),
    )
    adapter = AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(),
        generated=_generated(),
        channel=_Channel(),  # type: ignore[arg-type]
        stub=_Stub(response),
    )
    with pytest.raises(AstraVectorGrpcError, match="INVALID_RESPONSE"):
        adapter.start(_command())


def test_config_rejects_invalid_deadline_and_blank_metadata() -> None:
    with pytest.raises(ValueError, match="deadline"):
        AstraVectorGrpcConfig(deadline_seconds=0)
    with pytest.raises(ValueError, match="metadata"):
        AstraVectorGrpcConfig(metadata={"x-token": ""})


def test_adapter_translates_grpc_rpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Rpc(grpc.RpcError):
        def code(self):  # type: ignore[no-untyped-def]
            return grpc.StatusCode.UNAVAILABLE

        def details(self):  # type: ignore[no-untyped-def]
            return "temporarily unavailable"

    class _FailingStub:
        def StartLogicalDocumentIngestion(self, *args: object, **kwargs: object) -> object:
            raise _Rpc()

    adapter = AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(),
        generated=_generated(),
        channel=_Channel(),  # type: ignore[arg-type]
        stub=_FailingStub(),
    )
    with pytest.raises(AstraVectorGrpcError) as exc_info:
        adapter.start(_command())
    assert exc_info.value.code == "UNAVAILABLE"
