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
    # Producer intent is immutable delivery context. Existing access_zone_*
    # columns remain compatibility columns for pre-M8 rows only.
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

    # Backfill BEFORE enforcing selector presence. PostgreSQL validates a new
    # CHECK against existing rows immediately, so the inverse order makes an
    # upgrade of any non-empty M1 database fail.
    op.execute(
        f"""
        UPDATE {SCHEMA}.indexation_job
           SET requested_access_zone_id = access_zone_id,
               requested_access_zone_code = access_zone_code
         WHERE requested_access_zone_id IS NULL
           AND requested_access_zone_code IS NULL
        """
    )
    op.create_check_constraint(
        "requested_access_zone_selector_present",
        "indexation_job",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )

    # M8 permits UUID-only producer selectors. The pre-M8 code column therefore
    # cannot remain NOT NULL; requested_* is the authoritative invariant.
    op.alter_column(
        "indexation_job",
        "access_zone_code",
        existing_type=sa.String(length=4),
        nullable=True,
        schema=SCHEMA,
    )

    # M7 replay is independently durable. Snapshot normalized delivery context
    # so restart/replay never depends on runtime defaults or producer resubmit.
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
               requested_ttl_days = COALESCE(j.requested_ttl_days, 0)
          FROM {SCHEMA}.indexation_job j
         WHERE j.id = pac.job_id
        """
    )
    op.create_check_constraint(
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        "requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL",
        schema=SCHEMA,
    )

    # DeliveryCheckpoint stores AstraVector-resolved identity, never producer input.
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
        "prepared_artifact_access_zone_selector_present",
        "prepared_artifact_checkpoint",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "prepared_artifact_ttl_days_non_negative",
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

    # Pre-M8 schema requires access_zone_code. UUID-only M8 rows cannot be
    # represented by that schema, so downgrade is deliberately guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {SCHEMA}.indexation_job WHERE access_zone_code IS NULL
            ) THEN
                RAISE EXCEPTION 'cannot downgrade M8.1: UUID-only AccessZone rows exist';
            END IF;
        END $$
        """
    )
    op.alter_column(
        "indexation_job",
        "access_zone_code",
        existing_type=sa.String(length=4),
        nullable=False,
        schema=SCHEMA,
    )
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
