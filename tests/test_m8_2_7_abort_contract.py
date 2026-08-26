from __future__ import annotations

from concurrent import futures
from uuid import UUID

import grpc
import pytest

from astra_indexator.astravector.abort_policy import (
    AbortFailureDisposition,
    classify_abort_failure,
)
from astra_indexator.astravector.contracts import AbortIngestionCommand, IngestionSessionState
from astra_indexator.astravector.generated_loader import load_generated_client
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_SESSION_ID = UUID("99999999-9999-9999-9999-999999999999")
DEADLINE_SECONDS = 5.0
DEADLINE_JITTER_TOLERANCE_SECONDS = 0.25


def _command() -> AbortIngestionCommand:
    return AbortIngestionCommand(
        ingestion_session_id=SESSION_ID,
        reason="operator requested cancellation",
    )


def _transport() -> tuple[object, grpc.Server, AstraVectorGrpcAdapter]:
    generated = load_generated_client()
    pb = generated.pb
    pb_grpc = generated.pb_grpc

    class Servicer(pb_grpc.AstraVectorIngestionFacadeServicer):
        def __init__(self) -> None:
            self.abort_request = None
            self.abort_metadata: tuple[tuple[str, str], ...] = ()
            self.abort_time_remaining: float | None = None
            self.response_session_id = SESSION_ID
            self.response_status = "ABORTED"
            self.failure_code: grpc.StatusCode | None = None
            self.failure_message = ""

        def AbortLogicalDocumentIngestion(self, request, context):  # type: ignore[no-untyped-def]
            self.abort_request = request
            self.abort_metadata = tuple(
                (item.key, item.value) for item in context.invocation_metadata()
            )
            self.abort_time_remaining = context.time_remaining()
            if self.failure_code is not None:
                context.abort(self.failure_code, self.failure_message)
            return pb.AbortLogicalDocumentIngestionResponse(
                ingestion_session_id=str(self.response_session_id),
                status=self.response_status,
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


def test_real_generated_grpc_abort_round_trip_preserves_identity_reason_and_deadline() -> None:
    servicer, server, adapter = _transport()
    try:
        status = adapter.abort(_command())

        assert status.ingestion_session_id == SESSION_ID
        assert status.raw_status == "ABORTED"
        assert status.state is IngestionSessionState.ABORTED

        request = servicer.abort_request
        assert request is not None
        assert request.ingestion_session_id == str(SESSION_ID)
        assert request.reason == "operator requested cancellation"
        assert ("x-astra-service", "astra-indexator") in servicer.abort_metadata
        assert servicer.abort_time_remaining is not None
        assert 0 < servicer.abort_time_remaining <= (
            DEADLINE_SECONDS + DEADLINE_JITTER_TOLERANCE_SECONDS
        )
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)


def test_abort_repeated_success_is_idempotent_at_wire_boundary() -> None:
    _, server, adapter = _transport()
    try:
        first = adapter.abort(_command())
        second = adapter.abort(_command())
        assert first.state is IngestionSessionState.ABORTED
        assert second.state is IngestionSessionState.ABORTED
        assert first.ingestion_session_id == second.ingestion_session_id == SESSION_ID
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)


def test_abort_rejects_response_for_different_session() -> None:
    servicer, server, adapter = _transport()
    try:
        servicer.response_session_id = OTHER_SESSION_ID
        with pytest.raises(AstraVectorGrpcError) as exc_info:
            adapter.abort(_command())
        assert exc_info.value.code == "INVALID_RESPONSE"
        assert "different ingestion_session_id" in exc_info.value.message
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (grpc.StatusCode.DEADLINE_EXCEEDED, "deadline"),
        (grpc.StatusCode.UNAVAILABLE, "connection lost after send"),
        (grpc.StatusCode.ABORTED, "INGESTION_SESSION_STATE_CHANGED"),
    ],
)
def test_ambiguous_abort_failures_require_status_reconciliation(
    code: grpc.StatusCode, message: str
) -> None:
    servicer, server, adapter = _transport()
    try:
        servicer.failure_code = code
        servicer.failure_message = message
        with pytest.raises(AstraVectorGrpcError) as exc_info:
            adapter.abort(_command())
        assert classify_abort_failure(exc_info.value) is AbortFailureDisposition.RECONCILE_STATUS
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)


@pytest.mark.parametrize(
    "message",
    [
        "INGESTION_SESSION_FINALIZING",
        "INGESTION_SESSION_COMPLETED",
        "INGESTION_SESSION_FAILED",
        "INGESTION_SESSION_EXPIRED",
    ],
)
def test_terminal_abort_preconditions_fail_closed(message: str) -> None:
    error = AstraVectorGrpcError(code="FAILED_PRECONDITION", message=message)
    assert classify_abort_failure(error) is AbortFailureDisposition.PERMANENT_FAILURE


def test_unknown_abort_failure_is_permanent_by_default() -> None:
    error = AstraVectorGrpcError(code="UNKNOWN", message="unqualified abort failure")
    assert classify_abort_failure(error) is AbortFailureDisposition.PERMANENT_FAILURE
