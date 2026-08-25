from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.application.abort_reconciliation import (
    AbortConflictError,
    AbortReconciliationRunner,
    AbortResolution,
)
from astra_indexator.astravector.contracts import IngestionSessionState, IngestionStatus
from astra_indexator.astravector.grpc_adapter import AstraVectorGrpcError

JOB_ID = UUID("11111111-1111-1111-1111-111111111111")
SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")


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
        self.recorded: list[str] = []

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
        self.recorded.append(session_status_raw)
        return self.checkpoint_state


class _Port:
    def __init__(self, *, abort_steps: list[object], statuses: list[IngestionStatus]) -> None:
        self.abort_steps = abort_steps
        self.statuses = statuses
        self.abort_commands: list[object] = []
        self.status_calls = 0

    def abort(self, command: object) -> IngestionStatus:
        self.abort_commands.append(command)
        step = self.abort_steps.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, IngestionStatus)
        return step

    def get_ingestion_status(self, ingestion_session_id: UUID) -> IngestionStatus:
        assert ingestion_session_id == SESSION_ID
        self.status_calls += 1
        return self.statuses.pop(0)


def _status(state: IngestionSessionState) -> IngestionStatus:
    return IngestionStatus(
        ingestion_session_id=SESSION_ID,
        raw_status=state.value,
        state=state,
        received_batches=3,
        received_blocks=50,
        received_bytes=1000,
        expires_at="",
    )


def _runner(port: _Port, repository: _Repository) -> AbortReconciliationRunner:
    return AbortReconciliationRunner(
        _Session,
        port,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        max_abort_attempts=3,
        max_status_polls=5,
        poll_delay_seconds=0,
    )


def _execute(runner: AbortReconciliationRunner):  # type: ignore[no-untyped-def]
    return runner.abort(
        job_id=JOB_ID,
        ingestion_session_id=SESSION_ID,
        reason="worker recovery",
    )


def test_direct_abort_ack_is_terminal_success() -> None:
    port = _Port(abort_steps=[_status(IngestionSessionState.ABORTED)], statuses=[])
    repository = _Repository()
    outcome = _execute(_runner(port, repository))
    assert outcome.resolution is AbortResolution.DIRECT_ACK
    assert outcome.status.state is IngestionSessionState.ABORTED
    assert len(port.abort_commands) == 1
    assert repository.recorded == ["ABORTED"]


def test_timeout_then_aborted_reconciles_without_second_abort() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(abort_steps=[timeout], statuses=[_status(IngestionSessionState.ABORTED)])
    repository = _Repository()
    outcome = _execute(_runner(port, repository))
    assert outcome.resolution is AbortResolution.RECONCILED_ABORTED
    assert len(port.abort_commands) == 1
    assert port.status_calls == 1


def test_timeout_then_active_retries_exact_same_abort_command() -> None:
    timeout = AstraVectorGrpcError(code="UNAVAILABLE", message="lost after send")
    port = _Port(
        abort_steps=[timeout, _status(IngestionSessionState.ABORTED)],
        statuses=[_status(IngestionSessionState.ACTIVE)],
    )
    repository = _Repository()
    outcome = _execute(_runner(port, repository))
    assert outcome.resolution is AbortResolution.DIRECT_ACK
    assert len(port.abort_commands) == 2
    assert port.abort_commands[0] == port.abort_commands[1]


def test_timeout_then_finalizing_polls_and_never_replays_abort() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(
        abort_steps=[timeout],
        statuses=[
            _status(IngestionSessionState.FINALIZING),
            _status(IngestionSessionState.FINALIZING),
            _status(IngestionSessionState.ABORTED),
        ],
    )
    repository = _Repository()
    outcome = _execute(_runner(port, repository))
    assert outcome.resolution is AbortResolution.RECONCILED_ABORTED
    assert len(port.abort_commands) == 1
    assert port.status_calls == 3


def test_timeout_then_finalizing_to_completed_is_conflict_without_second_abort() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(
        abort_steps=[timeout],
        statuses=[
            _status(IngestionSessionState.FINALIZING),
            _status(IngestionSessionState.COMPLETED),
        ],
    )
    repository = _Repository()
    with pytest.raises(AbortConflictError, match="completed successfully"):
        _execute(_runner(port, repository))
    assert len(port.abort_commands) == 1
    assert port.status_calls == 2


def test_timeout_then_completed_is_conflict_not_new_version() -> None:
    timeout = AstraVectorGrpcError(code="DEADLINE_EXCEEDED", message="deadline")
    port = _Port(abort_steps=[timeout], statuses=[_status(IngestionSessionState.COMPLETED)])
    repository = _Repository()
    with pytest.raises(AbortConflictError, match="completed successfully"):
        _execute(_runner(port, repository))
    assert len(port.abort_commands) == 1


@pytest.mark.parametrize("state", [IngestionSessionState.FAILED, IngestionSessionState.EXPIRED])
def test_timeout_then_existing_terminal_state_is_recovery_success(
    state: IngestionSessionState,
) -> None:
    timeout = AstraVectorGrpcError(code="UNKNOWN", message="ambiguous")
    port = _Port(abort_steps=[timeout], statuses=[_status(state)])
    repository = _Repository()
    outcome = _execute(_runner(port, repository))
    assert outcome.resolution is AbortResolution.ALREADY_TERMINAL
    assert outcome.status.state is state
    assert len(port.abort_commands) == 1
