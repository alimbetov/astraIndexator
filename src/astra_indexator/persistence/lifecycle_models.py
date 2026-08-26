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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

SCHEMA = "astra_indexator"


class DocumentVersionLifecycle(Base):
    __tablename__ = "document_version_lifecycle"
    __table_args__ = (
        CheckConstraint("document_version > 0", name="document_version_positive"),
        CheckConstraint(
            "state IN ("
            "'BUILDING','READY','ACTIVE','SUPERSEDED','CANCEL_PENDING','CANCELLED',"
            "'DELETE_PENDING','DELETED','FAILED'"
            ")",
            name="state_allowed",
        ),
        CheckConstraint(
            "requested_access_zone_code IS NULL OR "
            "requested_access_zone_code ~ '^[0-9]{4}$'",
            name="requested_access_zone_code_format",
        ),
        CheckConstraint(
            "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
            name="requested_access_zone_selector_present",
        ),
        CheckConstraint(
            "requested_ttl_days IS NULL OR requested_ttl_days >= 0",
            name="requested_ttl_days_non_negative",
        ),
        CheckConstraint(
            "(state = 'ACTIVE' AND is_current = true) OR "
            "(state <> 'ACTIVE' AND is_current = false)",
            name="active_matches_current",
        ),
        UniqueConstraint("job_id", name="uq_document_version_lifecycle_job"),
        Index(
            "ix_document_version_lifecycle_document",
            "document_id",
            "document_version",
        ),
        Index(
            "uq_document_version_lifecycle_current_active",
            "document_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE' AND is_current = true"),
        ),
        {"schema": SCHEMA},
    )

    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'BUILDING'"),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    requested_access_zone_code: Mapped[str | None] = mapped_column(String(4))
    requested_access_zone_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    resolved_access_zone_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_ttl_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LifecycleOperation(Base):
    __tablename__ = "lifecycle_operation"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint(
            "document_version IS NULL OR document_version > 0",
            name="document_version_positive",
        ),
        CheckConstraint(
            "operation_type IN ('REINDEX','CANCEL','DELETE','RECONCILE')",
            name="operation_type_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY_WAIT','COMPLETED','FAILED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "requested_access_zone_code IS NULL OR "
            "requested_access_zone_code ~ '^[0-9]{4}$'",
            name="requested_access_zone_code_format",
        ),
        CheckConstraint(
            "reason IS NULL OR length(trim(reason)) > 0",
            name="reason_non_blank",
        ),
        UniqueConstraint(
            "producer_request_id",
            name="uq_lifecycle_operation_producer_request_id",
        ),
        Index(
            "ix_lifecycle_operation_document",
            "document_id",
            "document_version",
            "created_at",
        ),
        Index(
            "ix_lifecycle_operation_retry",
            "next_retry_at",
            postgresql_where=text("status = 'RETRY_WAIT'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    producer_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_version: Mapped[int | None] = mapped_column(BigInteger)
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="SET NULL"),
    )
    requested_access_zone_code: Mapped[str | None] = mapped_column(String(4))
    requested_access_zone_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
