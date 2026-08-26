"""M9 lifecycle foundation.

Revision ID: 0006_m9_lifecycle_foundation
Revises: 0005_m8_delivery_batch_replay
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_m9_lifecycle_foundation"
down_revision = "0005_m8_delivery_batch_replay"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.create_table(
        "document_version_lifecycle",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.BigInteger(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), server_default=sa.text("'BUILDING'"), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("requested_access_zone_code", sa.String(length=4), nullable=True),
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_ttl_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "document_version > 0",
            name="ck_document_version_lifecycle_document_version_positive",
        ),
        sa.CheckConstraint(
            "state IN ('BUILDING','READY','ACTIVE','SUPERSEDED','CANCEL_PENDING','CANCELLED','DELETE_PENDING','DELETED','FAILED')",
            name="ck_document_version_lifecycle_state_allowed",
        ),
        sa.CheckConstraint(
            "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
            name="ck_document_version_lifecycle_requested_access_zone_code_format",
        ),
        sa.CheckConstraint(
            "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
            name="ck_document_version_lifecycle_requested_access_zone_selector_present",
        ),
        sa.CheckConstraint(
            "requested_ttl_days IS NULL OR requested_ttl_days >= 0",
            name="ck_document_version_lifecycle_requested_ttl_days_non_negative",
        ),
        sa.CheckConstraint(
            "(state = 'ACTIVE' AND is_current = true) OR "
            "(state <> 'ACTIVE' AND is_current = false)",
            name="ck_document_version_lifecycle_active_matches_current",
        ),
        sa.ForeignKeyConstraint(["job_id"], [f"{SCHEMA}.indexation_job.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id", "document_version", name="pk_document_version_lifecycle"),
        sa.UniqueConstraint("job_id", name="uq_document_version_lifecycle_job"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_version_lifecycle_document",
        "document_version_lifecycle",
        ["document_id", "document_version"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_document_version_lifecycle_current_active",
        "document_version_lifecycle",
        ["document_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("state = 'ACTIVE' AND is_current = true"),
    )

    op.create_table(
        "lifecycle_operation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("producer_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.BigInteger(), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_lifecycle_operation_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "document_version IS NULL OR document_version > 0",
            name="ck_lifecycle_operation_document_version_positive",
        ),
        sa.CheckConstraint(
            "operation_type IN ('REINDEX','CANCEL','DELETE','RECONCILE')",
            name="ck_lifecycle_operation_operation_type_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY_WAIT','COMPLETED','FAILED','CANCELLED')",
            name="ck_lifecycle_operation_status_allowed",
        ),
        sa.ForeignKeyConstraint(["job_id"], [f"{SCHEMA}.indexation_job.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_lifecycle_operation"),
        sa.UniqueConstraint("producer_request_id", name="uq_lifecycle_operation_producer_request_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_lifecycle_operation_document",
        "lifecycle_operation",
        ["document_id", "document_version", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_lifecycle_operation_retry",
        "lifecycle_operation",
        ["next_retry_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'RETRY_WAIT'"),
    )


def downgrade() -> None:
    op.drop_index("ix_lifecycle_operation_retry", table_name="lifecycle_operation", schema=SCHEMA)
    op.drop_index("ix_lifecycle_operation_document", table_name="lifecycle_operation", schema=SCHEMA)
    op.drop_table("lifecycle_operation", schema=SCHEMA)
    op.drop_index(
        "uq_document_version_lifecycle_current_active",
        table_name="document_version_lifecycle",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_document_version_lifecycle_document",
        table_name="document_version_lifecycle",
        schema=SCHEMA,
    )
    op.drop_table("document_version_lifecycle", schema=SCHEMA)
