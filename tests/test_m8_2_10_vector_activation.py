from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from astra_indexator.application.coordinator import LeaseLostError, LeaseToken
from astra_indexator.application.vector_activation import VectorActivationRunner
from astra_indexator.astravector.contracts import (
    ActivateDocumentVersionResult,
    AstraVectorTransportError,
    DocumentVectorStatus,
)
from astra_indexator.astravector.policy import VectorReadinessDisposition

JOB_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
TOKEN = LeaseToken(
    job_id=JOB_ID,
    worker_id="worker-a",
    lease_generation=3,
    attempt_id=uuid4(),
)


class _Session:
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def begin(self):  # type: ignore[no-untyped-def]
        return self


class _Repository:
    def __init__(self) -> None:
        self.checkpoint_state = SimpleNamespace(ingestion_session_id=SESSION_ID)
        self.recorded: list[DocumentVectorStatus] = []

    def checkpoint(self, session: object, job_id: UUID) -> object:
        assert job_id == JOB_ID
        return self.checkpoint_state

    def record_vector_status(
        self,
        session: object,
        *,
        job_id: UUID,
        status: DocumentVectorStatus,
    ) -> None:
        assert job_id == JOB_ID
        self.recorded.append(status)


class _Fence:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def assert_owned(self, session: object, token: LeaseToken) -> None:
        assert token == TOKEN
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise LeaseLostError("lease lost")


@dataclass
class _Port:
    statuses: list[DocumentVectorStatus]
    activation_error: bool = False
    activation_calls: int = 0

    def activate_document_version(self, command):
        self.activation_calls += 1
        assert command.access_zone_id == ZONE_ID
        assert command.document_id == DOCUMENT_ID
        assert command.document_version == 1
        if self.activation_error:
            raise AstraVectorTransportError(code="UNAVAILABLE", message="ambiguous activation")
        return ActivateDocumentVersionResult(
            document_id=DOCUMENT_ID,
            document_version=1,
            raw_status="ACTIVE",
        )

    def get_document_vector_status(self, *, access_zone_id, document_id, document_version):
        assert access_zone_id == ZONE_ID
        assert document_id == DOCUMENT_ID
        assert document_version == 1
        return self.statuses.pop(0)


def _ready(*, document_status: str = "") -> DocumentVectorStatus:
    return DocumentVectorStatus(
        raw_state="OPERATION_STATE_READY_TO_ACTIVATE",
        progress_percent=100.0,
        searchable=True,
        ready_to_activate=True,
        expected_bindings=1,
        synced_bindings=1,
        pending_bindings=0,
        qdrant_collection_exists=True,
        qdrant_points_expected=1,
        qdrant_points_found=1,
        qdrant_points_missing=0,
        document_status=document_status,
    )


def _active() -> DocumentVectorStatus:
    return DocumentVectorStatus(
        raw_state="OPERATION_STATE_READY_TO_ACTIVATE",
        progress_percent=100.0,
        searchable=True,
        ready_to_activate=True,
        expected_bindings=1,
        synced_bindings=1,
        pending_bindings=0,
        qdrant_collection_exists=True,
        qdrant_points_expected=1,
        qdrant_points_found=1,
        qdrant_points_missing=0,
        document_status="ACTIVE",
    )


def _runner(port: _Port, repository: _Repository, fence: _Fence) -> VectorActivationRunner:
    return VectorActivationRunner(
        _Session,
        port,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        lease_fence=fence,  # type: ignore[arg-type]
        max_active_polls=3,
        poll_delay_seconds=0,
    )


def test_activation_calls_public_api_then_waits_for_active_status() -> None:
    repository = _Repository()
    port = _Port([_active()])

    outcome = _runner(port, repository, _Fence()).activate_until_searchable(
        token=TOKEN,
        ingestion_session_id=SESSION_ID,
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=1,
        initial_status=_ready(),
    )

    assert port.activation_calls == 1
    assert outcome.activation is not None
    assert outcome.readiness.decision.disposition is VectorReadinessDisposition.SEARCHABLE
    assert [item.document_status for item in repository.recorded] == ["", "ACTIVE"]


def test_ambiguous_activation_transport_error_reconciles_with_public_status() -> None:
    repository = _Repository()
    port = _Port([_active()], activation_error=True)

    outcome = _runner(port, repository, _Fence()).activate_until_searchable(
        token=TOKEN,
        ingestion_session_id=SESSION_ID,
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=1,
        initial_status=_ready(),
    )

    assert port.activation_calls == 1
    assert outcome.activation is None
    assert outcome.readiness.decision.disposition is VectorReadinessDisposition.SEARCHABLE


def test_lost_lease_before_activation_prevents_public_activation_call() -> None:
    port = _Port([_active()])

    with pytest.raises(LeaseLostError):
        _runner(port, _Repository(), _Fence(fail_after=0)).activate_until_searchable(
            token=TOKEN,
            ingestion_session_id=SESSION_ID,
            access_zone_id=ZONE_ID,
            document_id=DOCUMENT_ID,
            document_version=1,
            initial_status=_ready(),
        )

    assert port.activation_calls == 0
