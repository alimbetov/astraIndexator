from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.astravector.generated_loader import GeneratedAstraVectorClient
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)

ACCESS_ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
OTHER_DOCUMENT_ID = UUID("99999999-9999-9999-9999-999999999999")
DOCUMENT_VERSION = 7


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _OperationState:
    @staticmethod
    def Name(value: int) -> str:
        return {6: "OPERATION_STATE_READY_TO_ACTIVATE", 7: "OPERATION_STATE_ACTIVE"}[value]


class _Channel:
    def close(self) -> None:
        return None


class _Stub:
    def __init__(self, *, response_document_id: UUID = DOCUMENT_ID) -> None:
        self.response_document_id = response_document_id

    def GetDocumentVectorStatus(self, request: object, **kwargs: object) -> object:
        sync = SimpleNamespace(
            expected_bindings=10,
            synced_bindings=10,
            pending_bindings=0,
            failed_bindings=0,
            outbox_pending=0,
            outbox_retry_pending=0,
            outbox_failed=0,
            qdrant_collection_exists=True,
            qdrant_points_expected=10,
            qdrant_points_found=10,
            qdrant_points_missing=0,
            qdrant_points_extra=0,
        )
        return SimpleNamespace(
            document=SimpleNamespace(
                access_zone_id=str(ACCESS_ZONE_ID),
                document_id=str(self.response_document_id),
                document_version=DOCUMENT_VERSION,
            ),
            status=SimpleNamespace(
                state=6,
                progress_percent=100.0,
                searchable=False,
                ready_to_activate=True,
                message="ready",
                sync=sync,
            ),
        )


def _adapter(stub: _Stub) -> AstraVectorGrpcAdapter:
    pb = SimpleNamespace(
        RequestContext=_Message,
        DocumentRef=_Message,
        GetDocumentVectorStatusRequest=_Message,
        OperationState=_OperationState,
    )
    generated = GeneratedAstraVectorClient(pb=pb, pb_grpc=SimpleNamespace())  # type: ignore[arg-type]
    return AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(deadline_seconds=5.0),
        generated=generated,
        channel=_Channel(),  # type: ignore[arg-type]
        stub=stub,
    )


def test_vector_status_maps_sync_evidence_from_public_facade() -> None:
    status = _adapter(_Stub()).get_document_vector_status(
        access_zone_id=ACCESS_ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=DOCUMENT_VERSION,
    )

    assert status.raw_state == "OPERATION_STATE_READY_TO_ACTIVATE"
    assert status.ready_to_activate is True
    assert status.searchable is False
    assert status.expected_bindings == 10
    assert status.synced_bindings == 10
    assert status.qdrant_collection_exists is True
    assert status.qdrant_points_found == 10
    assert status.qdrant_points_missing == 0


def test_vector_status_rejects_different_document_identity() -> None:
    adapter = _adapter(_Stub(response_document_id=OTHER_DOCUMENT_ID))

    with pytest.raises(AstraVectorGrpcError, match="different document identity"):
        adapter.get_document_vector_status(
            access_zone_id=ACCESS_ZONE_ID,
            document_id=DOCUMENT_ID,
            document_version=DOCUMENT_VERSION,
        )
