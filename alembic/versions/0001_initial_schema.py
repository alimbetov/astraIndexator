"""M1 persistence foundation.

Revision ID: 0001_initial_schema
Revises: none
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))

    op.create_table(
        "indexation_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("producer_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.BigInteger(), nullable=False),
        sa.Column("external_revision", sa.String(255)),
        sa.Column("knowledge_type", sa.String(32)),
        sa.Column("access_zone_code", sa.String(4), nullable=False),
        sa.Column("access_zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column("requested_ttl_days", sa.Integer()),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_file_name", sa.Text()),
        sa.Column("source_content_hash", sa.String(128)),
        sa.Column("source_size_bytes", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("processing_stage", sa.String(64)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("processing_fingerprint", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("document_version > 0", name="ck_indexation_job_document_version_positive"),
        sa.CheckConstraint("access_zone_code ~ '^[0-9]{4}$'", name="ck_indexation_job_access_zone_code_format"),
        sa.CheckConstraint("requested_ttl_days IS NULL OR requested_ttl_days >= 0", name="ck_indexation_job_requested_ttl_days_non_negative"),
        sa.CheckConstraint("source_size_bytes IS NULL OR source_size_bytes >= 0", name="ck_indexation_job_source_size_bytes_non_negative"),
        sa.CheckConstraint("lease_generation >= 0", name="ck_indexation_job_lease_generation_non_negative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_indexation_job_attempt_count_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_indexation_job_max_attempts_positive"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','RETRY_WAIT','COMPLETED','FAILED','DEAD_LETTER','CANCELLED')", name="ck_indexation_job_status_allowed"),
        sa.UniqueConstraint("producer_request_id", name="uq_indexation_job_producer_request_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_indexation_job_claim", "indexation_job", [sa.text("priority DESC"), "created_at"], schema=SCHEMA, postgresql_where=sa.text("status IN ('PENDING','RETRY_WAIT')"))
    op.create_index("ix_indexation_job_retry", "indexation_job", ["next_retry_at"], schema=SCHEMA, postgresql_where=sa.text("status = 'RETRY_WAIT'"))
    op.create_index("ix_indexation_job_expired_lease", "indexation_job", ["lease_until"], schema=SCHEMA, postgresql_where=sa.text("status = 'PROCESSING'"))
    op.create_index("ix_indexation_job_document", "indexation_job", ["document_id", "document_version"], schema=SCHEMA)
    op.create_index("uq_indexation_job_active_document_version", "indexation_job", ["access_zone_code", "document_id", "document_version"], schema=SCHEMA, unique=True, postgresql_where=sa.text("status IN ('PENDING','PROCESSING','RETRY_WAIT')"))

    op.create_table(
        "processing_attempt",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(32)),
        sa.Column("started_stage", sa.String(64)),
        sa.Column("finished_stage", sa.String(64)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("processing_fingerprint", sa.String(128)),
        sa.CheckConstraint("attempt_number > 0", name="ck_processing_attempt_attempt_number_positive"),
        sa.CheckConstraint("lease_generation > 0", name="ck_processing_attempt_attempt_lease_generation_positive"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_processing_attempt_job_attempt_number"),
        schema=SCHEMA,
    )
    op.create_index("ix_processing_attempt_job", "processing_attempt", ["job_id", "started_at"], schema=SCHEMA)

    op.create_table(
        "delivery_checkpoint",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ingestion_session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("next_batch_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_accepted_batch_index", sa.Integer()),
        sa.Column("final_content_hash", sa.String(128)),
        sa.Column("session_status_raw", sa.String(64)),
        sa.Column("vector_state_raw", sa.String(64)),
        sa.Column("searchable", sa.Boolean()),
        sa.Column("expected_bindings", sa.BigInteger()),
        sa.Column("synced_bindings", sa.BigInteger()),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("next_batch_index >= 0", name="ck_delivery_checkpoint_next_batch_index_non_negative"),
        sa.CheckConstraint("last_accepted_batch_index IS NULL OR last_accepted_batch_index >= 0", name="ck_delivery_checkpoint_last_accepted_batch_index_non_negative"),
        sa.CheckConstraint("expected_bindings IS NULL OR expected_bindings >= 0", name="ck_delivery_checkpoint_expected_bindings_non_negative"),
        sa.CheckConstraint("synced_bindings IS NULL OR synced_bindings >= 0", name="ck_delivery_checkpoint_synced_bindings_non_negative"),
        schema=SCHEMA,
    )

    op.create_table(
        "delivery_batch",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("batch_index", sa.Integer(), primary_key=True),
        sa.Column("batch_content_hash", sa.String(128), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("serialized_bytes", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("batch_index >= 0", name="ck_delivery_batch_batch_index_non_negative"),
        sa.CheckConstraint("block_count >= 0", name="ck_delivery_batch_block_count_non_negative"),
        sa.CheckConstraint("serialized_bytes IS NULL OR serialized_bytes >= 0", name="ck_delivery_batch_serialized_bytes_non_negative"),
        schema=SCHEMA,
    )

    op.create_table(
        "job_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.processing_attempt.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32)),
        sa.Column("processing_stage", sa.String(64)),
        sa.Column("lease_generation", sa.BigInteger()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("lease_generation IS NULL OR lease_generation >= 0", name="ck_job_event_event_lease_generation_non_negative"),
        schema=SCHEMA,
    )
    op.create_index("ix_job_event_job_created", "job_event", ["job_id", "created_at"], schema=SCHEMA)

    op.create_table(
        "knowledge_inventory",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_version", sa.BigInteger(), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_type", sa.String(32)),
        sa.Column("access_zone_code", sa.String(4), nullable=False),
        sa.Column("access_zone_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_file_name", sa.Text()),
        sa.Column("source_content_hash", sa.String(128)),
        sa.Column("processing_fingerprint", sa.String(128)),
        sa.Column("logical_fragment_count", sa.BigInteger()),
        sa.Column("logical_block_count", sa.BigInteger()),
        sa.Column("vector_state", sa.String(64)),
        sa.Column("searchable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expected_bindings", sa.BigInteger()),
        sa.Column("synced_bindings", sa.BigInteger()),
        sa.Column("ttl_state", sa.String(32)),
        sa.Column("effective_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("document_version > 0", name="ck_knowledge_inventory_inventory_document_version_positive"),
        sa.CheckConstraint("access_zone_code ~ '^[0-9]{4}$'", name="ck_knowledge_inventory_inventory_access_zone_code_format"),
        sa.CheckConstraint("logical_fragment_count IS NULL OR logical_fragment_count >= 0", name="ck_knowledge_inventory_logical_fragment_count_non_negative"),
        sa.CheckConstraint("logical_block_count IS NULL OR logical_block_count >= 0", name="ck_knowledge_inventory_logical_block_count_non_negative"),
        schema=SCHEMA,
    )
    op.create_index("ix_knowledge_inventory_zone_searchable", "knowledge_inventory", ["access_zone_code", "searchable"], schema=SCHEMA)
    op.create_index("ix_knowledge_inventory_expiry", "knowledge_inventory", ["effective_expires_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("knowledge_inventory", schema=SCHEMA)
    op.drop_table("job_event", schema=SCHEMA)
    op.drop_table("delivery_batch", schema=SCHEMA)
    op.drop_table("delivery_checkpoint", schema=SCHEMA)
    op.drop_table("processing_attempt", schema=SCHEMA)
    op.drop_table("indexation_job", schema=SCHEMA)
    op.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA}"))
