from __future__ import annotations

from uuid import uuid4

from astra_indexator.astravector import (
    DocumentVectorStatus,
    GrpcFailure,
    IngestionSessionState,
    IngestionStatus,
    RetryDecision,
    classify_grpc_failure,
    map_session_state,
    should_retry_finalize,
    vector_delivery_complete,
)


def _status(raw: str) -> IngestionStatus:
    return IngestionStatus(
        ingestion_session_id=uuid4(),
        raw_status=raw,
        state=map_session_state(raw),
        received_batches=1,
        received_blocks=2,
        received_bytes=3,
        expires_at="2026-08-26T00:00:00Z",
    )


def test_unknown_session_state_is_forward_compatible() -> None:
    assert map_session_state("paused") == IngestionSessionState.UNKNOWN
    assert map_session_state(" completed ") == IngestionSessionState.COMPLETED


def test_finalize_retry_only_from_active_after_reconciliation() -> None:
    assert should_retry_finalize(_status("ACTIVE")) is True
    assert should_retry_finalize(_status("FINALIZING")) is False
    assert should_retry_finalize(_status("COMPLETED")) is False


def test_searchability_is_authoritative_completion_signal() -> None:
    not_searchable = DocumentVectorStatus(
        raw_state="READY_TO_ACTIVATE",
        progress_percent=100.0,
        searchable=False,
        ready_to_activate=True,
    )
    searchable = DocumentVectorStatus(
        raw_state="ACTIVE",
        progress_percent=100.0,
        searchable=True,
        ready_to_activate=True,
    )
    assert vector_delivery_complete(not_searchable) is False
    assert vector_delivery_complete(searchable) is True


def test_retry_policy_matches_session_contract() -> None:
    assert classify_grpc_failure(GrpcFailure("UNAVAILABLE")) == RetryDecision.BACKOFF_AND_RETRY
    assert classify_grpc_failure(GrpcFailure("DEADLINE_EXCEEDED")) == RetryDecision.RECONCILE_STATUS
    assert classify_grpc_failure(GrpcFailure("ABORTED")) == RetryDecision.RECONCILE_STATUS
    assert (
        classify_grpc_failure(GrpcFailure("FAILED_PRECONDITION", "BATCH_HASH_MISMATCH"))
        == RetryDecision.PERMANENT_FAILURE
    )
    assert (
        classify_grpc_failure(GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_FINALIZING"))
        == RetryDecision.RECONCILE_STATUS
    )
    assert (
        classify_grpc_failure(GrpcFailure("INVALID_ARGUMENT"))
        == RetryDecision.PERMANENT_FAILURE
    )
