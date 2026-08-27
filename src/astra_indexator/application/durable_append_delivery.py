from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import LeaseLostError, LeaseToken
from astra_indexator.astravector.batching import PlannedDeliveryBatch
from astra_indexator.astravector.contracts import AppendBlocksResult, AstraVectorIngestionPort
from astra_indexator.persistence.delivery import (
    BatchReplayDisposition,
    DeliveryBatchRepository,
)
from astra_indexator.persistence.models import IndexationJob


class LeaseRpcWindowTooShort(RuntimeError):
    """The current lease is valid but too short to safely start a mutating RPC."""

    def __init__(self, *, remaining_seconds: float, required_seconds: float) -> None:
        super().__init__(
            "remaining lease window is too short for mutating RPC: "
            f"remaining={remaining_seconds:.3f}s required={required_seconds:.3f}s"
        )
        self.remaining_seconds = remaining_seconds
        self.required_seconds = required_seconds


@dataclass(frozen=True, slots=True)
class DurableBatchDeliveryOutcome:
    batch_index: int
    batch_content_hash: str
    disposition: BatchReplayDisposition
    remote_result: AppendBlocksResult | None


class DurableAppendLeaseFence:
    """PostgreSQL-authoritative M8.3 lease fence for delivery mutations.

    The matching ``indexation_job`` row is locked for the surrounding transaction.
    Therefore the lease cannot be concurrently reclaimed while the caller commits a
    delivery checkpoint mutation in that transaction.
    """

    def assert_owned(self, session: Session, token: LeaseToken) -> None:
        owned = session.execute(
            select(IndexationJob.id)
            .where(
                IndexationJob.id == token.job_id,
                IndexationJob.worker_id == token.worker_id,
                IndexationJob.lease_generation == token.lease_generation,
                IndexationJob.status == "PROCESSING",
                IndexationJob.lease_until.is_not(None),
                IndexationJob.lease_until >= func.now(),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if owned is None:
            raise LeaseLostError(
                "delivery lease token is stale, expired, or no longer owns the PROCESSING job"
            )

    def assert_safe_rpc_window(
        self,
        session: Session,
        token: LeaseToken,
        *,
        rpc_deadline_seconds: float,
        safety_margin_seconds: float,
    ) -> float:
        if rpc_deadline_seconds <= 0:
            raise ValueError("rpc_deadline_seconds must be positive")
        if safety_margin_seconds < 0:
            raise ValueError("safety_margin_seconds must not be negative")

        row = session.execute(
            select(IndexationJob.lease_until, func.now())
            .where(
                IndexationJob.id == token.job_id,
                IndexationJob.worker_id == token.worker_id,
                IndexationJob.lease_generation == token.lease_generation,
                IndexationJob.status == "PROCESSING",
                IndexationJob.lease_until.is_not(None),
            )
            .with_for_update()
        ).one_or_none()
        if row is None or row[0] is None:
            raise LeaseLostError(
                "delivery lease token is stale or no longer owns the PROCESSING job"
            )

        lease_until, database_now = row
        remaining_seconds = (lease_until - database_now).total_seconds()
        if remaining_seconds < 0:
            raise LeaseLostError("delivery lease expired before mutating RPC")

        required_seconds = rpc_deadline_seconds + safety_margin_seconds
        if remaining_seconds < required_seconds:
            raise LeaseRpcWindowTooShort(
                remaining_seconds=remaining_seconds,
                required_seconds=required_seconds,
            )
        return remaining_seconds


class DurableAppendDeliveryRunner:
    """M8.3 crash-safe Append execution with lease-fenced durable boundaries.

    A PREPARED row is durably committed before the network call. The current lease is
    checked again immediately before that mutating call. The Append RPC starts only when
    its configured deadline fits inside the remaining PostgreSQL lease window plus a safety
    margin. After a remote acknowledgement, the lease is checked in the same transaction
    that marks the batch ACCEPTED. If ownership was lost during the RPC, the acknowledgement
    is intentionally not committed; the next owner replays the same deterministic
    ``batchIndex + batchContentHash``.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
        lease_fence: DurableAppendLeaseFence | None = None,
        rpc_deadline_seconds: float = 30.0,
        rpc_safety_margin_seconds: float = 5.0,
    ) -> None:
        if rpc_deadline_seconds <= 0:
            raise ValueError("rpc_deadline_seconds must be positive")
        if rpc_safety_margin_seconds < 0:
            raise ValueError("rpc_safety_margin_seconds must not be negative")
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()
        self._lease_fence = lease_fence or DurableAppendLeaseFence()
        self._rpc_deadline_seconds = rpc_deadline_seconds
        self._rpc_safety_margin_seconds = rpc_safety_margin_seconds

    def deliver(
        self,
        *,
        token: LeaseToken,
        ingestion_session_id: UUID,
        batches: Sequence[PlannedDeliveryBatch],
        initial_session_status_raw: str | None = None,
    ) -> tuple[DurableBatchDeliveryOutcome, ...]:
        if not batches:
            return ()
        expected_indices = list(range(len(batches)))
        actual_indices = [batch.batch_index for batch in batches]
        if actual_indices != expected_indices:
            raise ValueError(
                f"planned batches must be contiguous from zero; got {actual_indices!r}"
            )

        with self._session_factory() as session:
            with session.begin():
                self._lease_fence.assert_owned(session, token)
                self._repository.bind_session(
                    session,
                    job_id=token.job_id,
                    ingestion_session_id=ingestion_session_id,
                    session_status_raw=initial_session_status_raw,
                )

        outcomes: list[DurableBatchDeliveryOutcome] = []
        for batch in batches:
            with self._session_factory() as session:
                with session.begin():
                    self._lease_fence.assert_owned(session, token)
                    prepared = self._repository.prepare_batch(
                        session,
                        job_id=token.job_id,
                        batch=batch,
                    )

            if prepared.disposition is BatchReplayDisposition.ALREADY_ACCEPTED:
                outcomes.append(
                    DurableBatchDeliveryOutcome(
                        batch_index=batch.batch_index,
                        batch_content_hash=batch.batch_content_hash,
                        disposition=prepared.disposition,
                        remote_result=None,
                    )
                )
                continue

            with self._session_factory() as session:
                with session.begin():
                    self._lease_fence.assert_safe_rpc_window(
                        session,
                        token,
                        rpc_deadline_seconds=self._rpc_deadline_seconds,
                        safety_margin_seconds=self._rpc_safety_margin_seconds,
                    )

            result = self._port.append(batch.command(ingestion_session_id=ingestion_session_id))

            with self._session_factory() as session:
                with session.begin():
                    self._lease_fence.assert_owned(session, token)
                    self._repository.mark_accepted(
                        session,
                        job_id=token.job_id,
                        batch=batch,
                        session_status_raw=result.raw_status,
                    )

            outcomes.append(
                DurableBatchDeliveryOutcome(
                    batch_index=batch.batch_index,
                    batch_content_hash=batch.batch_content_hash,
                    disposition=prepared.disposition,
                    remote_result=result,
                )
            )
        return tuple(outcomes)
