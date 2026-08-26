from __future__ import annotations

from concurrent import futures
from uuid import UUID

import grpc
import pytest

from astra_indexator.astravector.contracts import (
    AppendBlocksCommand,
    IngestionSessionState,
    LogicalBlock,
    SourceLink,
    SourceLocation,
    StartIngestionCommand,
)
from astra_indexator.astravector.generated_loader import load_generated_client
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)

SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DEADLINE_SECONDS = 5.0
DEADLINE_JITTER_TOLERANCE_SECONDS = 0.25


def _start_command() -> StartIngestionCommand:
    return StartIngestionCommand(
        access_zone_id=None,
        access_zone_code="0001",
        document_id=DOCUMENT_ID,
        document_version=3,
        source_uri="seaweedfs://documents/a.pdf",
        file_name="a.pdf",
        content_hash="ab" * 32,
        idempotency_key="astraindexator:a:v3",
        total_bytes_estimate=1234,
        total_blocks_estimate=1,
        total_pages_estimate=2,
        metadata={"source": "m8.2.3"},
        ttl_days=0,
    )


def _append_command() -> AppendBlocksCommand:
    block = LogicalBlock(
        block_id="p-1",
        parent_block_id="doc-1",
        block_type="PARAGRAPH",
        text="Транспортный блок",
        order_index=0,
        source_location=SourceLocation(
            page_start=1,
            page_end=1,
            char_start=0,
            char_end=18,
            section_path="1",
            heading="Раздел",
        ),
        source_links=(
            SourceLink(
                type="PAGE",
                url="seaweedfs://documents/a.pdf#page=1",
                label="page 1",
                attributes={"page": "1"},
            ),
        ),
        metadata={"language": "ru"},
    )
    return AppendBlocksCommand(
        ingestion_session_id=SESSION_ID,
        blocks=(block,),
        batch_index=0,
        is_last_batch=True,
        batch_content_hash="cd" * 32,
    )


def _transport() -> tuple[object, grpc.Server, AstraVectorGrpcAdapter]:
    generated = load_generated_client()
    pb = generated.pb
    pb_grpc = generated.pb_grpc

    class Servicer(pb_grpc.AstraVectorIngestionFacadeServicer):
        def __init__(self) -> None:
            self.start_request = None
            self.append_request = None
            self.start_metadata: tuple[tuple[str, str], ...] = ()
            self.append_metadata: tuple[tuple[str, str], ...] = ()
            self.start_time_remaining: float | None = None
            self.append_time_remaining: float | None = None
            self.fail_append = False

        @staticmethod
        def _metadata(context: grpc.ServicerContext) -> tuple[tuple[str, str], ...]:
            return tuple((item.key, item.value) for item in context.invocation_metadata())

        def StartLogicalDocumentIngestion(self, request, context):  # type: ignore[no-untyped-def]
            self.start_request = request
            self.start_metadata = self._metadata(context)
            self.start_time_remaining = context.time_remaining()
            return pb.StartLogicalDocumentIngestionResponse(
                ingestion_session_id=str(SESSION_ID),
                status="ACTIVE",
                expires_at="2026-08-27T00:00:00Z",
            )

        def AppendLogicalDocumentBlocks(self, request, context):  # type: ignore[no-untyped-def]
            self.append_request = request
            self.append_metadata = self._metadata(context)
            self.append_time_remaining = context.time_remaining()
            if self.fail_append:
                context.abort(grpc.StatusCode.UNAVAILABLE, "append unavailable")
            return pb.AppendLogicalDocumentBlocksResponse(
                ingestion_session_id=str(SESSION_ID),
                status="ACTIVE",
                accepted_blocks=len(request.blocks),
                accepted_batch_index=request.batch_index,
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


def _assert_deadline_reached_server(time_remaining: float | None) -> None:
    assert time_remaining is not None
    assert 0 < time_remaining <= DEADLINE_SECONDS + DEADLINE_JITTER_TOLERANCE_SECONDS


def test_real_generated_grpc_start_and_append_round_trip() -> None:
    servicer, server, adapter = _transport()
    try:
        start = adapter.start(_start_command())
        assert start.ingestion_session_id == SESSION_ID
        assert start.state is IngestionSessionState.ACTIVE

        start_request = servicer.start_request
        assert start_request is not None
        assert start_request.access_zone_id == ""
        assert start_request.access_zone_code == "0001"
        assert start_request.document_id == str(DOCUMENT_ID)
        assert start_request.document_version == 3
        assert start_request.ttl_days == 0
        assert start_request.metadata["source"] == "m8.2.3"
        assert ("x-astra-service", "astra-indexator") in servicer.start_metadata
        _assert_deadline_reached_server(servicer.start_time_remaining)

        append = adapter.append(_append_command())
        assert append.ingestion_session_id == SESSION_ID
        assert append.accepted_batch_index == 0
        assert append.accepted_blocks == 1
        assert append.state is IngestionSessionState.ACTIVE

        append_request = servicer.append_request
        assert append_request is not None
        assert append_request.ingestion_session_id == str(SESSION_ID)
        assert append_request.batch_index == 0
        assert append_request.is_last_batch is True
        assert append_request.batch_content_hash == "cd" * 32
        assert len(append_request.blocks) == 1
        mapped = append_request.blocks[0]
        assert mapped.block_id == "p-1"
        assert mapped.text == "Транспортный блок"
        assert mapped.source_location.heading == "Раздел"
        assert mapped.source_links[0].url == "seaweedfs://documents/a.pdf#page=1"
        assert ("x-astra-service", "astra-indexator") in servicer.append_metadata
        _assert_deadline_reached_server(servicer.append_time_remaining)
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)


def test_real_generated_grpc_append_error_is_preserved_as_transport_error() -> None:
    servicer, server, adapter = _transport()
    try:
        adapter.start(_start_command())
        servicer.fail_append = True
        with pytest.raises(AstraVectorGrpcError) as exc_info:
            adapter.append(_append_command())
        assert exc_info.value.code == "UNAVAILABLE"
        assert "append unavailable" in exc_info.value.message
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)
