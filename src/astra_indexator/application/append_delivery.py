from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.astravector.batching import PlannedDeliveryBatch
from astra_indexator.astravector.contracts import AppendBlocksResult, AstraVectorIngestionPort
from astra_indexator.persistence.delivery import (
    BatchReplayDisposition,
    DeliveryBatchRepository,
)


@dataclass(frozen=True, slots=True)
class BatchDeliveryOutcome:
    batch_index: int
    batch_content_hash: str
    disposition: BatchReplayDisposition
    remote_result: AppendBlocksResult | None


class AppendDeliveryRunner:
    """Crash-safe Append runner with explicit durable-before-network boundaries.

    A PREPARED row is committed before each network call. The AstraVector acknowledgement is
    committed afterwards in a new transaction. A crash between those transactions therefore
    leaves deterministic replay evidence instead of forcing the caller to guess whether the
    remote side accepted the batch.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        port: AstraVectorIngestionPort,
        *,
        repository: DeliveryBatchRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._port = port
        self._repository = repository or DeliveryBatchRepository()

    def deliver(
        self,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        batches: Sequence[PlannedDeliveryBatch],
        initial_session_status_raw: str | None = None,
    ) -> tuple[BatchDeliveryOutcome, ...]:
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
                self._repository.bind_session(
                    session,
                    job_id=job_id,
                    ingestion_session_id=ingestion_session_id,
                    session_status_raw=initial_session_status_raw,
                )

        outcomes: list[BatchDeliveryOutcome] = []
        for batch in batches:
            with self._session_factory() as session:
                with session.begin():
                    prepared = self._repository.prepare_batch(
                        session,
                        job_id=job_id,
                        batch=batch,
                    )

            if prepared.disposition is BatchReplayDisposition.ALREADY_ACCEPTED:
                outcomes.append(
                    BatchDeliveryOutcome(
                        batch_index=batch.batch_index,
                        batch_content_hash=batch.batch_content_hash,
                        disposition=prepared.disposition,
                        remote_result=None,
                    )
                )
                continue

            result = self._port.append(batch.command(ingestion_session_id=ingestion_session_id))

            with self._session_factory() as session:
                with session.begin():
                    self._repository.mark_accepted(
                        session,
                        job_id=job_id,
                        batch=batch,
                        session_status_raw=result.raw_status,
                    )

            outcomes.append(
                BatchDeliveryOutcome(
                    batch_index=batch.batch_index,
                    batch_content_hash=batch.batch_content_hash,
                    disposition=prepared.disposition,
                    remote_result=result,
                )
            )
        return tuple(outcomes)
