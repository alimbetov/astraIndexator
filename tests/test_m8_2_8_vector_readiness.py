from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.application.vector_readiness import (
    VectorReadinessPending,
    VectorReadinessRunner,
    VectorReadinessTerminalError,
)
from astra_indexator.astravector.contracts import DocumentVectorStatus
from astra_indexator.astravector.policy import (
    ActivationReadinessPolicy,
    VectorReadinessDisposition,
    VectorReadinessIntegrityError,
    evaluate_vector_readiness,
)

JOB_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ACCESS_ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
DOCUMENT_VERSION = 7


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
    ) -> object:
        assert job_id == JOB_ID
        self.recorded.append(status)
        return self.checkpoint_state


class _Port:
    def __init__(self, statuses: list[DocumentVectorStatus]) -> None:
        self.statuses = statuses
        self.calls = 0

    def get_document_vector_status(
        self,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVectorStatus:
        assert access_zone_id == ACCESS_ZONE_ID
        assert document_id == DOCUMENT_ID
        assert document_version == DOCUMENT_VERSION
        self.calls += 1
        return self.statuses.pop(0)


def _status(
    state: str,
    *,
    searchable: bool = False,
    ready: bool = False,
    expected: int = 10,
    synced: int = 0,
    pending: int = 10,
    failed: int = 0,
    outbox_pending: int = 0,
    outbox_retry_pending: int = 0,
    outbox_failed: int = 0,
    qdrant_expected: int = 10,
    qdrant_found: int = 0,
    qdrant_missing: int = 10,
    message: str = "",
) -> DocumentVectorStatus:
    return DocumentVectorStatus(
        raw_state=f"OPERATION_STATE_{state}",
        progress_percent=100.0 if state in {"READY_TO_ACTIVATE", "ACTIVE"} else 50.0,
        searchable=searchable,
        ready_to_activate=ready,
        message=message,
        expected_bindings=expected,
        synced_bindings=synced,
        pending_bindings=pending,
        failed_bindings=failed,
        outbox_pending=outbox_pending,
        outbox_retry_pending=outbox_retry_pending,
        outbox_failed=outbox_failed,
        qdrant_collection_exists=True,
        qdrant_points_expected=qdrant_expected,
        qdrant_points_found=qdrant_found,
        qdrant_points_missing=qdrant_missing,
    )


def _ready(state: str, *, searchable: bool) -> DocumentVectorStatus:
    return _status(
        state,
        searchable=searchable,
        ready=True,
        synced=10,
        pending=0,
        qdrant_found=10,
        qdrant_missing=0,
    )


def _runner(
    port: _Port,
    repository: _Repository,
    *,
    policy: ActivationReadinessPolicy = ActivationReadinessPolicy.REQUIRE_SEARCHABLE,
    max_polls: int = 5,
) -> VectorReadinessRunner:
    return VectorReadinessRunner(
        _Session,
        port,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        policy=policy,
        max_polls=max_polls,
        poll_delay_seconds=0,
    )


def _run(runner: VectorReadinessRunner, initial_status: DocumentVectorStatus | None = None):  # type: ignore[no-untyped-def]
    return runner.wait_until_ready(
        job_id=JOB_ID,
        ingestion_session_id=SESSION_ID,
        access_zone_id=ACCESS_ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=DOCUMENT_VERSION,
        initial_status=initial_status,
    )


def test_default_policy_waits_through_ready_to_activate_until_active_searchable() -> None:
    repository = _Repository()
    port = _Port([_ready("READY_TO_ACTIVATE", searchable=False), _ready("ACTIVE", searchable=True)])

    outcome = _run(_runner(port, repository))

    assert outcome.decision.disposition is VectorReadinessDisposition.SEARCHABLE
    assert outcome.polls == 2
    assert port.calls == 2
    assert [item.raw_state for item in repository.recorded] == [
        "OPERATION_STATE_READY_TO_ACTIVATE",
        "OPERATION_STATE_ACTIVE",
    ]


def test_manual_handoff_policy_stops_at_ready_to_activate() -> None:
    repository = _Repository()
    status = _ready("READY_TO_ACTIVATE", searchable=False)
    port = _Port([])

    outcome = _run(
        _runner(
            port,
            repository,
            policy=ActivationReadinessPolicy.ALLOW_READY_TO_ACTIVATE,
        ),
        initial_status=status,
    )

    assert outcome.decision.disposition is VectorReadinessDisposition.READY_TO_ACTIVATE
    assert outcome.decision.completion_level == "VECTOR_READY"
    assert port.calls == 0


def test_active_searchable_is_terminal_success() -> None:
    decision = evaluate_vector_readiness(_ready("ACTIVE", searchable=True))
    assert decision.disposition is VectorReadinessDisposition.SEARCHABLE
    assert decision.completion_level == "SEARCHABLE"


def test_transitional_state_remains_pending() -> None:
    repository = _Repository()
    port = _Port([_status("SYNCING"), _status("SYNCING")])

    with pytest.raises(VectorReadinessPending, match="did not reach"):
        _run(_runner(port, repository, max_polls=2))

    assert port.calls == 2
    assert len(repository.recorded) == 2


def test_failed_vector_state_is_terminal_error() -> None:
    repository = _Repository()
    port = _Port([_status("FAILED", expected=0, pending=0, qdrant_expected=0, qdrant_missing=0, message="embedding failed")])

    with pytest.raises(VectorReadinessTerminalError, match="embedding failed"):
        _run(_runner(port, repository))


def test_ready_to_activate_rejects_incomplete_sync_evidence() -> None:
    inconsistent = _status(
        "READY_TO_ACTIVATE",
        ready=True,
        synced=9,
        pending=1,
        qdrant_found=9,
        qdrant_missing=1,
    )

    with pytest.raises(VectorReadinessIntegrityError, match="failed bindings|synced_bindings|pending"):
        evaluate_vector_readiness(
            inconsistent,
            policy=ActivationReadinessPolicy.ALLOW_READY_TO_ACTIVATE,
        )


def test_active_without_searchable_is_integrity_error() -> None:
    with pytest.raises(VectorReadinessIntegrityError, match="must be searchable"):
        evaluate_vector_readiness(_ready("ACTIVE", searchable=False))


def test_transitional_searchable_is_integrity_error() -> None:
    with pytest.raises(VectorReadinessIntegrityError, match="transitional"):
        evaluate_vector_readiness(
            _status("SYNCING", searchable=True, expected=0, pending=0, qdrant_expected=0, qdrant_missing=0)
        )
