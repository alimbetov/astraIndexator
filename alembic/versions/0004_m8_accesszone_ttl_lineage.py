"""M8.1 durable AccessZone/TTL lineage.

Revision ID: 0004_m8_accesszone_ttl_lineage
Revises: 0003_prepared_artifact_checkpoint
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_m8_accesszone_ttl_lineage"
down_revision = "0003_prepared_artifact_checkpoint"
branch_labels = None
depends_on = None

SCHEMA = "astra_indexator"


def upgrade() -> None:
    # Producer intent is immutable delivery context.  The existing columns remain
    # compatibility columns; these explicit requested_* columns remove the
    # ambiguity between producer input and AstraVector-resolved identity.
    op.add_column(
        "indexation_job",
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "indexation_job",
        sa.Column("requested_access_zone_code", sa.String(length=4), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "requested_access_zone_code_format",
        "indexation_job",
        "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "requested_access_zone_selector_present",
        "indexation_job",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )

    # Backfill the explicit producer-intent fields from the pre-M8 schema.
    op.execute(
        f"""
        UPDATE {SCHEMA}.indexation_job
           SET requested_access_zone_id = access_zone_id,
               requested_access_zone_code = access_zone_code
         WHERE requested_access_zone_id IS NULL
           AND requested_access_zone_code IS NULL
        """
    )

    # M7 replay is independently durable.  Snapshot the normalized delivery
    # context in the checkpoint so replay cannot depend on mutable/default
    # runtime configuration and cannot lose AccessZone/TTL after restart.
    op.add_column(
        "prepared_artifact_checkpoint",
        sa.Column("requested_access_zone_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "prepared_artifact_checkpoint",
        sa.Column("requested_access_zone_code", sa.String(length=4), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "prepared_artifact_checkpoint",
        sa.Column("requested_ttl_days", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        "requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "prepared_artifact_ttl_days_non_negative",
        "prepared_artifact_checkpoint",
        "requested_ttl_days IS NULL OR requested_ttl_days >= 0",
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.prepared_artifact_checkpoint pac
           SET requested_access_zone_id = j.requested_access_zone_id,
               requested_access_zone_code = j.requested_access_zone_code,
               requested_ttl_days = j.requested_ttl_days
          FROM {SCHEMA}.indexation_job j
         WHERE j.id = pac.job_id
        """
    )

    # DeliveryCheckpoint.access_zone_id is the AstraVector-resolved identity.
    # Rename it so future M8 code cannot accidentally treat it as producer input.
    op.alter_column(
        "delivery_checkpoint",
        "access_zone_id",
        new_column_name="resolved_access_zone_id",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.alter_column(
        "delivery_checkpoint",
        "resolved_access_zone_id",
        new_column_name="access_zone_id",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "prepared_artifact_ttl_days_non_negative",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "prepared_artifact_access_zone_code_format",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("prepared_artifact_checkpoint", "requested_ttl_days", schema=SCHEMA)
    op.drop_column("prepared_artifact_checkpoint", "requested_access_zone_code", schema=SCHEMA)
    op.drop_column("prepared_artifact_checkpoint", "requested_access_zone_id", schema=SCHEMA)
    op.drop_constraint(
        "requested_access_zone_selector_present",
        "indexation_job",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "requested_access_zone_code_format",
        "indexation_job",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("indexation_job", "requested_access_zone_code", schema=SCHEMA)
    op.drop_column("indexation_job", "requested_access_zone_id", schema=SCHEMA)
