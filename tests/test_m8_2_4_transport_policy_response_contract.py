from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.astravector.contracts import AppendBlocksCommand, LogicalBlock
from astra_indexator.astravector.generated_loader import load_generated_client
from astra_indexator.astravector.grpc_adapter import (
    AstraVectorGrpcAdapter,
    AstraVectorGrpcConfig,
    AstraVectorGrpcError,
)
from astra_indexator.astravector.policy import (
    GrpcFailure,
    RetryDecision,
    classify_grpc_failure,
)

SESSION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            GrpcFailure("UNAVAILABLE", "postgres ingestion append lookup"),
            RetryDecision.BACKOFF_AND_RETRY,
        ),
        (GrpcFailure("DEADLINE_EXCEEDED", "deadline"), RetryDecision.RECONCILE_STATUS),
        (GrpcFailure("ABORTED", "INGESTION_SESSION_FINALIZING"), RetryDecision.RECONCILE_STATUS),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "max_concurrent_ingestion_sessions exceeded"),
            RetryDecision.BACKOFF_AND_RETRY,
        ),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "max_sessions_per_access_zone exceeded"),
            RetryDecision.BACKOFF_AND_RETRY,
        ),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "max_sessions_per_document exceeded"),
            RetryDecision.BACKOFF_AND_RETRY,
        ),
        (
            GrpcFailure(
                "RESOURCE_EXHAUSTED", "batch exceeds chunked_ingestion_max_blocks_per_batch"
            ),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "batch exceeds chunked_ingestion_max_batch_bytes"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "max_blocks_per_document exceeded"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("RESOURCE_EXHAUSTED", "future unqualified limit"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "BATCH_HASH_MISMATCH"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "FINAL_CONTENT_HASH_MISMATCH"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_FINALIZING"),
            RetryDecision.RECONCILE_STATUS,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_COMPLETED"),
            RetryDecision.RECONCILE_STATUS,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_ABORTED"),
            RetryDecision.RECONCILE_STATUS,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_FAILED:E42:broken"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "INGESTION_SESSION_EXPIRED"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (
            GrpcFailure("FAILED_PRECONDITION", "future unqualified precondition"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (GrpcFailure("NOT_FOUND", "INGESTION_SESSION_NOT_FOUND"), RetryDecision.PERMANENT_FAILURE),
        (
            GrpcFailure("DATA_LOSS", "INGESTION_COMPLETED_RESULT_MISSING"),
            RetryDecision.PERMANENT_FAILURE,
        ),
        (GrpcFailure("INVALID_ARGUMENT", "bad request"), RetryDecision.PERMANENT_FAILURE),
        (GrpcFailure("OUT_OF_RANGE", "bad range"), RetryDecision.PERMANENT_FAILURE),
        (GrpcFailure("PERMISSION_DENIED", "denied"), RetryDecision.PERMANENT_FAILURE),
        (GrpcFailure("UNAUTHENTICATED", "missing auth"), RetryDecision.PERMANENT_FAILURE),
        (GrpcFailure("INTERNAL", "future server defect"), RetryDecision.PERMANENT_FAILURE),
        (GrpcFailure("SOME_FUTURE_CODE", "unknown"), RetryDecision.PERMANENT_FAILURE),
    ],
)
def test_failure_policy_is_explicit_and_fail_closed(
    failure: GrpcFailure, expected: RetryDecision
) -> None:
    assert classify_grpc_failure(failure) is expected


def test_failure_policy_normalizes_code_and_marker_case() -> None:
    assert (
        classify_grpc_failure(
            GrpcFailure(" resource_exhausted ", " Max_Concurrent_Ingestion_Sessions Exceeded ")
        )
        is RetryDecision.BACKOFF_AND_RETRY
    )
    assert (
        classify_grpc_failure(GrpcFailure(" failed_precondition ", " ingestion_session_completed "))
        is RetryDecision.RECONCILE_STATUS
    )


class _Channel:
    def close(self) -> None:
        pass


class _AppendStub:
    def __init__(self, response: object) -> None:
        self.response = response

    def AppendLogicalDocumentBlocks(self, request, *, timeout, metadata):  # type: ignore[no-untyped-def]
        return self.response


def _append_command() -> AppendBlocksCommand:
    return AppendBlocksCommand(
        ingestion_session_id=SESSION_ID,
        blocks=(
            LogicalBlock(
                block_id="p-1",
                parent_block_id="doc-1",
                block_type="PARAGRAPH",
                text="response contract",
                order_index=0,
            ),
        ),
        batch_index=7,
        is_last_batch=False,
        batch_content_hash="cd" * 32,
    )


def _adapter(response: object) -> AstraVectorGrpcAdapter:
    return AstraVectorGrpcAdapter(
        AstraVectorGrpcConfig(),
        generated=load_generated_client(),
        channel=_Channel(),  # type: ignore[arg-type]
        stub=_AppendStub(response),
    )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            SimpleNamespace(
                ingestion_session_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                accepted_blocks=1,
                accepted_batch_index=7,
                status="ACTIVE",
                warnings=(),
            ),
            "different ingestion_session_id",
        ),
        (
            SimpleNamespace(
                ingestion_session_id=str(SESSION_ID),
                accepted_blocks=1,
                accepted_batch_index=8,
                status="ACTIVE",
                warnings=(),
            ),
            "unexpected batch index",
        ),
        (
            SimpleNamespace(
                ingestion_session_id=str(SESSION_ID),
                accepted_blocks=2,
                accepted_batch_index=7,
                status="ACTIVE",
                warnings=(),
            ),
            "unexpected block count",
        ),
        (
            SimpleNamespace(
                ingestion_session_id="not-a-uuid",
                accepted_blocks=1,
                accepted_batch_index=7,
                status="ACTIVE",
                warnings=(),
            ),
            "malformed acknowledgement",
        ),
    ],
)
def test_append_acknowledgement_mismatch_is_invalid_response(
    response: object, message: str
) -> None:
    with pytest.raises(AstraVectorGrpcError) as exc_info:
        _adapter(response).append(_append_command())
    assert exc_info.value.code == "INVALID_RESPONSE"
    assert message in exc_info.value.message
