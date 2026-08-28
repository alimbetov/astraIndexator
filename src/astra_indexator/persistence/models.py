from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

SCHEMA = "astra_indexator"


class IndexationJob(Base):
    __tablename__ = "indexation_job"
    __table_args__ = (
        CheckConstraint("document_version > 0", name="document_version_positive"),
        CheckConstraint("access_zone_code ~ '^[0-9]{4}$'", name="access_zone_code_format"),
        CheckConstraint(
            "requested_ttl_days IS NULL OR requested_ttl_days >= 0",
            name="requested_ttl_days_non_negative",
        ),
        CheckConstraint(
            "source_size_bytes IS NULL OR source_size_bytes >= 0",
            name="source_size_bytes_non_negative",
        ),
        CheckConstraint("lease_generation >= 0", name="lease_generation_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','RETRY_WAIT','COMPLETED','FAILED','DEAD_LETTER','CANCELLED')",
            name="status_allowed",
        ),
        UniqueConstraint("producer_request_id", name="uq_indexation_job_producer_request_id"),
        Index(
            "ix_indexation_job_claim",
            text("priority DESC"),
            "created_at",
            postgresql_where=text("status IN ('PENDING','RETRY_WAIT')"),
        ),
        Index(
            "ix_indexation_job_retry",
            "next_retry_at",
            postgresql_where=text("status = 'RETRY_WAIT'"),
        ),
        Index(
            "ix_indexation_job_expired_lease",
            "lease_until",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
        Index("ix_indexation_job_document", "document_id", "document_version"),
        Index(
            "uq_indexation_job_active_document_version",
            "access_zone_code",
            "document_id",
            "document_version",
            unique=True,
            postgresql_where=text("status IN ('PENDING','PROCESSING','RETRY_WAIT')"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    producer_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_revision: Mapped[str | None] = mapped_column(String(255))

    knowledge_type: Mapped[str | None] = mapped_column(String(32))
    access_zone_code: Mapped[str] = mapped_column(String(4), nullable=False)
    requested_ttl_days: Mapped[int | None] = mapped_column(Integer)

    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_name: Mapped[str | None] = mapped_column(Text)
    source_content_hash: Mapped[str | None] = mapped_column(String(128))
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PENDING'")
    )
    processing_stage: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    processing_fingerprint: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingAttempt(Base):
    __tablename__ = "processing_attempt"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint("lease_generation > 0", name="attempt_lease_generation_positive"),
        UniqueConstraint(
            "job_id", "attempt_number", name="uq_processing_attempt_job_attempt_number"
        ),
        Index("ix_processing_attempt_job", "job_id", "started_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(32))
    started_stage: Mapped[str | None] = mapped_column(String(64))
    finished_stage: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    processing_fingerprint: Mapped[str | None] = mapped_column(String(128))


class DeliveryCheckpoint(Base):
    __tablename__ = "delivery_checkpoint"
    __table_args__ = (
        CheckConstraint("next_batch_index >= 0", name="next_batch_index_non_negative"),
        CheckConstraint(
            "last_accepted_batch_index IS NULL OR last_accepted_batch_index >= 0",
            name="last_accepted_batch_index_non_negative",
        ),
        CheckConstraint(
            "expected_bindings IS NULL OR expected_bindings >= 0",
            name="expected_bindings_non_negative",
        ),
        CheckConstraint(
            "synced_bindings IS NULL OR synced_bindings >= 0", name="synced_bindings_non_negative"
        ),
        CheckConstraint(
            "delivery_compatibility_sha256 IS NULL OR "
            "delivery_compatibility_sha256 ~ '^[0-9a-f]{64}$'",
            name="delivery_compatibility_sha256_format",
        ),
        {"schema": SCHEMA},
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Private AstraVector recovery evidence. Not a producer/domain AccessZone selector.
    resolved_access_zone_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    ingestion_session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    next_batch_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_accepted_batch_index: Mapped[int | None] = mapped_column(Integer)
    final_content_hash: Mapped[str | None] = mapped_column(String(128))
    delivery_compatibility_sha256: Mapped[str | None] = mapped_column(String(64))
    session_status_raw: Mapped[str | None] = mapped_column(String(64))
    vector_state_raw: Mapped[str | None] = mapped_column(String(64))
    searchable: Mapped[bool | None] = mapped_column(Boolean)
    expected_bindings: Mapped[int | None] = mapped_column(BigInteger)
    synced_bindings: Mapped[int | None] = mapped_column(BigInteger)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeliveryBatch(Base):
    __tablename__ = "delivery_batch"
    __table_args__ = (
        CheckConstraint("batch_index >= 0", name="batch_index_non_negative"),
        CheckConstraint("block_count > 0", name="block_count_positive"),
        CheckConstraint(
            "serialized_bytes IS NULL OR serialized_bytes >= 0",
            name="serialized_bytes_non_negative",
        ),
        CheckConstraint("status IN ('PREPARED','ACCEPTED')", name="status_allowed"),
        CheckConstraint(
            "(status = 'PREPARED' AND accepted_at IS NULL) OR "
            "(status = 'ACCEPTED' AND accepted_at IS NOT NULL)",
            name="accepted_at_matches_status",
        ),
        {"schema": SCHEMA},
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        primary_key=True,
    )
    batch_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    serialized_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    __tablename__ = "job_event"
    __table_args__ = (
        CheckConstraint(
            "lease_generation IS NULL OR lease_generation >= 0",
            name="event_lease_generation_non_negative",
        ),
        Index("ix_job_event_job_created", "job_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.processing_attempt.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    processing_stage: Mapped[str | None] = mapped_column(String(64))
    lease_generation: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeInventory(Base):
    __tablename__ = "knowledge_inventory"
    __table_args__ = (
        CheckConstraint("document_version > 0", name="inventory_document_version_positive"),
        CheckConstraint(
            "access_zone_code ~ '^[0-9]{4}$'", name="inventory_access_zone_code_format"
        ),
        CheckConstraint(
            "logical_fragment_count IS NULL OR logical_fragment_count >= 0",
            name="logical_fragment_count_non_negative",
        ),
        CheckConstraint(
            "logical_block_count IS NULL OR logical_block_count >= 0",
            name="logical_block_count_non_negative",
        ),
        Index("ix_knowledge_inventory_zone_searchable", "access_zone_code", "searchable"),
        Index("ix_knowledge_inventory_expiry", "effective_expires_at"),
        {"schema": SCHEMA},
    )

    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    knowledge_type: Mapped[str | None] = mapped_column(String(32))
    access_zone_code: Mapped[str] = mapped_column(String(4), nullable=False)
    source_file_name: Mapped[str | None] = mapped_column(Text)
    source_content_hash: Mapped[str | None] = mapped_column(String(128))
    processing_fingerprint: Mapped[str | None] = mapped_column(String(128))
    logical_fragment_count: Mapped[int | None] = mapped_column(BigInteger)
    logical_block_count: Mapped[int | None] = mapped_column(BigInteger)
    vector_state: Mapped[str | None] = mapped_column(String(64))
    searchable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    expected_bindings: Mapped[int | None] = mapped_column(BigInteger)
    synced_bindings: Mapped[int | None] = mapped_column(BigInteger)
    ttl_state: Mapped[str | None] = mapped_column(String(32))
    effective_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
