from __future__ import annotations

from uuid import UUID

from astra_indexator.application.delete_reconciliation import (
    DeleteReconciliationRunner,
    ReconciliationClassification,
)
from astra_indexator.astravector.contracts import (
    AstraVectorTransportError,
    DeleteDocumentCommand,
    DeleteDocumentResult,
    DocumentVectorStatus,
)

ZONE_ID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


def _status(state: str, *, searchable: bool = False) -> DocumentVectorStatus:
    return DocumentVectorStatus(
        raw_state=state,
        progress_percent=100.0,
        searchable=searchable,
        ready_to_activate=False,
    )


class _Port:
    def __init__(self, deletes: list[object], statuses: list[DocumentVectorStatus]) -> None:
        self.deletes = deletes
        self.statuses = statuses
        self.commands: list[DeleteDocumentCommand] = []
        self.status_calls = 0

    def delete_document(self, command: DeleteDocumentCommand) -> DeleteDocumentResult:
        self.commands.append(command)
        step = self.deletes.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, DeleteDocumentResult)
        return step

    def get_document_vector_status(
        self,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVectorStatus:
        assert access_zone_id == ZONE_ID
        assert document_id == DOCUMENT_ID
        assert document_version == 2
        self.status_calls += 1
        return self.statuses.pop(0)


def _command() -> DeleteDocumentCommand:
    return DeleteDocumentCommand(
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=2,
        reason="retention cleanup",
        idempotency_key="delete-op-1",
    )


def _result(state: str) -> DeleteDocumentResult:
    return DeleteDocumentResult(
        access_zone_id=ZONE_ID,
        document_id=DOCUMENT_ID,
        document_version=2,
        raw_operation_state=state,
    )


def test_timeout_then_deleted_is_confirmed_without_second_delete() -> None:
    port = _Port(
        [AstraVectorTransportError(code="DEADLINE_EXCEEDED", message="lost ack")],
        [_status("DELETED")],
    )
    outcome = DeleteReconciliationRunner(port).delete(_command())  # type: ignore[arg-type]
    assert outcome.classification is ReconciliationClassification.CONFIRMED_SUCCEEDED
    assert len(port.commands) == 1
    assert port.status_calls == 1


def test_timeout_then_active_retries_exact_same_delete() -> None:
    port = _Port(
        [
            AstraVectorTransportError(code="UNAVAILABLE", message="lost ack"),
            _result("DELETE_SCHEDULED"),
        ],
        [_status("ACTIVE", searchable=True), _status("DELETED")],
    )
    outcome = DeleteReconciliationRunner(port).delete(_command())  # type: ignore[arg-type]
    assert outcome.classification is ReconciliationClassification.CONFIRMED_SUCCEEDED
    assert len(port.commands) == 2
    assert port.commands[0] == port.commands[1]


def test_delete_scheduled_polls_without_duplicate_mutation() -> None:
    port = _Port(
        [_result("DELETE_SCHEDULED")],
        [
            _status("DELETE_SCHEDULED"),
            _status("DELETING"),
            _status("DELETED"),
        ],
    )
    outcome = DeleteReconciliationRunner(port, max_status_polls=5).delete(  # type: ignore[arg-type]
        _command()
    )
    assert outcome.classification is ReconciliationClassification.CONFIRMED_SUCCEEDED
    assert len(port.commands) == 1
    assert port.status_calls == 3


def test_operation_state_prefix_is_normalized() -> None:
    port = _Port(
        [_result("OPERATION_STATE_DELETE_SCHEDULED")],
        [_status("OPERATION_STATE_DELETED")],
    )
    outcome = DeleteReconciliationRunner(port).delete(_command())  # type: ignore[arg-type]
    assert outcome.classification is ReconciliationClassification.CONFIRMED_SUCCEEDED
