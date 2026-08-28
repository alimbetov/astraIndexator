from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astra_indexator.astravector.batching import PlannedDeliveryBatch
from astra_indexator.astravector.canonical_hash import normalize_sha256_hex
from astra_indexator.astravector.contracts import DocumentVectorStatus

from .models import DeliveryBatch, DeliveryCheckpoint


class DeliveryIntegrityError(RuntimeError):
    """Persisted delivery history conflicts with the reconstructed deterministic batch."""


class DeliverySequenceError(RuntimeError):
    """A batch was staged or acknowledged outside the durable next-batch sequence."""


class BatchReplayDisposition(str, Enum):
    SEND = "SEND"
    REPLAY_PENDING = "REPLAY_PENDING"
    ALREADY_ACCEPTED = "ALREADY_ACCEPTED"


@dataclass(frozen=True, slots=True)
class PreparedBatchState:
    disposition: BatchReplayDisposition
    batch_index: int
    batch_content_hash: str


class DeliveryBatchRepository:
    """Transactional checkpoint/replay semantics for AstraVector Append delivery."""

    def ensure_checkpoint(self, session: Session, job_id: UUID) -> DeliveryCheckpoint:
        stmt = (
            insert(DeliveryCheckpoint)
            .values(job_id=job_id, next_batch_index=0)
            .on_conflict_do_nothing(index_elements=[DeliveryCheckpoint.job_id])
        )
        session.execute(stmt)
        return self._checkpoint_for_update(session, job_id)

    def bind_session(
        self,
        session: Session,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        session_status_raw: str | None = None,
    ) -> DeliveryCheckpoint:
        stmt = (
            insert(DeliveryCheckpoint)
            .values(
                job_id=job_id,
                ingestion_session_id=ingestion_session_id,
                next_batch_index=0,
                session_status_raw=session_status_raw,
            )
            .on_conflict_do_nothing(index_elements=[DeliveryCheckpoint.job_id])
        )
        session.execute(stmt)
        checkpoint = self._checkpoint_for_update(session, job_id)
        if checkpoint.ingestion_session_id is None:
            checkpoint.ingestion_session_id = ingestion_session_id
        elif checkpoint.ingestion_session_id != ingestion_session_id:
            raise DeliveryIntegrityError(
                "job is already bound to a different AstraVector ingestion session"
            )
        if session_status_raw is not None:
            checkpoint.session_status_raw = session_status_raw
        checkpoint.updated_at = datetime.now(timezone.utc)
        session.flush()
        return checkpoint

    def prepare_batch(
        self,
        session: Session,
        *,
        job_id: UUID,
        batch: PlannedDeliveryBatch,
    ) -> PreparedBatchState:
        expected_hash = normalize_sha256_hex(batch.batch_content_hash)
        checkpoint = self._checkpoint_for_update(session, job_id)
        if checkpoint.ingestion_session_id is None:
            raise DeliverySequenceError("delivery checkpoint is not bound to an ingestion session")

        existing = session.execute(
            select(DeliveryBatch)
            .where(
                DeliveryBatch.job_id == job_id,
                DeliveryBatch.batch_index == batch.batch_index,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing is not None:
            self._assert_same_batch(existing, batch, expected_hash)
            if existing.status == "ACCEPTED":
                if checkpoint.next_batch_index < batch.batch_index + 1:
                    raise DeliveryIntegrityError(
                        "accepted DeliveryBatch is ahead of DeliveryCheckpoint.next_batch_index"
                    )
                return PreparedBatchState(
                    disposition=BatchReplayDisposition.ALREADY_ACCEPTED,
                    batch_index=batch.batch_index,
                    batch_content_hash=expected_hash,
                )
            if existing.status != "PREPARED":
                raise DeliveryIntegrityError(
                    f"unsupported persisted batch status {existing.status!r}"
                )
            if checkpoint.next_batch_index != batch.batch_index:
                raise DeliveryIntegrityError(
                    "PREPARED batch index does not equal DeliveryCheckpoint.next_batch_index"
                )
            return PreparedBatchState(
                disposition=BatchReplayDisposition.REPLAY_PENDING,
                batch_index=batch.batch_index,
                batch_content_hash=expected_hash,
            )

        if batch.batch_index != checkpoint.next_batch_index:
            raise DeliverySequenceError(
                f"cannot stage batch {batch.batch_index}; durable next batch is "
                f"{checkpoint.next_batch_index}"
            )

        session.add(
            DeliveryBatch(
                job_id=job_id,
                batch_index=batch.batch_index,
                batch_content_hash=expected_hash,
                block_count=len(batch.blocks),
                serialized_bytes=batch.serialized_bytes,
                status="PREPARED",
            )
        )
        session.flush()
        return PreparedBatchState(
            disposition=BatchReplayDisposition.SEND,
            batch_index=batch.batch_index,
            batch_content_hash=expected_hash,
        )

    def mark_accepted(
        self,
        session: Session,
        *,
        job_id: UUID,
        batch: PlannedDeliveryBatch,
        session_status_raw: str,
    ) -> DeliveryCheckpoint:
        expected_hash = normalize_sha256_hex(batch.batch_content_hash)
        checkpoint = self._checkpoint_for_update(session, job_id)
        persisted = session.execute(
            select(DeliveryBatch)
            .where(
                DeliveryBatch.job_id == job_id,
                DeliveryBatch.batch_index == batch.batch_index,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if persisted is None:
            raise DeliverySequenceError("batch must be PREPARED before it can be acknowledged")
        self._assert_same_batch(persisted, batch, expected_hash)

        if persisted.status == "ACCEPTED":
            if checkpoint.next_batch_index < batch.batch_index + 1:
                raise DeliveryIntegrityError(
                    "accepted batch exists but checkpoint did not advance past it"
                )
            return checkpoint
        if persisted.status != "PREPARED":
            raise DeliveryIntegrityError(f"unsupported persisted batch status {persisted.status!r}")
        if checkpoint.next_batch_index != batch.batch_index:
            raise DeliverySequenceError(
                f"cannot acknowledge batch {batch.batch_index}; durable next batch is "
                f"{checkpoint.next_batch_index}"
            )

        now = datetime.now(timezone.utc)
        persisted.status = "ACCEPTED"
        persisted.accepted_at = now
        checkpoint.last_accepted_batch_index = batch.batch_index
        checkpoint.next_batch_index = batch.batch_index + 1
        checkpoint.session_status_raw = session_status_raw
        checkpoint.last_error_code = None
        checkpoint.last_error_message = None
        checkpoint.updated_at = now
        session.flush()
        return checkpoint

    def record_session_status(
        self,
        session: Session,
        *,
        job_id: UUID,
        ingestion_session_id: UUID,
        session_status_raw: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> DeliveryCheckpoint:
        checkpoint = self._checkpoint_for_update(session, job_id)
        if checkpoint.ingestion_session_id != ingestion_session_id:
            raise DeliveryIntegrityError(
                "reconciliation status belongs to a different AstraVector ingestion session"
            )
        checkpoint.session_status_raw = session_status_raw
        checkpoint.last_error_code = error_code or None
        checkpoint.last_error_message = error_message or None
        checkpoint.updated_at = datetime.now(timezone.utc)
        session.flush()
        return checkpoint

    def record_vector_status(
        self,
        session: Session,
        *,
        job_id: UUID,
        status: DocumentVectorStatus,
    ) -> DeliveryCheckpoint:
        checkpoint = self._checkpoint_for_update(session, job_id)
        checkpoint.vector_state_raw = status.raw_state
        checkpoint.searchable = status.searchable
        checkpoint.expected_bindings = status.expected_bindings
        checkpoint.synced_bindings = status.synced_bindings
        checkpoint.last_reconciled_at = datetime.now(timezone.utc)
        checkpoint.last_error_code = None
        checkpoint.last_error_message = status.message or None
        checkpoint.updated_at = checkpoint.last_reconciled_at
        session.flush()
        return checkpoint

    def checkpoint(self, session: Session, job_id: UUID) -> DeliveryCheckpoint | None:
        return session.get(DeliveryCheckpoint, job_id)

    @staticmethod
    def _assert_same_batch(
        persisted: DeliveryBatch,
        batch: PlannedDeliveryBatch,
        expected_hash: str,
    ) -> None:
        persisted_hash = normalize_sha256_hex(persisted.batch_content_hash)
        if persisted_hash != expected_hash:
            raise DeliveryIntegrityError(
                "same batch_index reconstructed with a different batch_content_hash"
            )
        if persisted.block_count != len(batch.blocks):
            raise DeliveryIntegrityError(
                "same batch_index/hash reconstructed with a different block_count"
            )
        if (
            persisted.serialized_bytes is not None
            and persisted.serialized_bytes != batch.serialized_bytes
        ):
            raise DeliveryIntegrityError(
                "same batch_index/hash reconstructed with different canonical byte length"
            )

    @staticmethod
    def _checkpoint_for_update(session: Session, job_id: UUID) -> DeliveryCheckpoint:
        checkpoint = session.execute(
            select(DeliveryCheckpoint).where(DeliveryCheckpoint.job_id == job_id).with_for_update()
        ).scalar_one_or_none()
        if checkpoint is None:
            raise DeliverySequenceError(f"delivery checkpoint does not exist for job {job_id}")
        return checkpoint
