from __future__ import annotations

from concurrent import futures
from uuid import UUID

import grpc
import pytest

from astra_indexator.astravector.contracts import DocumentVectorStatus
from astra_indexator.astravector.generated_loader import load_generated_client
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcAdapter, AstraVectorGrpcConfig
from astra_indexator.astravector.policy import (
    ActivationReadinessPolicy,
    VectorReadinessDisposition,
    VectorReadinessIntegrityError,
    evaluate_vector_readiness,
)

ACCESS_ZONE_ID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DOCUMENT_VERSION = 7


def _status(**overrides: object) -> DocumentVectorStatus:
    values: dict[str, object] = {
        "raw_state": "OPERATION_STATE_ACTIVE",
        "progress_percent": 100.0,
        "searchable": True,
        "ready_to_activate": False,
        "expected_bindings": 2,
        "synced_bindings": 2,
        "pending_bindings": 0,
        "failed_bindings": 0,
        "outbox_pending": 0,
        "outbox_retry_pending": 0,
        "outbox_failed": 0,
        "qdrant_collection_exists": True,
        "qdrant_points_expected": 2,
        "qdrant_points_found": 2,
        "qdrant_points_missing": 0,
        "qdrant_points_extra": 0,
    }
    values.update(overrides)
    return DocumentVectorStatus(**values)  # type: ignore[arg-type]


def test_active_consistent_status_is_searchable() -> None:
    decision = evaluate_vector_readiness(_status())
    assert decision.disposition is VectorReadinessDisposition.SEARCHABLE
    assert decision.completion_level == "SEARCHABLE"


def test_ready_to_activate_is_not_searchable_under_default_policy() -> None:
    status = _status(
        raw_state="OPERATION_STATE_READY_TO_ACTIVATE",
        searchable=False,
        ready_to_activate=True,
    )
    decision = evaluate_vector_readiness(status)
    assert decision.disposition is VectorReadinessDisposition.WAIT
    assert decision.completion_level == "VECTOR_READY"

    handoff = evaluate_vector_readiness(
        status, policy=ActivationReadinessPolicy.ALLOW_READY_TO_ACTIVATE
    )
    assert handoff.disposition is VectorReadinessDisposition.READY_TO_ACTIVATE


@pytest.mark.parametrize(
    "state",
    ["ACCEPTED", "INDEXING", "VECTORING", "PUBLISHING", "SYNCING"],
)
def test_transitional_states_are_finalized_but_not_searchable(state: str) -> None:
    decision = evaluate_vector_readiness(
        _status(raw_state=state, searchable=False, ready_to_activate=False)
    )
    assert decision.disposition is VectorReadinessDisposition.WAIT
    assert decision.completion_level == "FINALIZED"


@pytest.mark.parametrize("state", ["FAILED", "EXPIRED", "DELETED", "DELETE_SCHEDULED", "DELETING"])
def test_terminal_states_are_not_searchable(state: str) -> None:
    decision = evaluate_vector_readiness(
        _status(raw_state=state, searchable=False, ready_to_activate=False)
    )
    assert decision.disposition is VectorReadinessDisposition.TERMINAL


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_state": "ACTIVE", "searchable": False},
        {"raw_state": "READY_TO_ACTIVATE", "searchable": True, "ready_to_activate": True},
        {"raw_state": "READY_TO_ACTIVATE", "searchable": False, "ready_to_activate": False},
        {"expected_bindings": 2, "synced_bindings": 1},
        {"pending_bindings": 1},
        {"failed_bindings": 1},
        {"outbox_pending": 1},
        {"outbox_retry_pending": 1},
        {"outbox_failed": 1},
        {"qdrant_points_missing": 1},
        {"qdrant_points_expected": 2, "qdrant_points_found": 1},
        {"raw_state": "OPERATION_STATE_FUTURE", "searchable": False},
    ],
)
def test_contradictory_or_unknown_readiness_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(VectorReadinessIntegrityError):
        evaluate_vector_readiness(_status(**overrides))


def test_real_generated_grpc_vector_status_round_trip_preserves_identity_and_sync_evidence() -> None:
    generated = load_generated_client()
    pb = generated.pb
    pb_grpc = generated.pb_grpc

    class Servicer(pb_grpc.AstraVectorIngestionFacadeServicer):
        def __init__(self) -> None:
            self.request = None

        def GetDocumentVectorStatus(self, request, context):  # type: ignore[no-untyped-def]
            self.request = request
            return pb.GetDocumentVectorStatusResponse(
                document=pb.DocumentRef(
                    access_zone_id=str(ACCESS_ZONE_ID),
                    document_id=str(DOCUMENT_ID),
                    document_version=DOCUMENT_VERSION,
                ),
                status=pb.DocumentVectorStatus(
                    state=pb.OPERATION_STATE_ACTIVE,
                    progress_percent=100.0,
                    searchable=True,
                    ready_to_activate=False,
                    sync=pb.GetVectorSyncStatusResponse(
                        expected_bindings=3,
                        synced_bindings=3,
                        qdrant_collection_exists=True,
                        qdrant_points_expected=3,
                        qdrant_points_found=3,
                    ),
                ),
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
        AstraVectorGrpcConfig(target=f"127.0.0.1:{port}"),
        generated=generated,
        channel=channel,
    )
    try:
        status = adapter.get_document_vector_status(
            access_zone_id=ACCESS_ZONE_ID,
            document_id=DOCUMENT_ID,
            document_version=DOCUMENT_VERSION,
        )
        decision = evaluate_vector_readiness(status)
        assert decision.disposition is VectorReadinessDisposition.SEARCHABLE
        assert status.expected_bindings == status.synced_bindings == 3
        assert status.qdrant_collection_exists is True
        assert status.qdrant_points_expected == status.qdrant_points_found == 3
        assert servicer.request.document.access_zone_id == str(ACCESS_ZONE_ID)
        assert servicer.request.document.document_id == str(DOCUMENT_ID)
        assert servicer.request.document.document_version == DOCUMENT_VERSION
        assert servicer.request.include_qdrant is True
    finally:
        adapter.close()
        server.stop(grace=0).wait(timeout=5)
