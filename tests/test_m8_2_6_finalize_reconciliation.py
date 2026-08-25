from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.application.finalize_reconciliation import (
    FinalizeReconciliationRunner,
    FinalizeResolution,
    FinalizeTerminalError,
)
from astra_indexator.astravector.contracts import (
    DocumentVectorStatus,
    FinalizeIngestionResult,
    IngestionSessionState,
    IngestionStatus,
)
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcError

JOB_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
ZONE_ID = UUID("33333333-3333-3333-3333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")


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
        self.recorded: list[IngestionStatus] = []

    def checkpoint(self, session: object, job_id: UUID) -> object:
        assert job_id == JOB_ID
        return self.checkpoint_state

    def record_session_status(
        self,
        session: object,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        session_status_raw: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> object:
        assert job_id == JOB_ID
        assert ingestion_session_id == SESSION_ID
        self.recorded.append(
            IngestionStatus(
                ingestion_session_id=ingestion_session_id,
                raw_status=session_status_raw,
                state=IngestionSessionState(session_status_raw),
                received_batches=1,
                received_blocks=2,
                received_bytes=3,
                expires_at="",
                error_code=error_code or "",
                error_message=error_message or "",
            )
        )
        return self.checkpoint_state


class _Port:
    def __init__(self, *, finalize_steps: list[object], statuses: list[IngestionStatus]) -> None:
        self.finalize_steps = finalize_steps
        self.statuses = statuses
        self.finalize_commands: list[object] = []
        self.status_calls = 0
        self.vector_calls = 0

    def finalize(self, command: object) -> FinalizeIngestionResult:
        self.finalize_commands.append(command)
        step = self.finalize_steps.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, FinalizeIngestionResult)
        return step

    def get_ingestion_status(self, ingestion_session_id: UUID) -> IngestionStatus:
        assert ingestion_session_id == SESSION_ID
        self.status_calls += 1
        return self.statuses.pop(0)

    def get_document_vector_status(
        self,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVectorStatus:
        assert access_zone_id == ZONE_ID
        assert document_id == DOCUMENT_ID
        assert document_version == 7
        self.vector_calls += 1
        return DocumentVectorStatus(
            raw_state="OPERATION_STATE_VECTORING",
            progress_percent=25.0,
            searchable=False,
            ready_to_activate=False,
        )


def _status(state: IngestionSessionState, *, code: str = "", message: str = "") -> IngestionStatus:
    return IngestionStatus(
        ingestion_session_id=SESSION_ID,
        raw_status=state.value,
        state=state,
        received_batches=1,
        received_blocks=2,
        received_bytes=3,
        expires_at="",
        error_code=code,
        error_message=message,
    )


def _finalized() -> FinalizeIngestionResult:
    return FinalizeIngestionResult(
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=7,
        raw_operation_state="OPERATION_STATE_VECTORING",
        operation_id="op-1",
    )


def _runner(port: _Port, repository: _Repository) -> FinalizeReconciliationRunner:
    return FinalizeReconciliationRunner(
        _Session,
        port,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        max_finalize_attempts=3,
        max_status_polls=5,
        poll_delay_seconds=0,
    )


def _execute(runner: FinalizeReconciliationRunner):  # type: ignore[no-untyped-def]
    return runner.finalize(
        job_id=JOB_ID,
        ingestion_session_id=SESSION_ID,
        final_content_hash="ab" * 32,
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=7,
    )


def test_timeout_then_active_retries_exact_same_finalize_command() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(
        finalize_steps=[timeout, _finalized()], statuses=[_status(IngestionSessionState.ACTIVE)]
    )
    repository = _Repository()

    outcome = _execute(_runner(port, repository))

    assert outcome.resolution is FinalizeResolution.DIRECT_ACK
    assert len(port.finalize_commands) == 2
    assert port.finalize_commands[0] == port.finalize_commands[1]
    assert port.status_calls == 1
    assert port.vector_calls == 1
    assert repository.recorded[-1].state is IngestionSessionState.ACTIVE


def test_timeout_then_finalizing_polls_without_second_finalize() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(
        finalize_steps=[timeout],
        statuses=[
            _status(IngestionSessionState.FINALIZING),
            _status(IngestionSessionState.FINALIZING),
            _status(IngestionSessionState.COMPLETED),
        ],
    )
    repository = _Repository()

    outcome = _execute(_runner(port, repository))

    assert outcome.resolution is FinalizeResolution.RECONCILED_COMPLETED
    assert len(port.finalize_commands) == 1
    assert port.status_calls == 3
    assert port.vector_calls == 1
    assert repository.recorded[-1].state is IngestionSessionState.COMPLETED


def test_timeout_then_completed_goes_directly_to_vector_status() -> None:
    timeout = AstraVectorGrpcError(code="UNAVAILABLE", message="connection lost after send")
    port = _Port(
        finalize_steps=[timeout],
        statuses=[_status(IngestionSessionState.COMPLETED)],
    )
    repository = _Repository()

    outcome = _execute(_runner(port, repository))

    assert outcome.resolution is FinalizeResolution.RECONCILED_COMPLETED
    assert len(port.finalize_commands) == 1
    assert port.vector_calls == 1


def test_timeout_then_failed_is_terminal_and_never_creates_new_version() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(
        finalize_steps=[timeout],
        statuses=[
            _status(
                IngestionSessionState.FAILED,
                code="FINAL_CONTENT_HASH_MISMATCH",
                message="content hash mismatch",
            )
        ],
    )
    repository = _Repository()

    with pytest.raises(FinalizeTerminalError, match="FINAL_CONTENT_HASH_MISMATCH"):
        _execute(_runner(port, repository))

    assert len(port.finalize_commands) == 1
    assert port.vector_calls == 0
