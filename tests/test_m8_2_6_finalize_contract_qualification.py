from __future__ import annotations

from concurrent import futures
from uuid import UUID

import grpc

from astra_indexator.application.finalize_reconciliation import should_reconcile_finalize_failure
from astra_indexator.astravector.contracts import (
    FinalizeIngestionCommand,
    IngestionSessionState,
)
from astra_indexator.astravector.generated_loader import load_generated_client
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
DEADLINE_SECONDS = 5.0
DEADLINE_JITTER_TOLERANCE_SECONDS = 0.25


def test_finalize_failure_policy_is_fail_closed_and_consistent_with_m8_2_4() -> None:
    assert should_reconcile_finalize_failure(
        AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline after send")
    )
    assert should_reconcile_finalize_failure(
        AstraVectorGrpcError(code="UNAVAILABLE", message="connection lost after send")
    )
    assert should_reconcile_finalize_failure(
        AstraVectorGrpcError(code="ABORTED", message="transport aborted")
    )
    assert should_reconcile_finalize_failure(
        AstraVectorGrpcError(
            code="FAILED_PRECONDITION",
            message="INGESTION_SESSION_FINALIZING: session is already finalizing",
        )
    )
    assert should_reconcile_finalize_failure(
        AstraVectorGrpcError(
            code="FAILED_PRECONDITION",
            message="INGESTION_SESSION_COMPLETED: session already completed",
        )
    )

    assert not should_reconcile_finalize_failure(
        AstraVectorGrpcError(code="CANCELLED", message="client cancelled")
    )
    assert not should_reconcile_finalize_failure(
        AstraVectorGrpcError(code="UNKNOWN", message="opaque transport failure")
    )
    assert not should_reconcile_finalize_failure(
        AstraVectorGrpcError(
            code="FAILED_PRECONDITION",
            message="FINAL_CONTENT_HASH_MISMATCH: deterministic integrity failure",
        )
    )
    assert not should_reconcile_finalize_failure(
        AstraVectorGrpcError(
            code="FAILED_PRECONDITION",
            message="new unqualified server marker",
        )
    )


def _transport() -> tuple[object, grpc.Server, AstraVectorGrpcAdapter]:
    generated = load_generated_client()
    pb = generated.pb
    pb_grpc = generated.pb_grpc

    class Servicer(pb_grpc.AstraVectorIngestionFacadeServicer):
        def __init__(self) -> None:
            self.finalize_request = None
            self.status_request = None
            self.finalize_metadata: tuple[tuple[str, str], ...] = ()
            self.status_metadata: tuple[tuple[str, str], ...] = ()
            self.finalize_time_remaining: float | None = None
            self.status_time_remaining: float | None = None

        @staticmethod
        def _metadata(context: grpc.ServicerContext) -> tuple[tuple[str, str], ...]:
            return tuple((item.key, item.value) for item in context.invocation_metadata())

        def FinalizeLogicalDocumentIngestion(self, request, context):  # type: ignore[no-untyped-def]
            self.finalize_request = request
            self.finalize_metadata = self._metadata(context)
            self.finalize_time_remaining = context.time_remaining()
            return pb.IndexLogicalDocumentResponse(
                document=pb.DocumentRef(
                    access_zone_id=str(ZONE_ID),
                    document_id=str(DOCUMENT_ID),
                    document_version=7,
                ),
                operation=pb.OperationStatus(
                    operation_id="op-finalize-1",
                    state=pb.OPERATION_STATE_VECTORING,
                    message="accepted for vectoring",
                    warnings=(pb.DiagnosticWarningV005(code="W", message="accepted"),),
                ),
            )

        def GetLogicalDocumentIngestionStatus(self, request, context):  # type: ignore[no-untyped-def]
            self.status_request = request
            self.status_metadata = self._metadata(context)
            self.status_time_remaining = context.time_remaining()
            return pb.GetLogicalDocumentIngestionStatusResponse(
                ingestion_session_id=str(SESSION_ID),
                status="FINALIZING",
                received_batches=3,
                received_blocks=17,
                received_bytes=4096,
                expires_at="2026-08-27T00:00:00Z",
            )

    servicer = Servicer()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_AstraVectorIngestionFacadeServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5)
    adapter = AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(
            target=f"127.0.0.1:{port}",
            deadline_seconds=DEADLINE_SECONDS,
            metadata={"x-astra-service": "astra-indexator"},
        ),
        generated=generated,
        channel=channel,
    )
    return servicer, server, adapter


def _assert_deadline(value: float | None) -> None:
    assert value is not None
    assert 0 < value <= DEADLINE_SECONDS + DEADLINE_JITTER_TOLERANCE_SECONDS


def test_real_generated_grpc_finalize_and_status_round_trip() -> None:
    servicer, server, adapter = _transport()
    try:
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
        assert result.operation_id == "op-finalize-1"
        assert result.warnings == ("W: accepted",)

        request = servicer.finalize_request
        assert request is not None
        assert request.ingestion_session_id == str(SESSION_ID)
        assert request.final_content_hash == "ab" * 32
        assert ("x-astra-service", "astra-indexator") in servicer.finalize_metadata
        _assert_deadline(servicer.finalize_time_remaining)

        status = adapter.get_ingestion_status(SESSION_ID)
        assert status.ingestion_session_id == SESSION_ID
        assert status.state is IngestionSessionState.FINALIZING
        assert status.received_batches == 3
        assert status.received_blocks == 17
        assert status.received_bytes == 4096

        status_request = servicer.status_request
        assert status_request is not None
        assert status_request.ingestion_session_id == str(SESSION_ID)
        assert ("x-astra-service", "astra-indexator") in servicer.status_metadata
        _assert_deadline(servicer.status_time_remaining)
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)
